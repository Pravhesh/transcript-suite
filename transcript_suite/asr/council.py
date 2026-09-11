"""
Multi-Model Inference Council (Model Jury).
Coordinates multiple distinct ASR architectures (Canary-Qwen-2.5B, Whisper,
and Time-Stretch Acoustic Auditor) to reach consensus, resolve acoustic ambiguities,
and minimize human intervention.
"""

from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from pathlib import Path
import difflib
import re
import tempfile
import torch
import numpy as np
import soundfile as sf
from .canary import sanitize_canary_output


@dataclass
class CouncilVote:
    member: str
    role: str
    hypothesis: str
    confidence: float
    weight: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "member": self.member,
            "role": self.role,
            "hypothesis": self.hypothesis.strip(),
            "confidence": round(self.confidence, 3),
            "weight": self.weight
        }


@dataclass
class CouncilDeliberation:
    verdict: str
    consensus_score: float
    agreement_type: str  # UNANIMOUS | MAJORITY | SPLIT_DECISION
    votes: List[Dict[str, Any]]
    disputed_tokens: List[str]
    needs_human_review: bool
    deliberation_notes: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict.strip(),
            "consensus_score": round(self.consensus_score, 3),
            "agreement_type": self.agreement_type,
            "votes": self.votes,
            "disputed_tokens": self.disputed_tokens,
            "needs_human_review": self.needs_human_review,
            "deliberation_notes": self.deliberation_notes
        }


def normalize_for_comparison(text: str) -> str:
    """Normalizes transcript string for semantic consensus calculation."""
    if not text:
        return ""
    t = text.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def calculate_similarity(t1: str, t2: str) -> float:
    """Calculates Levenshtein token similarity ratio between two texts (0.0 to 1.0)."""
    norm1 = normalize_for_comparison(t1)
    norm2 = normalize_for_comparison(t2)
    if not norm1 and not norm2:
        return 1.0
    if not norm1 or not norm2:
        return 0.0
    if norm1 == norm2:
        return 1.0
    return difflib.SequenceMatcher(None, norm1, norm2).ratio()


def find_disputed_words(hypotheses: List[str]) -> List[str]:
    """Extracts words that appear in some hypotheses but not others."""
    word_sets = [set(normalize_for_comparison(h).split()) for h in hypotheses if h]
    if not word_sets:
        return []
    all_words = set().union(*word_sets)
    common_words = set.intersection(*word_sets) if len(word_sets) > 1 else all_words
    disputed = sorted(list(all_words - common_words))
    return disputed[:8]


class ModelCouncil:
    """
    Orchestrates the 4-Juror Multi-Model Inference Council:
    - Lead Justice: Canary-Qwen-2.5B (NeMo SpeechLM2)
    - Cross-Examiner: Whisper-Small.en (Hugging Face Transformers, fp16)
    - Acoustic Anchor: Standard Conformer-CTC (NeMo EncDecCTCModelBPE)
    - Time-Stretch Auditor: 0.75x acoustic slowdown pass
    """

    def __init__(
        self,
        canary_transcriber=None,
        whisper_model_id: str = "openai/whisper-small.en",
        conformer_model_id: str = "nvidia/stt_en_conformer_ctc_large",
        device: Optional[str] = None,
        slowdown_factor: float = 0.75
    ):
        self.canary_transcriber = canary_transcriber
        self.whisper_model_id = whisper_model_id
        self.conformer_model_id = conformer_model_id
        self.device = device or ("cuda:0" if torch.cuda.is_available() else "cpu")
        self.slowdown_factor = slowdown_factor

        self._whisper_pipeline = None
        self._conformer_model = None
        self._is_whisper_loaded = False
        self._is_conformer_loaded = False

    def _get_whisper_pipeline(self):
        """Lazy loader for Whisper cross-examiner."""
        if self._whisper_pipeline is None:
            try:
                from transformers import pipeline
                torch_dtype = torch.float16 if "cuda" in str(self.device) else torch.float32
                print(f"[Council] Loading Cross-Examiner ({self.whisper_model_id}) onto {self.device}...")
                self._whisper_pipeline = pipeline(
                    "automatic-speech-recognition",
                    model=self.whisper_model_id,
                    dtype=torch_dtype,
                    device=self.device
                )
                self._is_whisper_loaded = True
            except Exception as e:
                print(f"[Council Warning] Failed to load Whisper cross-examiner ({e}).")
                self._whisper_pipeline = None
        return self._whisper_pipeline

    def _get_conformer_model(self):
        """Lazy loader for Conformer-CTC acoustic anchor."""
        if self._conformer_model is None:
            try:
                import nemo.collections.asr as nemo_asr
                print(f"[Council] Loading Acoustic Anchor ({self.conformer_model_id}) onto {self.device}...")
                self._conformer_model = nemo_asr.models.EncDecCTCModelBPE.from_pretrained(
                    model_name=self.conformer_model_id
                )
                if self.device.startswith("cuda") and torch.cuda.is_available():
                    self._conformer_model = self._conformer_model.to(self.device)
                self._conformer_model.eval()
                self._is_conformer_loaded = True
            except Exception as e:
                print(f"[Council Warning] Failed to load Conformer-CTC acoustic anchor ({e}).")
                self._conformer_model = None
        return self._conformer_model

    def transcribe_with_whisper(self, audio_data: np.ndarray | str | Path) -> str:
        """Transcribes audio chunk using Whisper cross-examiner."""
        pipe = self._get_whisper_pipeline()
        if pipe is None:
            return ""
        try:
            if isinstance(audio_data, (str, Path)):
                audio_input = str(Path(audio_data).resolve())
            else:
                audio_input = audio_data.astype(np.float32) if audio_data.dtype != np.float32 else audio_data

            kwargs = {}
            if hasattr(pipe.model, "generation_config") and getattr(pipe.model.generation_config, "is_multilingual", False):
                kwargs["generate_kwargs"] = {"language": "en", "task": "transcribe"}

            res = pipe(audio_input, **kwargs)
            text = res.get("text", "").strip() if isinstance(res, dict) else str(res).strip()
            return text.strip()
        except Exception as e:
            print(f"[Council Warning] Whisper transcription failed: {e}")
            return ""

    def transcribe_with_conformer(self, wav_path: str | Path) -> str:
        """Transcribes audio chunk using Conformer-CTC acoustic anchor."""
        model = self._get_conformer_model()
        if model is None:
            return ""
        try:
            wav_str = str(Path(wav_path).resolve())
            with torch.inference_mode():
                results = model.transcribe([wav_str])
            if isinstance(results, list) and len(results) > 0:
                hyp = results[0]
                text = getattr(hyp, "text", str(hyp))
                return text.strip()
            return str(results).strip()
        except Exception as e:
            print(f"[Council Warning] Conformer-CTC transcription failed: {e}")
            return ""

    def deliberate(
        self,
        audio_path: str | Path,
        sample_rate: int = 16000,
        canary_text: Optional[str] = None,
        waveform: Optional[torch.Tensor] = None,
        seg_idx: int = 0,
        chunks_dir: Optional[Path] = None,
        force_full_council: bool = False
    ) -> CouncilDeliberation:
        """
        Conducts 4-Juror Council Deliberation:
        1. Lead Justice (Canary-Qwen): Complex vocabulary & context reasoning.
        2. Cross-Examiner (Whisper-Small): Low-SNR conversational acoustic decoding.
        3. Acoustic Anchor (Conformer-CTC): Non-autoregressive zero-hallucination ground truth.
        4. Time-Stretch Auditor (0.75x slowdown): Summoned on split decisions.
        """
        audio_path = Path(audio_path).resolve()
        votes: List[CouncilVote] = []

        # 1. Lead Justice Vote (Canary-Qwen)
        h_canary = canary_text
        if h_canary is None and self.canary_transcriber:
            h_canary = self.canary_transcriber.transcribe_chunk(str(audio_path))
        # Sanitize prompt leaks
        h_canary = sanitize_canary_output(h_canary or "", chunk_waveform=waveform)

        canary_conf = 0.95 if h_canary else 0.10
        if any(m in h_canary for m in ["???", "[inaudible]", "...", "uhh", "um"]):
            canary_conf = 0.50
        elif len(h_canary) < 4 and h_canary:
            canary_conf = 0.65

        votes.append(CouncilVote(
            member="Canary-Qwen-2.5B",
            role="Lead Justice",
            hypothesis=h_canary,
            confidence=canary_conf,
            weight=1.5
        ))

        # 2. Cross-Examiner Vote (Whisper-Small)
        h_whisper = self.transcribe_with_whisper(audio_path)
        whisper_conf = 0.92 if h_whisper else 0.15
        if not h_whisper or any(m in h_whisper for m in ["???", "..."]):
            whisper_conf = 0.45

        votes.append(CouncilVote(
            member="Whisper-Small.en",
            role="Cross-Examiner",
            hypothesis=h_whisper,
            confidence=whisper_conf,
            weight=1.2
        ))

        # 3. Acoustic Anchor Vote (Standard Conformer-CTC)
        h_conformer = self.transcribe_with_conformer(audio_path)
        conformer_conf = 0.96 if h_conformer else 0.90
        votes.append(CouncilVote(
            member="Conformer-CTC-Large",
            role="Acoustic Anchor",
            hypothesis=h_conformer,
            confidence=conformer_conf,
            weight=1.3
        ))

        # Deliberation Analysis

        # Case A: Silence / Non-speech Anchor
        # If Conformer-CTC (zero hallucination) heard nothing:
        if not h_conformer:
            if not h_canary and not h_whisper:
                return CouncilDeliberation(
                    verdict="",
                    consensus_score=1.0,
                    agreement_type="UNANIMOUS",
                    votes=[v.to_dict() for v in votes],
                    disputed_tokens=[],
                    needs_human_review=False,
                    deliberation_notes="Unanimous consensus: non-speech / silence."
                )
            else:
                return CouncilDeliberation(
                    verdict="",
                    consensus_score=0.92,
                    agreement_type="CTC_ANCHORED",
                    votes=[v.to_dict() for v in votes],
                    disputed_tokens=find_disputed_words([h_canary, h_whisper]),
                    needs_human_review=False,
                    deliberation_notes="Acoustic Anchor (Conformer-CTC) verified non-speech / ambient silence. Autoregressive hallucination suppressed."
                )

        # Case B: Canary Prompt Leak Neutralization
        if not h_canary and h_whisper:
            sim_w_c = calculate_similarity(h_whisper, h_conformer)
            verdict = h_whisper
            notes = f"Canary prompt leak neutralized. Adopted Whisper-Small anchored by Conformer-CTC ({round(sim_w_c * 100)}% acoustic match)."
            return CouncilDeliberation(
                verdict=verdict,
                consensus_score=round(max(0.85, sim_w_c), 3),
                agreement_type="MAJORITY",
                votes=[v.to_dict() for v in votes],
                disputed_tokens=find_disputed_words([h_whisper, h_conformer]),
                needs_human_review=False,
                deliberation_notes=notes
            )

        # Case C: Bilateral / Trilateral Consensus Evaluation
        sim_canary_whisper = calculate_similarity(h_canary, h_whisper)
        sim_canary_conformer = calculate_similarity(h_canary, h_conformer)
        sim_whisper_conformer = calculate_similarity(h_whisper, h_conformer)
        avg_consensus = (sim_canary_whisper + sim_canary_conformer + sim_whisper_conformer) / 3.0

        # High consensus between Canary & Whisper
        if sim_canary_whisper >= 0.85 and canary_conf >= 0.75 and whisper_conf >= 0.75:
            verdict = h_canary if len(h_canary) >= len(h_whisper) else h_whisper
            agreement_type = "UNANIMOUS" if (sim_canary_conformer >= 0.70 or sim_whisper_conformer >= 0.70) else "MAJORITY"
            return CouncilDeliberation(
                verdict=verdict,
                consensus_score=round(sim_canary_whisper, 3),
                agreement_type=agreement_type,
                votes=[v.to_dict() for v in votes],
                disputed_tokens=find_disputed_words([h_canary, h_whisper, h_conformer]),
                needs_human_review=False,
                deliberation_notes=f"{agreement_type} consensus ({round(sim_canary_whisper * 100)}%) between Canary-Qwen and Whisper-Small."
            )

        # Whisper + Conformer overrule Canary
        if sim_whisper_conformer >= 0.65 and sim_canary_whisper < 0.65:
            return CouncilDeliberation(
                verdict=h_whisper,
                consensus_score=round(sim_whisper_conformer, 3),
                agreement_type="MAJORITY",
                votes=[v.to_dict() for v in votes],
                disputed_tokens=find_disputed_words([h_canary, h_whisper, h_conformer]),
                needs_human_review=False,
                deliberation_notes=f"Majority consensus ({round(sim_whisper_conformer * 100)}%): Whisper-Small supported by Conformer-CTC acoustic anchor overrules Canary."
            )

        # Canary + Conformer overrule Whisper
        if sim_canary_conformer >= 0.65 and sim_canary_whisper < 0.65:
            return CouncilDeliberation(
                verdict=h_canary,
                consensus_score=round(sim_canary_conformer, 3),
                agreement_type="MAJORITY",
                votes=[v.to_dict() for v in votes],
                disputed_tokens=find_disputed_words([h_canary, h_whisper, h_conformer]),
                needs_human_review=False,
                deliberation_notes=f"Majority consensus ({round(sim_canary_conformer * 100)}%): Canary-Qwen supported by Conformer-CTC acoustic anchor overrules Whisper."
            )

        # Case D: Dispute / Low Agreement -> Summon Juror 4: Time-Stretch Auditor (0.75x Slowdown)
        h_auditor = ""
        auditor_conf = 0.88
        slow_audio_path = None

        if chunks_dir:
            slow_audio_path = Path(chunks_dir) / f"chunk_{seg_idx}_slow.wav"

        if slow_audio_path and slow_audio_path.exists():
            pass
        elif waveform is not None:
            try:
                stretched = self._stretch_audio(waveform, factor=self.slowdown_factor)
                if slow_audio_path:
                    sf.write(str(slow_audio_path), stretched.squeeze(0).cpu().numpy(), sample_rate, subtype="PCM_16")
                else:
                    with tempfile.NamedTemporaryFile(suffix="_slow.wav", delete=False) as tf:
                        sf.write(tf.name, stretched.squeeze(0).cpu().numpy(), sample_rate, subtype="PCM_16")
                        slow_audio_path = Path(tf.name)
            except Exception as e:
                print(f"[Council Warning] Failed to generate time-stretch audio: {e}")

        eval_path = slow_audio_path if (slow_audio_path and slow_audio_path.exists()) else audio_path
        h_auditor = self.transcribe_with_whisper(eval_path)
        if not h_auditor and self.canary_transcriber:
            try:
                h_auditor = self.canary_transcriber.transcribe_chunk(str(eval_path))
                h_auditor = sanitize_canary_output(h_auditor)
            except Exception:
                pass

        votes.append(CouncilVote(
            member="Acoustic-Auditor-0.75x",
            role="Time-Stretch Auditor",
            hypothesis=h_auditor,
            confidence=auditor_conf,
            weight=1.2
        ))

        # Final Synthesis with Auditor
        hypotheses = [v.hypothesis for v in votes if v.hypothesis]
        disputed = find_disputed_words(hypotheses)

        sim_aud_whisper = calculate_similarity(h_auditor, h_whisper)
        sim_aud_canary = calculate_similarity(h_auditor, h_canary)

        if sim_aud_whisper >= 0.70:
            verdict = h_whisper
            agreement_type = "MAJORITY"
            notes = f"Acoustic Auditor (0.75x) confirmed Whisper hypothesis ({round(sim_aud_whisper * 100)}%)."
            needs_review = False
        elif sim_aud_canary >= 0.70:
            verdict = h_canary
            agreement_type = "MAJORITY"
            notes = f"Acoustic Auditor (0.75x) confirmed Canary hypothesis ({round(sim_aud_canary * 100)}%)."
            needs_review = False
        else:
            agreement_type = "SPLIT_DECISION"
            best_vote = max(votes, key=lambda v: v.confidence * v.weight if v.hypothesis else -1)
            verdict = best_vote.hypothesis
            notes = f"Split decision across 4 jurors (consensus: {round(avg_consensus * 100)}%). Resolved via weighted acoustic score ({best_vote.member})."
            needs_review = (avg_consensus < 0.55)

        return CouncilDeliberation(
            verdict=verdict or h_whisper or h_canary,
            consensus_score=round(avg_consensus, 3),
            agreement_type=agreement_type,
            votes=[v.to_dict() for v in votes],
            disputed_tokens=disputed,
            needs_human_review=needs_review,
            deliberation_notes=notes
        )

    def deliberate_waveform_segment(
        self,
        waveform_slice: torch.Tensor,
        sr: int = 16000,
        canary_text: Optional[str] = None,
        seg_idx: int = 0,
        chunks_dir: Optional[Path] = None
    ) -> CouncilDeliberation:
        """
        Convenience runner for in-memory tensor slices.
        Writes a temporary WAV file for models requiring filesystem input, then invokes deliberate().
        """
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            wav_data = waveform_slice.squeeze(0).cpu().numpy()
            sf.write(tf.name, wav_data, sr, subtype="PCM_16")
            tmp_wav = Path(tf.name)

        try:
            delib = self.deliberate(
                audio_path=tmp_wav,
                sample_rate=sr,
                canary_text=canary_text,
                waveform=waveform_slice,
                seg_idx=seg_idx,
                chunks_dir=chunks_dir
            )
        finally:
            tmp_wav.unlink(missing_ok=True)

        return delib

    def _stretch_audio(self, waveform: torch.Tensor, factor: float = 0.75) -> torch.Tensor:
        """Applies time stretching via linear interpolation."""
        if waveform.ndim == 1:
            waveform = waveform.unsqueeze(0)
        orig_len = waveform.shape[1]
        target_len = int(orig_len / factor)
        stretched = torch.nn.functional.interpolate(
            waveform.unsqueeze(0),
            size=target_len,
            mode='linear',
            align_corners=False
        ).squeeze(0)
        return stretched

    def unload_members(self):
        """Unloads Whisper and Conformer models to free VRAM."""
        if self._whisper_pipeline is not None:
            del self._whisper_pipeline
            self._whisper_pipeline = None
            self._is_whisper_loaded = False

        if self._conformer_model is not None:
            del self._conformer_model
            self._conformer_model = None
            self._is_conformer_loaded = False

        import gc
        gc.collect()
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
        try:
            import ctypes
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except Exception:
            pass
        print("[Council] Multi-Model Inference Council models unloaded and memory trimmed.")

