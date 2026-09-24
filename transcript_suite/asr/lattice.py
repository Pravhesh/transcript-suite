"""
Unified Acoustic & Token Lattice (Confusion Network) for ASR Council Consensus.
Constructs a word-level confusion network aligning hypotheses from Canary-Qwen, Whisper,
Conformer-CTC, and Parakeet-TDT into consensus columns (MSTA alignment).
Provides token-level weighted voting, phonetic homophone pooling, and acoustic anchor vetoes.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
import re
import difflib
from .phonetics import double_metaphone, are_homophones


@dataclass
class LatticeToken:
    """A single token proposed by a juror model in a lattice column."""
    model: str
    surface_text: str  # Original case & punctuation, e.g. "Jersey,"
    clean_text: str    # Normalized lowercase alphanumeric, e.g. "jersey"
    confidence: float
    weight: float
    is_epsilon: bool = False
    phonetic_codes: Tuple[str, str] = ("", "")


def clean_word(w: str) -> str:
    """Strips non-alphanumeric punctuation and lowercases for alignment."""
    return re.sub(r"[^\w]", "", w).lower()


def token_alignment_distance(t1: LatticeToken, t2: LatticeToken) -> float:
    """
    Computes alignment distance between two tokens (0.0 = identical, 1.0 = completely distinct).
    """
    if t1.is_epsilon and t2.is_epsilon:
        return 0.0
    if t1.is_epsilon or t2.is_epsilon:
        return 0.60  # Gap penalty

    if t1.clean_text == t2.clean_text:
        return 0.0

    if are_homophones(t1.clean_text, t2.clean_text):
        return 0.15  # Very strong match for acoustic sound-alikes (there/their, Diane/Diana)

    # Character similarity for minor typos or morphological variations (run/running)
    ratio = difflib.SequenceMatcher(None, t1.clean_text, t2.clean_text).ratio()
    if ratio >= 0.70:
        return 1.0 - ratio

    return 1.0


@dataclass
class ConfusionBin:
    """A single column in the Confusion Network containing aligned tokens across jurors."""
    tokens: List[LatticeToken] = field(default_factory=list)

    def representative_clean(self) -> str:
        """Returns the first non-epsilon clean token in this bin for alignment matching."""
        for t in self.tokens:
            if not t.is_epsilon and t.clean_text:
                return t.clean_text
        return ""

    def adjudicate(self) -> Dict[str, Any]:
        """
        Adjudicates weighted consensus voting for this confusion bin:
        - Pools phonetic homophones together acoustically.
        - Applies Conformer-CTC acoustic anchor bonus and hallucination vetoes.
        - Selects the optimal surface casing and punctuation from high-level LM jurors.
        """
        non_eps = [t for t in self.tokens if not t.is_epsilon and t.clean_text]
        if not non_eps:
            return {
                "winner_surface": "",
                "winner_clean": "",
                "is_epsilon": True,
                "consensus_score": 1.0,
                "is_disputed": False,
                "dispute_note": "",
                "is_ctc_anchor_veto": False,
                "is_homophone_resolved": False,
            }

        # Identify CTC acoustic anchor vote
        ctc_token = next(
            (t for t in self.tokens if "conformer" in t.model.lower() or "ctc" in t.model.lower()),
            None
        )
        ctc_voted_eps = ctc_token is not None and ctc_token.is_epsilon

        total_weight = sum(t.weight * t.confidence for t in self.tokens)

        # 1. Group candidates by clean text with phonetic homophone pooling
        candidate_scores: Dict[str, float] = {}
        candidate_surfaces: Dict[str, List[LatticeToken]] = {}
        homophone_mapped = False

        for t in non_eps:
            clean = t.clean_text
            assigned_root = clean
            for root in candidate_scores.keys():
                if are_homophones(clean, root):
                    assigned_root = root
                    homophone_mapped = True
                    break

            score = t.weight * t.confidence
            # Acoustic anchor bonus: CTC reflects ground-truth acoustic frames without LM hallucination
            if "conformer" in t.model.lower() or "ctc" in t.model.lower():
                score += 0.25

            candidate_scores[assigned_root] = candidate_scores.get(assigned_root, 0.0) + score
            if assigned_root not in candidate_surfaces:
                candidate_surfaces[assigned_root] = []
            candidate_surfaces[assigned_root].append(t)

        best_root = max(candidate_scores.items(), key=lambda x: x[1])[0]
        best_cand_score = candidate_scores[best_root]
        surfaces = candidate_surfaces[best_root]

        # 2. Hallucination Suppression:
        # If CTC explicitly voted EPSILON (no speech acoustic frames),
        # and only ONE model proposed a word, and epsilon weight exceeds candidate score, CTC vetoes it.
        eps_weight = sum(t.weight * t.confidence for t in self.tokens if t.is_epsilon)
        if ctc_voted_eps and len(surfaces) == 1 and eps_weight > best_cand_score:
            return {
                "winner_surface": "",
                "winner_clean": "",
                "is_epsilon": True,
                "consensus_score": round(eps_weight / max(0.01, total_weight), 3),
                "is_disputed": True,
                "dispute_note": f"CTC acoustic anchor vetoed phantom word '{best_root}'",
                "is_ctc_anchor_veto": True,
                "is_homophone_resolved": False,
            }

        # 3. Surface selection (casing and punctuation inheritance)
        def surface_rank(t: LatticeToken) -> int:
            rank = 0
            m_lower = t.model.lower()
            if "canary" in m_lower:
                rank += 30
            elif "whisper" in m_lower:
                rank += 20
            elif "parakeet" in m_lower:
                rank += 10

            # Casing priority
            if t.surface_text and t.surface_text[0].isupper():
                rank += 5
            # Punctuation priority
            if any(p in t.surface_text for p in ",.?!:;"):
                rank += 3
            return rank

        best_surface_tok = max(surfaces, key=surface_rank)

        # If homophones pooled (e.g. there vs their), prefer the spelling from Canary or Whisper LM
        if homophone_mapped and len(set(t.clean_text for t in surfaces)) > 1:
            lm_tok = next((t for t in surfaces if "canary" in t.model.lower() or "whisper" in t.model.lower()), None)
            if lm_tok:
                best_surface_tok = lm_tok

        consensus_score = min(1.0, round(best_cand_score / max(0.01, total_weight), 3))

        # Check if disputed
        unique_cleans = set(t.clean_text for t in non_eps)
        is_disputed = (len(unique_cleans) > 1 and not homophone_mapped) or any(t.is_epsilon for t in self.tokens)

        dispute_note = ""
        if is_disputed:
            parts = [f"{t.model.split('-')[0]}: '{t.surface_text}'" for t in self.tokens if not t.is_epsilon]
            dispute_note = " vs ".join(parts)

        return {
            "winner_surface": best_surface_tok.surface_text,
            "winner_clean": best_root,
            "is_epsilon": False,
            "consensus_score": consensus_score,
            "is_disputed": is_disputed,
            "dispute_note": dispute_note,
            "is_homophone_resolved": homophone_mapped,
            "is_ctc_anchor_veto": False,
        }


class TokenLattice:
    """
    Multi-Sequence Progressive Alignment Lattice (Confusion Network).
    Aligns hypotheses token-by-token and arbitrates consensus across all models.
    """

    def __init__(self):
        self.bins: List[ConfusionBin] = []
        self.submitted_models: List[Dict[str, Any]] = []

    def add_hypothesis(self, model_name: str, text: str, confidence: float, weight: float):
        """
        Aligns a new model hypothesis into the confusion network using Needleman-Wunsch DP.
        """
        self.submitted_models.append({
            "model": model_name,
            "confidence": confidence,
            "weight": weight
        })

        raw_words = text.strip().split() if text else []
        tokens = [
            LatticeToken(
                model=model_name,
                surface_text=w,
                clean_text=clean_word(w),
                confidence=confidence,
                weight=weight,
                is_epsilon=False,
                phonetic_codes=double_metaphone(clean_word(w)),
            )
            for w in raw_words
            if clean_word(w)
        ]

        # Case 1: First non-empty hypothesis initializes the lattice
        if not self.bins:
            if not tokens:
                return  # Still empty, will be initialized by next non-empty hypothesis
            for tok in tokens:
                cb = ConfusionBin()
                # Backfill epsilon for any prior models that had empty text
                for prev in self.submitted_models[:-1]:
                    cb.tokens.append(LatticeToken(
                        model=prev["model"],
                        surface_text="",
                        clean_text="",
                        confidence=prev["confidence"],
                        weight=prev["weight"],
                        is_epsilon=True,
                    ))
                cb.tokens.append(tok)
                self.bins.append(cb)
            return

        # Case 2: Empty hypothesis added to existing lattice
        if not tokens:
            for b in self.bins:
                b.tokens.append(LatticeToken(
                    model=model_name,
                    surface_text="",
                    clean_text="",
                    confidence=confidence,
                    weight=weight,
                    is_epsilon=True,
                ))
            return

        # Case 3: Needleman-Wunsch Global Alignment against existing lattice bins
        N = len(self.bins)
        M = len(tokens)

        dp = [[0.0] * (M + 1) for _ in range(N + 1)]
        trace = [[(0, 0)] * (M + 1) for _ in range(N + 1)]

        gap_penalty = 0.60

        for i in range(1, N + 1):
            dp[i][0] = dp[i - 1][0] + gap_penalty
            trace[i][0] = (i - 1, 0)

        for j in range(1, M + 1):
            dp[0][j] = dp[0][j - 1] + gap_penalty
            trace[0][j] = (0, j - 1)

        for i in range(1, N + 1):
            bin_clean = self.bins[i - 1].representative_clean()
            dummy_bin_tok = LatticeToken(
                model="bin",
                surface_text=bin_clean,
                clean_text=bin_clean,
                confidence=1.0,
                weight=1.0,
                phonetic_codes=double_metaphone(bin_clean),
            )
            for j in range(1, M + 1):
                cost_match = token_alignment_distance(dummy_bin_tok, tokens[j - 1])

                sub_cost = dp[i - 1][j - 1] + cost_match
                del_cost = dp[i - 1][j] + gap_penalty
                ins_cost = dp[i][j - 1] + gap_penalty

                best_cost = min(sub_cost, del_cost, ins_cost)
                dp[i][j] = best_cost

                if best_cost == sub_cost:
                    trace[i][j] = (i - 1, j - 1)
                elif best_cost == del_cost:
                    trace[i][j] = (i - 1, j)
                else:
                    trace[i][j] = (i, j - 1)

        # Backtrack
        curr_i = N
        curr_j = M
        alignment: List[Tuple[Optional[int], Optional[LatticeToken]]] = []
        while curr_i > 0 or curr_j > 0:
            pi, pj = trace[curr_i][curr_j]
            if pi == curr_i - 1 and pj == curr_j - 1:
                alignment.append((curr_i - 1, tokens[curr_j - 1]))
            elif pi == curr_i - 1 and pj == curr_j:
                alignment.append((curr_i - 1, None))
            else:
                alignment.append((None, tokens[curr_j - 1]))
            curr_i, curr_j = pi, pj

        alignment.reverse()

        # Merge alignment into new bins
        new_bins: List[ConfusionBin] = []
        prior_models = [m for m in self.submitted_models if m["model"] != model_name]

        for bin_idx, new_tok in alignment:
            if bin_idx is not None:
                existing_bin = self.bins[bin_idx]
                if new_tok is not None:
                    existing_bin.tokens.append(new_tok)
                else:
                    existing_bin.tokens.append(LatticeToken(
                        model=model_name,
                        surface_text="",
                        clean_text="",
                        confidence=confidence,
                        weight=weight,
                        is_epsilon=True,
                    ))
                new_bins.append(existing_bin)
            else:
                # Insertion: Create new bin
                new_bin = ConfusionBin()
                for pm in prior_models:
                    new_bin.tokens.append(LatticeToken(
                        model=pm["model"],
                        surface_text="",
                        clean_text="",
                        confidence=pm["confidence"],
                        weight=pm["weight"],
                        is_epsilon=True,
                    ))
                if new_tok is not None:
                    new_bin.tokens.append(new_tok)
                new_bins.append(new_bin)

        self.bins = new_bins

    def synthesize(self) -> Dict[str, Any]:
        """
        Synthesizes the optimal consensus word sequence across all lattice bins.
        """
        if not self.bins:
            return {
                "verdict": "",
                "consensus_score": 1.0,
                "disputed_tokens": [],
                "ctc_vetoes": 0,
                "homophone_resolutions": 0,
                "bin_count": 0,
                "bin_results": [],
            }

        results = [b.adjudicate() for b in self.bins]

        verdict_words = []
        total_score = 0.0
        disputed_items = []
        ctc_vetoes = 0
        homophone_resolutions = 0

        for res in results:
            total_score += res["consensus_score"]
            if not res["is_epsilon"] and res["winner_surface"]:
                verdict_words.append(res["winner_surface"])
            if res["is_disputed"] and res["dispute_note"]:
                disputed_items.append(res["dispute_note"])
            if res.get("is_ctc_anchor_veto"):
                ctc_vetoes += 1
            if res.get("is_homophone_resolved"):
                homophone_resolutions += 1

        avg_score = round(total_score / max(1, len(results)), 3)
        verdict = " ".join(verdict_words)

        return {
            "verdict": verdict,
            "consensus_score": avg_score,
            "disputed_tokens": disputed_items[:10],
            "ctc_vetoes": ctc_vetoes,
            "homophone_resolutions": homophone_resolutions,
            "bin_count": len(self.bins),
            "bin_results": results,
        }
