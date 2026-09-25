"""
Multi-Model Inference Council (Supreme Model Jury).
Coordinates multiple distinct ASR architectures (Canary-Qwen-2.5B, Whisper Large/Small,
Parakeet-TDT Transducer, Conformer-CTC, and Time-Stretch Acoustic Auditor) to reach consensus,
resolve acoustic ambiguities, and eliminate hallucinations.
"""

from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional, Callable, Tuple
from pathlib import Path
import difflib
import re
import math
import tempfile
import torch
import numpy as np
import soundfile as sf
from .canary import sanitize_canary_output
from .lattice import TokenLattice
from .phonetics import are_homophones, double_metaphone
from ..config import config
from ..audio.loader import estimate_snr_db


@dataclass
class CouncilVote:
    member: str
    role: str
    hypothesis: str
    confidence: float
    weight: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["hypothesis"] = self.hypothesis.strip()
        d["confidence"] = round(self.confidence, 3)
        return d


@dataclass
class CouncilDeliberation:
    verdict: str
    consensus_score: float
    agreement_type: str  # UNANIMOUS | MAJORITY | CTC_ANCHORED | SPLIT_DECISION
    votes: List[Dict[str, Any]]
    disputed_tokens: List[str]
    needs_human_review: bool
    deliberation_notes: str
    lattice_bins: Optional[List[Dict[str, Any]]] = None
    homophone_resolutions: int = 0
    ctc_vetoes: int = 0
    loop_circuit_breaker_tripped: bool = False
    confidence_decomposition: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["verdict"] = self.verdict.strip()
        d["consensus_score"] = round(self.consensus_score, 3)
        d["loop_circuit_breaker_tripped"] = self.loop_circuit_breaker_tripped
        if self.confidence_decomposition is not None:
            d["confidence_decomposition"] = self.confidence_decomposition
        return d


def detect_autoregressive_loop(text: str) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Autoregressive Loop Circuit-Breaker (Feature 2.2.C).
    Monitors repetition entropy and detects degenerate n-gram and phrase loops:
    1. Consecutive repeating n-grams (1-gram >= 4x, 2-gram >= 3x, 3-gram >= 3x, 4-gram >= 2x).
    2. Shannon token entropy H < 1.80 and low vocabulary diversity D < 0.40 on >=8 words.
    3. Whole phrase tail cycles (repeating clauses >=3 words).
    Returns (is_loop, reason, metrics).
    """
    if not text:
        return False, "", {"word_count": 0, "diversity": 1.0, "entropy": 0.0}

    raw_words = re.findall(r"\b\w+\b", text.lower())
    total_words = len(raw_words)
    if total_words < 3:
        return False, "", {"word_count": total_words, "diversity": 1.0, "entropy": 0.0}

    counts: Dict[str, int] = {}
    for w in raw_words:
        counts[w] = counts.get(w, 0) + 1

    unique_words = len(counts)
    diversity = unique_words / total_words

    # Shannon entropy H = -sum(p * log2(p))
    entropy = 0.0
    for count in counts.values():
        p = count / total_words
        entropy -= p * math.log2(p)

    metrics = {
        "word_count": total_words,
        "unique_words": unique_words,
        "diversity": round(diversity, 3),
        "entropy": round(entropy, 3)
    }

    # 1. Consecutive N-gram repeats check (n = 1, 2, 3, 4)
    # n=1 (single word repeated >= 4 consecutive times, e.g. 'yes yes yes yes')
    for i in range(len(raw_words) - 3):
        w = raw_words[i]
        if raw_words[i + 1] == w and raw_words[i + 2] == w and raw_words[i + 3] == w:
            metrics["ngram"] = 1
            metrics["pattern"] = w
            return True, f"1-gram loop: '{w}' repeated 4+ times consecutively", metrics

    # n=2 (2-word bigram repeated >= 3 consecutive times, e.g., 'you know you know you know')
    if total_words >= 6:
        for i in range(len(raw_words) - 5):
            bg1 = (raw_words[i], raw_words[i + 1])
            bg2 = (raw_words[i + 2], raw_words[i + 3])
            bg3 = (raw_words[i + 4], raw_words[i + 5])
            if bg1 == bg2 == bg3:
                pat = " ".join(bg1)
                metrics["ngram"] = 2
                metrics["pattern"] = pat
                return True, f"2-gram loop: '{pat}' repeated 3+ times consecutively", metrics

    # n=3 (3-word trigram repeated >= 3 consecutive times)
    if total_words >= 9:
        for i in range(len(raw_words) - 8):
            tg1 = (raw_words[i], raw_words[i + 1], raw_words[i + 2])
            tg2 = (raw_words[i + 3], raw_words[i + 4], raw_words[i + 5])
            tg3 = (raw_words[i + 6], raw_words[i + 7], raw_words[i + 8])
            if tg1 == tg2 == tg3:
                pat = " ".join(tg1)
                metrics["ngram"] = 3
                metrics["pattern"] = pat
                return True, f"3-gram loop: '{pat}' repeated 3+ times consecutively", metrics

    # n=4 (4-word 4-gram repeated >= 2 consecutive times)
    if total_words >= 8:
        for i in range(len(raw_words) - 7):
            fg1 = tuple(raw_words[i : i + 4])
            fg2 = tuple(raw_words[i + 4 : i + 8])
            if fg1 == fg2:
                pat = " ".join(fg1)
                metrics["ngram"] = 4
                metrics["pattern"] = pat
                return True, f"4-gram loop: '{pat}' repeated 2+ times consecutively", metrics

    # 2. Tail phrase cycle detection (repeating phrase of length k in [3..12])
    for k in range(3, min(15, total_words // 2 + 1)):
        tail1 = raw_words[-k:]
        tail2 = raw_words[-2 * k : -k]
        if tail1 == tail2:
            pat = " ".join(tail1)
            metrics["cycle_len"] = k
            metrics["pattern"] = pat
            return True, f"Tail phrase cycle: '{pat}' repeated", metrics

    # 3. Vocabulary diversity & Shannon Entropy
    if total_words >= 10 and diversity < 0.40:
        return True, f"Low vocabulary diversity ({diversity:.2f} < 0.40) across {total_words} words", metrics

    if total_words >= 8 and entropy < 1.80:
        return True, f"Low Shannon repetition entropy ({entropy:.2f} < 1.80)", metrics

    return False, "", metrics


def compute_orthogonal_confidence(
    waveform: Optional[torch.Tensor] = None,
    sr: int = 16000,
    conformer_conf: float = 0.90,
    lattice_score: float = 0.85,
    canary_text: str = "",
    whisper_text: str = "",
    disputed_tokens: Optional[List[str]] = None,
    circuit_breaker_tripped: bool = False
) -> Dict[str, Any]:
    """
    Orthogonal Confidence Decomposition (Feature 2.3.A).
    Decomposes scalar confidence into two independent axes:
    1. Physical Audio Clarity (C_acoustic): SNR in dB, digital clipping %, CTC anchor clarity.
    2. Linguistic Model Consensus (C_semantic): Lattice consensus score, pairwise similarity, undisputed token ratio.
    Maps to 4-quadrant taxonomy:
    - HIGH_ACOUSTIC_HIGH_SEMANTIC: Pristine audio, full consensus.
    - HIGH_ACOUSTIC_LOW_SEMANTIC: Clear microphone, but linguistic dispute (jargon/proper nouns).
    - LOW_ACOUSTIC_HIGH_SEMANTIC: Noisy/low SNR audio, but models unanimously agreed.
    - LOW_ACOUSTIC_LOW_SEMANTIC: Degraded audio with high model ambiguity.
    """
    disputed = disputed_tokens or []

    # 1. Acoustic Quality Axis (C_acoustic)
    if waveform is not None and waveform.numel() > 0:
        snr_db = estimate_snr_db(waveform, sr)
        clipped_count = (torch.abs(waveform) >= 0.995).sum().item()
        clipping_pct = (clipped_count / max(1, waveform.numel())) * 100.0
        s_clip = max(0.0, 1.0 - (clipping_pct / 5.0))
    else:
        snr_db = 28.0  # Clean baseline when waveform not supplied
        clipping_pct = 0.0
        s_clip = 1.0

    # Normalize SNR: 5 dB -> 0.0, 30 dB+ -> 1.0
    s_snr = max(0.0, min(1.0, (snr_db - 5.0) / 25.0))
    s_ctc = max(0.0, min(1.0, conformer_conf))

    # Composite C_acoustic
    c_acoustic = 0.50 * s_snr + 0.20 * s_clip + 0.30 * s_ctc
    c_acoustic = max(0.0, min(1.0, c_acoustic))

    if c_acoustic >= 0.85:
        acoustic_grade = "PRISTINE"
    elif c_acoustic >= 0.70:
        acoustic_grade = "CLEAR"
    elif c_acoustic >= 0.50:
        acoustic_grade = "MODERATE_NOISE"
    else:
        acoustic_grade = "POOR_SNR"

    # 2. Semantic Consensus Axis (C_semantic)
    s_lattice = max(0.0, min(1.0, lattice_score))
    s_sim = calculate_similarity(canary_text, whisper_text)

    all_words = len(normalize_for_comparison(f"{canary_text} {whisper_text}").split())
    disp_count = len(disputed)
    s_undisputed = max(0.0, 1.0 - (disp_count / max(1, (all_words // 2) + 1)))

    if circuit_breaker_tripped:
        c_semantic = min(0.55, 0.50 * s_lattice + 0.50 * s_ctc)
    else:
        c_semantic = 0.50 * s_lattice + 0.30 * s_sim + 0.20 * s_undisputed
    c_semantic = max(0.0, min(1.0, c_semantic))

    if c_semantic >= 0.88:
        semantic_grade = "UNANIMOUS"
    elif c_semantic >= 0.72:
        semantic_grade = "STRONG_CONSENSUS"
    elif c_semantic >= 0.52:
        semantic_grade = "DISPUTED_TERMS"
    else:
        semantic_grade = "SPLIT_DECISION"

    # 3. Quadrant Mapping (threshold = 0.65)
    is_high_acoustic = c_acoustic >= 0.65
    is_high_semantic = c_semantic >= 0.65

    if is_high_acoustic and is_high_semantic:
        quadrant = "HIGH_ACOUSTIC_HIGH_SEMANTIC"
        quadrant_label = "Pristine audio with unanimous consensus"
    elif is_high_acoustic and not is_high_semantic:
        quadrant = "HIGH_ACOUSTIC_LOW_SEMANTIC"
        quadrant_label = "Clear microphone audio, but linguistic/spelling disagreement"
    elif not is_high_acoustic and is_high_semantic:
        quadrant = "LOW_ACOUSTIC_HIGH_SEMANTIC"
        quadrant_label = "Noisy/low SNR audio, but council unanimously resolved speech"
    else:
        quadrant = "LOW_ACOUSTIC_LOW_SEMANTIC"
        quadrant_label = "Degraded audio quality with high model dispute"

    return {
        "acoustic_score": round(c_acoustic, 3),
        "acoustic_grade": acoustic_grade,
        "snr_db": round(snr_db, 1),
        "clipping_pct": round(clipping_pct, 2),
        "semantic_score": round(c_semantic, 3),
        "semantic_grade": semantic_grade,
        "quadrant": quadrant,
        "quadrant_label": quadrant_label
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
    Orchestrates the Tri-Architecture Supreme Model Council:
    - Lead Justice: Canary-Qwen-2.5B (NeMo SpeechLM2)
    - Cross-Examiner: Whisper Large-v3 / Small (Hugging Face Transformers, fp16)
    - Transducer Cross-Examiner: Parakeet-TDT-1.1B (NeMo FastConformer TDT)
    - Acoustic Anchor: Standard Conformer-CTC (NeMo EncDecCTCModelBPE)
    - Time-Stretch Auditor: 0.75x acoustic slowdown pass
    """

    def __init__(
        self,
        canary_transcriber=None,
        whisper_model_id: str = "openai/whisper-large-v3",
        parakeet_model_id: str = "nvidia/parakeet-tdt-1.1b",
        conformer_model_id: str = "nvidia/stt_en_conformer_ctc_xlarge",
        device: Optional[str] = None,
        slowdown_factor: float = 0.75,
        attention_backend: Optional[str] = None,
        glossary: Optional[List[str]] = None
    ):
        self.canary_transcriber = canary_transcriber
        self.whisper_model_id = whisper_model_id
        self.parakeet_model_id = parakeet_model_id
        self.conformer_model_id = conformer_model_id
        self.device = device or ("cuda:0" if torch.cuda.is_available() else "cpu")
        self.slowdown_factor = slowdown_factor
        self.attention_backend = (attention_backend or getattr(config, "attention_backend", "sdpa")).lower()
        self.glossary = list(glossary if glossary is not None else getattr(config, "custom_glossary", []))

        self._whisper_pipeline = None
        self._conformer_model = None
        self._parakeet_model = None
        self._is_whisper_loaded = False
        self._is_conformer_loaded = False
        self._is_parakeet_loaded = False

    def _get_whisper_pipeline(self):
        """Lazy loader for Whisper cross-examiner with attention backend selection (SDPA/FlashAttention-2/Eager)."""
        if self._whisper_pipeline is None:
            try:
                from transformers import pipeline
                torch_dtype = torch.float16 if "cuda" in str(self.device) else torch.float32
                print(f"[Council] Loading Cross-Examiner ({self.whisper_model_id}) onto {self.device} (attention: {self.attention_backend})...")
                model_kwargs = {}
                if "cuda" in str(self.device):
                    if self.attention_backend == "flash_attention_2":
                        try:
                            import flash_attn
                            model_kwargs["attn_implementation"] = "flash_attention_2"
                        except ImportError:
                            model_kwargs["attn_implementation"] = "sdpa"
                    elif self.attention_backend == "eager":
                        model_kwargs["attn_implementation"] = "eager"
                    else:
                        model_kwargs["attn_implementation"] = "sdpa"

                self._whisper_pipeline = pipeline(
                    "automatic-speech-recognition",
                    model=self.whisper_model_id,
                    dtype=torch_dtype,
                    device=self.device,
                    model_kwargs=model_kwargs
                )
                self._is_whisper_loaded = True
            except Exception as e:
                print(f"[Council Warning] Failed to load Whisper cross-examiner ({e}).")
                self._whisper_pipeline = None
        return self._whisper_pipeline

    def _get_conformer_model(self):
        """Lazy loader for Conformer-CTC acoustic anchor with NGC name resolution and 16-bit precision."""
        if self._conformer_model is None:
            try:
                import nemo.collections.asr as nemo_asr
                m_id = self.conformer_model_id
                if m_id.startswith("nvidia/"):
                    m_id = m_id[len("nvidia/"):]
                target_device = self.device if (self.device.startswith("cuda") and torch.cuda.is_available()) else "cpu"
                print(f"[Council] Loading Acoustic Anchor ({m_id}) onto {target_device} in 16-bit precision...")
                self._conformer_model = nemo_asr.models.EncDecCTCModelBPE.from_pretrained(
                    model_name=m_id,
                    map_location="cpu"
                )
                if target_device.startswith("cuda"):
                    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
                    self._conformer_model = self._conformer_model.to(device=target_device, dtype=dtype)
                self._conformer_model.eval()
                self._is_conformer_loaded = True
            except Exception as e:
                print(f"[Council Warning] Failed to load Conformer-CTC acoustic anchor ({e}).")
                self._conformer_model = None
        return self._conformer_model

    def _get_parakeet_model(self):
        """Lazy loader for Parakeet-TDT transducer with CPU staging to avoid 8GB double-allocation peak."""
        if self._parakeet_model is None:
            try:
                import nemo.collections.asr as nemo_asr
                import gc
                import ctypes
                target_device = self.device if (self.device.startswith("cuda") and torch.cuda.is_available()) else "cpu"
                print(f"[Council] Loading Transducer Cross-Examiner ({self.parakeet_model_id}) via CPU staging onto {target_device}...")
                self._parakeet_model = nemo_asr.models.ASRModel.from_pretrained(
                    model_name=self.parakeet_model_id,
                    map_location="cpu"
                )
                if target_device.startswith("cuda"):
                    self._parakeet_model = self._parakeet_model.half()
                    self._parakeet_model = self._parakeet_model.to(target_device)
                self._parakeet_model.eval()
                self._is_parakeet_loaded = True
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                try:
                    ctypes.CDLL("libc.so.6").malloc_trim(0)
                except Exception:
                    pass
            except Exception as e:
                print(f"[Council Warning] Parakeet-TDT could not be loaded ({e}).")
                self._parakeet_model = None
        return self._parakeet_model

    def transcribe_with_whisper(self, audio_data: np.ndarray | str | Path, glossary: Optional[list[str]] = None) -> str:
        """Transcribes audio chunk using Whisper cross-examiner with optional glossary biasing."""
        pipe = self._get_whisper_pipeline()
        if pipe is None:
            return ""
        try:
            if isinstance(audio_data, (str, Path)):
                audio_input = str(Path(audio_data).resolve())
            else:
                audio_input = audio_data.astype(np.float32) if audio_data.dtype != np.float32 else audio_data

            kwargs = {"generate_kwargs": {"num_beams": 1}}
            if hasattr(pipe.model, "generation_config") and getattr(pipe.model.generation_config, "is_multilingual", False):
                kwargs["generate_kwargs"]["language"] = "en"
                kwargs["generate_kwargs"]["task"] = "transcribe"

            active_glossary = glossary if glossary is not None else self.glossary
            if active_glossary and hasattr(pipe, "tokenizer") and hasattr(pipe.tokenizer, "get_prompt_ids"):
                try:
                    kwargs["generate_kwargs"]["prompt_ids"] = pipe.tokenizer.get_prompt_ids(", ".join(active_glossary))
                except Exception as ep:
                    print(f"[Council Warning] Could not encode Whisper prompt_ids: {ep}")

            with torch.inference_mode():
                res = pipe(audio_input, **kwargs)

            if isinstance(res, dict):
                text = res.get("text", "").strip()
            elif isinstance(res, list) and len(res) > 0 and isinstance(res[0], dict):
                text = res[0].get("text", "").strip()
            else:
                text = str(res).strip()
            return text.strip()
        except Exception as e:
            print(f"[Council Warning] Whisper transcription failed: {e}")
            return ""

    def transcribe_batch_whisper(
        self,
        wav_paths: list[str | Path],
        batch_size: Optional[int] = None,
        progress_cb: Optional[Callable[[int, int, float], None]] = None,
        glossary: Optional[list[str]] = None
    ) -> list[str]:
        """Transcribes a batch of audio chunks using Whisper cross-examiner with chunk progress and glossary biasing."""
        if not wav_paths:
            return []
        pipe = self._get_whisper_pipeline()
        if pipe is None:
            return [""] * len(wav_paths)
        if batch_size is None:
            batch_size = 2 if "large" in str(self.whisper_model_id).lower() else 4
        try:
            resolved_paths = [str(Path(p).resolve()) for p in wav_paths]
            kwargs = {"generate_kwargs": {"num_beams": 1}}
            if hasattr(pipe.model, "generation_config") and getattr(pipe.model.generation_config, "is_multilingual", False):
                kwargs["generate_kwargs"]["language"] = "en"
                kwargs["generate_kwargs"]["task"] = "transcribe"

            active_glossary = glossary if glossary is not None else self.glossary
            if active_glossary and hasattr(pipe, "tokenizer") and hasattr(pipe.tokenizer, "get_prompt_ids"):
                try:
                    kwargs["generate_kwargs"]["prompt_ids"] = pipe.tokenizer.get_prompt_ids(", ".join(active_glossary))
                except Exception as ep:
                    print(f"[Council Warning] Could not encode Whisper batch prompt_ids: {ep}")

            texts = []
            total_items = len(resolved_paths)
            for i in range(0, total_items, batch_size):
                sub_paths = resolved_paths[i : i + batch_size]
                with torch.inference_mode():
                    sub_results = pipe(sub_paths, batch_size=len(sub_paths), **kwargs)
                for r in sub_results:
                    t = r.get("text", "") if isinstance(r, dict) else str(r)
                    texts.append(t.strip())
                if progress_cb:
                    progress_cb(len(texts), total_items, len(texts) / total_items)
            return texts
        except Exception as e:
            print(f"[Council Warning] Whisper batch transcription failed: {e}")
            fallback = []
            for p in wav_paths:
                fallback.append(self.transcribe_with_whisper(p, glossary=active_glossary))
                if progress_cb:
                    progress_cb(len(fallback), len(wav_paths), len(fallback) / len(wav_paths))
            return fallback

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
        finally:
            if self.device.startswith("cuda"):
                torch.cuda.empty_cache()

    def transcribe_batch_conformer(
        self,
        wav_paths: list[str | Path],
        batch_size: int = 8,
        progress_cb: Optional[Callable[[int, int, float], None]] = None
    ) -> list[str]:
        """Transcribes audio chunk paths using Conformer-CTC in micro-batches with progress."""
        if not wav_paths:
            return []
        model = self._get_conformer_model()
        if model is None:
            return [""] * len(wav_paths)
        try:
            resolved_paths = [str(Path(p).resolve()) for p in wav_paths]
            texts = []
            total_items = len(resolved_paths)
            for i in range(0, total_items, batch_size):
                sub_paths = resolved_paths[i : i + batch_size]
                with torch.inference_mode():
                    sub_results = model.transcribe(sub_paths, batch_size=len(sub_paths), return_hypotheses=False)
                for r in sub_results:
                    t = getattr(r, "text", str(r))
                    texts.append(t.strip())
                if progress_cb:
                    progress_cb(len(texts), total_items, len(texts) / total_items)
                if self.device.startswith("cuda"):
                    torch.cuda.empty_cache()
            return texts
        except Exception as e:
            print(f"[Council Warning] Conformer batch transcription failed: {e}")
            fallback = []
            for p in wav_paths:
                fallback.append(self.transcribe_with_conformer(p))
                if progress_cb:
                    progress_cb(len(fallback), len(wav_paths), len(fallback) / len(wav_paths))
            return fallback

    def transcribe_with_parakeet(self, wav_path: str | Path) -> str:
        """Transcribes audio chunk using Parakeet-TDT transducer."""
        model = self._get_parakeet_model()
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
            print(f"[Council Warning] Parakeet transcription failed: {e}")
            return ""

    def transcribe_batch_parakeet(
        self,
        wav_paths: list[str | Path],
        batch_size: int = 8,
        progress_cb: Optional[Callable[[int, int, float], None]] = None
    ) -> list[str]:
        """Transcribes audio chunk paths using Parakeet-TDT in micro-batches with progress."""
        if not wav_paths:
            return []
        model = self._get_parakeet_model()
        if model is None:
            return [""] * len(wav_paths)
        try:
            resolved_paths = [str(Path(p).resolve()) for p in wav_paths]
            texts = []
            total_items = len(resolved_paths)
            for i in range(0, total_items, batch_size):
                sub_paths = resolved_paths[i : i + batch_size]
                with torch.inference_mode():
                    sub_results = model.transcribe(sub_paths, batch_size=len(sub_paths), return_hypotheses=False)
                for r in sub_results:
                    t = getattr(r, "text", str(r))
                    texts.append(t.strip())
                if progress_cb:
                    progress_cb(len(texts), total_items, len(texts) / total_items)
            return texts
        except Exception as e:
            print(f"[Council Warning] Parakeet batch transcription failed: {e}")
            fallback = []
            for p in wav_paths:
                fallback.append(self.transcribe_with_parakeet(p))
                if progress_cb:
                    progress_cb(len(fallback), len(wav_paths), len(fallback) / len(wav_paths))
            return fallback

    def synthesize_deliberation(
        self,
        canary_text: str,
        whisper_text: str,
        conformer_text: str,
        parakeet_text: Optional[str] = None,
        audio_path: Optional[Path] = None,
        waveform: Optional[torch.Tensor] = None,
        seg_idx: int = 0,
        chunks_dir: Optional[Path] = None,
        sample_rate: int = 16000,
        glossary: Optional[List[str]] = None
    ) -> CouncilDeliberation:
        """
        Arbitrates trilateral/quadrilateral consensus across all available juror hypotheses.
        """
        votes: List[CouncilVote] = []

        # 1. Lead Justice Vote (Canary-Qwen)
        raw_canary = (canary_text or "").strip()
        is_canary_loop, canary_loop_reason, _ = detect_autoregressive_loop(raw_canary)
        h_canary = sanitize_canary_output(raw_canary, chunk_waveform=waveform)
        if not h_canary and raw_canary and is_canary_loop:
            h_canary = raw_canary

        canary_conf = 0.95 if h_canary else 0.10
        if any(m in h_canary for m in ["???", "[inaudible]", "...", "uhh", "um"]):
            canary_conf = 0.50
        elif len(h_canary) < 4 and h_canary:
            canary_conf = 0.65

        # Feature 2.2.C: Canary Autoregressive Loop Check
        if is_canary_loop:
            print(f"[Council Loop Sentry] Canary-Qwen loop vetoed: {canary_loop_reason}")
            canary_conf = 0.05
            canary_weight = 0.0
            canary_role = "Lead Justice [CANARY_LOOP_VETO]"
        else:
            canary_weight = 1.5
            canary_role = "Lead Justice"

        votes.append(CouncilVote(
            member="Canary-Qwen-2.5B",
            role=canary_role,
            hypothesis=h_canary,
            confidence=canary_conf,
            weight=canary_weight
        ))

        # 2. Cross-Examiner Vote (Whisper)
        h_whisper = (whisper_text or "").strip()
        whisper_conf = 0.92 if h_whisper else 0.15
        if not h_whisper or any(m in h_whisper for m in ["???", "..."]):
            whisper_conf = 0.45

        # Feature 2.2.C: Whisper Autoregressive Loop Check
        is_whisper_loop, whisper_loop_reason, _ = detect_autoregressive_loop(h_whisper)
        if is_whisper_loop:
            print(f"[Council Loop Sentry] Whisper loop vetoed: {whisper_loop_reason}")
            whisper_conf = 0.05
            whisper_weight = 0.0
            whisper_role = "Cross-Examiner [WHISPER_LOOP_VETO]"
        else:
            whisper_weight = 1.2
            whisper_role = "Cross-Examiner"

        votes.append(CouncilVote(
            member="Whisper-CrossExaminer",
            role=whisper_role,
            hypothesis=h_whisper,
            confidence=whisper_conf,
            weight=whisper_weight
        ))

        # 3. Transducer Vote (Parakeet-TDT, if present)
        h_parakeet = (parakeet_text or "").strip()
        if h_parakeet:
            parakeet_conf = 0.94 if h_parakeet else 0.15
            votes.append(CouncilVote(
                member="Parakeet-TDT-1.1B",
                role="Transducer Juror",
                hypothesis=h_parakeet,
                confidence=parakeet_conf,
                weight=1.3
            ))

        # 4. Acoustic Anchor Vote (Conformer-CTC)
        h_conformer = (conformer_text or "").strip()
        conformer_conf = 0.96 if h_conformer else 0.90
        votes.append(CouncilVote(
            member="Conformer-CTC-Anchor",
            role="Acoustic Anchor",
            hypothesis=h_conformer,
            confidence=conformer_conf,
            weight=1.3
        ))

        # Circuit-Breaker: Check if autoregressive decoders must be completely bypassed (Feature 2.2.C)
        circuit_breaker_tripped = False
        if is_canary_loop and is_whisper_loop:
            circuit_breaker_tripped = True
        elif is_canary_loop and not h_whisper:
            circuit_breaker_tripped = True
        elif is_whisper_loop and not h_canary:
            circuit_breaker_tripped = True

        if circuit_breaker_tripped:
            anchor_verdict = h_parakeet or h_conformer
            reasons = []
            if is_canary_loop:
                reasons.append(f"Canary ({canary_loop_reason})")
            if is_whisper_loop:
                reasons.append(f"Whisper ({whisper_loop_reason})")
            notes = f"[LOOP_CIRCUIT_BREAKER_TRIPPED] Autoregressive hallucination loop vetoed: {'; '.join(reasons)}. Defaulted to Acoustic Anchor."
            disp = find_disputed_words([h_canary, h_whisper, anchor_verdict])
            decomp = compute_orthogonal_confidence(
                waveform=waveform,
                sr=sample_rate,
                conformer_conf=conformer_conf,
                lattice_score=0.60,
                canary_text=h_canary,
                whisper_text=h_whisper,
                disputed_tokens=disp,
                circuit_breaker_tripped=True
            )
            return CouncilDeliberation(
                verdict=anchor_verdict,
                consensus_score=0.60,
                agreement_type="CTC_ANCHORED",
                votes=[v.to_dict() for v in votes],
                disputed_tokens=disp,
                needs_human_review=False,
                deliberation_notes=notes,
                loop_circuit_breaker_tripped=True,
                confidence_decomposition=decomp,
                ctc_vetoes=1
            )

        # Deliberation Analysis

        # Case A: Silence / Non-speech Anchor
        if not h_conformer and not h_parakeet:
            if not h_canary and not h_whisper:
                decomp = compute_orthogonal_confidence(
                    waveform=waveform,
                    sr=sample_rate,
                    conformer_conf=conformer_conf,
                    lattice_score=1.0,
                    canary_text="",
                    whisper_text="",
                    disputed_tokens=[],
                    circuit_breaker_tripped=False
                )
                return CouncilDeliberation(
                    verdict="",
                    consensus_score=1.0,
                    agreement_type="UNANIMOUS",
                    votes=[v.to_dict() for v in votes],
                    disputed_tokens=[],
                    needs_human_review=False,
                    deliberation_notes="Unanimous consensus: non-speech / silence.",
                    loop_circuit_breaker_tripped=False,
                    confidence_decomposition=decomp
                )
            else:
                has_substantive = bool(h_whisper and len(h_whisper.split()) >= 2) or bool(h_canary and len(h_canary.split()) >= 2)
                disp = find_disputed_words([h_canary, h_whisper])
                if has_substantive:
                    chosen_verdict = h_whisper if h_whisper else h_canary
                    decomp = compute_orthogonal_confidence(
                        waveform=waveform,
                        sr=sample_rate,
                        conformer_conf=conformer_conf,
                        lattice_score=0.45,
                        canary_text=h_canary,
                        whisper_text=h_whisper,
                        disputed_tokens=disp,
                        circuit_breaker_tripped=False
                    )
                    return CouncilDeliberation(
                        verdict=chosen_verdict,
                        consensus_score=0.45,
                        agreement_type="SPLIT_DECISION",
                        votes=[v.to_dict() for v in votes],
                        disputed_tokens=disp,
                        needs_human_review=True,
                        deliberation_notes="Split decision: Acoustic anchors returned no text but Cross-Examiner transcribed substantive speech. Flagged for review/adjudication.",
                        loop_circuit_breaker_tripped=False,
                        confidence_decomposition=decomp
                    )
                else:
                    decomp = compute_orthogonal_confidence(
                        waveform=waveform,
                        sr=sample_rate,
                        conformer_conf=conformer_conf,
                        lattice_score=0.85,
                        canary_text=h_canary,
                        whisper_text=h_whisper,
                        disputed_tokens=disp,
                        circuit_breaker_tripped=False
                    )
                    return CouncilDeliberation(
                        verdict="",
                        consensus_score=0.85,
                        agreement_type="CTC_ANCHORED",
                        votes=[v.to_dict() for v in votes],
                        disputed_tokens=disp,
                        needs_human_review=False,
                        deliberation_notes="Acoustic Anchor (CTC) verified non-speech. Short token hallucination suppressed.",
                        loop_circuit_breaker_tripped=False,
                        confidence_decomposition=decomp,
                        ctc_vetoes=1
                    )

        # Unified Token & Acoustic Lattice (Confusion Network Alignment)
        active_hyps = [h for h in [h_canary, h_whisper, h_parakeet, h_conformer] if h]
        lattice = TokenLattice()
        lattice.add_hypothesis("Canary-Qwen-2.5B", h_canary, canary_conf, weight=canary_weight)
        lattice.add_hypothesis("Whisper-CrossExaminer", h_whisper, whisper_conf, weight=whisper_weight)
        lattice.add_hypothesis("Conformer-CTC-Anchor", h_conformer, conformer_conf, weight=1.3)
        if h_parakeet:
            lattice.add_hypothesis("Parakeet-TDT-1.1B", h_parakeet, parakeet_conf, weight=1.3)

        synth = lattice.synthesize()
        verdict = synth["verdict"]
        consensus_score = synth["consensus_score"]
        disputed_tokens = synth["disputed_tokens"]
        ctc_vetoes = synth["ctc_vetoes"]
        homophone_resolutions = synth["homophone_resolutions"]

        # Low Consensus Escalation -> Invoke Time-Stretch Auditor if audio available
        h_auditor = ""
        auditor_conf = 0.88
        if (consensus_score < 0.55 or len(disputed_tokens) >= 4) and audio_path and Path(audio_path).exists():
            try:
                slow_audio_path = None
                if chunks_dir:
                    slow_audio_path = Path(chunks_dir) / f"chunk_{seg_idx}_slow.wav"
                if not slow_audio_path or not slow_audio_path.exists():
                    if waveform is not None:
                        stretched = self._stretch_audio(waveform, factor=self.slowdown_factor)
                        with tempfile.NamedTemporaryFile(suffix="_slow.wav", delete=False) as tf:
                            sf.write(tf.name, stretched.squeeze(0).cpu().numpy(), sample_rate, subtype="PCM_16")
                            slow_audio_path = Path(tf.name)
                if slow_audio_path and slow_audio_path.exists():
                    h_auditor = self.transcribe_with_whisper(slow_audio_path, glossary=glossary)
                    votes.append(CouncilVote(
                        member="Acoustic-Auditor-0.75x",
                        role="Time-Stretch Auditor",
                        hypothesis=h_auditor,
                        confidence=auditor_conf,
                        weight=1.2
                    ))
                    # Add auditor hypothesis to lattice to break token disputes
                    lattice.add_hypothesis("Acoustic-Auditor-0.75x", h_auditor, auditor_conf, weight=1.2)
                    synth = lattice.synthesize()
                    verdict = synth["verdict"]
                    consensus_score = synth["consensus_score"]
                    disputed_tokens = synth["disputed_tokens"]
                    ctc_vetoes = synth["ctc_vetoes"]
                    homophone_resolutions = synth["homophone_resolutions"]
            except Exception as e:
                print(f"[Council Warning] Slow auditor pass error: {e}")

        # Fallback to simple disputed words if lattice has none but raw texts diverge
        if not disputed_tokens and len(find_disputed_words(active_hyps)) > 0:
            disputed_tokens = find_disputed_words(active_hyps)

        # Agreement classification
        sim_canary_whisper = calculate_similarity(h_canary, h_whisper)
        sim_canary_conformer = calculate_similarity(h_canary, h_conformer)
        sim_whisper_conformer = calculate_similarity(h_whisper, h_conformer)

        if not verdict:
            agreement_type = "CTC_ANCHORED" if ctc_vetoes > 0 else "UNANIMOUS"
            needs_review = False
        elif sim_canary_whisper >= 0.85 and canary_conf >= 0.75 and whisper_conf >= 0.75:
            agreement_type = "UNANIMOUS"
            needs_review = False
        elif consensus_score >= 0.65 or (sim_whisper_conformer >= 0.65 or sim_canary_conformer >= 0.65):
            agreement_type = "MAJORITY"
            needs_review = False
        elif ctc_vetoes > 0:
            agreement_type = "CTC_ANCHORED"
            needs_review = False
        else:
            agreement_type = "SPLIT_DECISION"
            needs_review = (consensus_score < 0.55)

        # Notes generation
        notes_parts = []
        if agreement_type == "UNANIMOUS":
            notes_parts.append(f"UNANIMOUS lattice consensus ({round(consensus_score * 100)}%) across Lead Justice and Cross-Examiner.")
        elif agreement_type == "CTC_ANCHORED":
            notes_parts.append(f"CTC-anchored consensus ({round(consensus_score * 100)}%): {ctc_vetoes} phantom tokens vetoed by acoustic anchor.")
        elif agreement_type == "MAJORITY":
            notes_parts.append(f"Majority lattice consensus ({round(consensus_score * 100)}%) across council.")
        else:
            notes_parts.append(f"Split decision across jurors (consensus: {round(consensus_score * 100)}%). Flagged for review.")

        if homophone_resolutions > 0:
            notes_parts.append(f"{homophone_resolutions} phonetic homophones harmonized.")
        if ctc_vetoes > 0 and agreement_type != "CTC_ANCHORED":
            notes_parts.append(f"{ctc_vetoes} acoustic anchor confirmations.")

        notes = " ".join(notes_parts)

        decomp = compute_orthogonal_confidence(
            waveform=waveform,
            sr=sample_rate,
            conformer_conf=conformer_conf,
            lattice_score=consensus_score,
            canary_text=h_canary,
            whisper_text=h_whisper,
            disputed_tokens=disputed_tokens,
            circuit_breaker_tripped=False
        )

        return CouncilDeliberation(
            verdict=verdict or h_whisper or h_canary,
            consensus_score=round(consensus_score, 3),
            agreement_type=agreement_type,
            votes=[v.to_dict() for v in votes],
            disputed_tokens=disputed_tokens,
            needs_human_review=needs_review,
            deliberation_notes=notes,
            homophone_resolutions=homophone_resolutions,
            ctc_vetoes=ctc_vetoes,
            loop_circuit_breaker_tripped=False,
            confidence_decomposition=decomp
        )

    def deliberate(
        self,
        audio_path: str | Path,
        sample_rate: int = 16000,
        canary_text: Optional[str] = None,
        waveform: Optional[torch.Tensor] = None,
        seg_idx: int = 0,
        chunks_dir: Optional[Path] = None,
        force_full_council: bool = False,
        glossary: Optional[List[str]] = None
    ) -> CouncilDeliberation:
        """1-pass deliberate wrapper for concurrent mode."""
        audio_path = Path(audio_path).resolve()

        h_canary = canary_text
        if h_canary is None and self.canary_transcriber:
            h_canary = self.canary_transcriber.transcribe_chunk(str(audio_path), glossary=glossary)

        h_whisper = self.transcribe_with_whisper(audio_path, glossary=glossary)
        h_conformer = self.transcribe_with_conformer(audio_path)

        return self.synthesize_deliberation(
            canary_text=h_canary,
            whisper_text=h_whisper,
            conformer_text=h_conformer,
            parakeet_text=None,
            audio_path=audio_path,
            waveform=waveform,
            seg_idx=seg_idx,
            chunks_dir=chunks_dir,
            sample_rate=sample_rate,
            glossary=glossary
        )

    def deliberate_waveform_segment(
        self,
        waveform_slice: torch.Tensor,
        sr: int = 16000,
        canary_text: Optional[str] = None,
        seg_idx: int = 0,
        chunks_dir: Optional[Path] = None,
        glossary: Optional[List[str]] = None
    ) -> CouncilDeliberation:
        """Convenience runner for in-memory tensor slices."""
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
                chunks_dir=chunks_dir,
                glossary=glossary
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

    def _clean_memory(self):
        """Forces double GC collect and glibc memory trim."""
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

    def unload_whisper(self):
        """Unloads Whisper model and frees VRAM."""
        if self._whisper_pipeline is not None:
            del self._whisper_pipeline
            self._whisper_pipeline = None
            self._is_whisper_loaded = False
        self._clean_memory()
        print("[Council] Whisper model unloaded from VRAM.")

    def unload_conformer(self):
        """Unloads Conformer model and frees VRAM."""
        if self._conformer_model is not None:
            del self._conformer_model
            self._conformer_model = None
            self._is_conformer_loaded = False
        self._clean_memory()
        print("[Council] Conformer-CTC model unloaded from VRAM.")

    def unload_parakeet(self):
        """Unloads Parakeet model and frees VRAM."""
        if self._parakeet_model is not None:
            del self._parakeet_model
            self._parakeet_model = None
            self._is_parakeet_loaded = False
        self._clean_memory()
        print("[Council] Parakeet transducer unloaded from VRAM.")

    def unload_parakeet_and_ctc(self):
        """Unloads Parakeet and Conformer models and frees VRAM."""
        self.unload_conformer()
        self.unload_parakeet()

    def unload_members(self):
        """Unloads all models to free VRAM."""
        self.unload_whisper()
        self.unload_parakeet_and_ctc()
        print("[Council] All Multi-Model Council members unloaded.")
