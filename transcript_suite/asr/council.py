"""
Multi-Model Inference Council (Supreme Model Jury).
Coordinates multiple distinct ASR architectures (Canary-Qwen-2.5B, Whisper Large/Small,
Parakeet-TDT Transducer, Conformer-CTC, and Time-Stretch Acoustic Auditor) to reach consensus,
resolve acoustic ambiguities, and eliminate hallucinations.
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
    agreement_type: str  # UNANIMOUS | MAJORITY | CTC_ANCHORED | SPLIT_DECISION
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
        slowdown_factor: float = 0.75
    ):
        self.canary_transcriber = canary_transcriber
        self.whisper_model_id = whisper_model_id
        self.parakeet_model_id = parakeet_model_id
        self.conformer_model_id = conformer_model_id
        self.device = device or ("cuda:0" if torch.cuda.is_available() else "cpu")
        self.slowdown_factor = slowdown_factor

        self._whisper_pipeline = None
        self._conformer_model = None
        self._parakeet_model = None
        self._is_whisper_loaded = False
        self._is_conformer_loaded = False
        self._is_parakeet_loaded = False

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
                    model_name=self.conformer_model_id,
                    map_location="cpu"
                )
                if self.device.startswith("cuda") and torch.cuda.is_available():
                    self._conformer_model = self._conformer_model.to(self.device)
                self._conformer_model.eval()
                self._is_conformer_loaded = True
            except Exception as e:
                print(f"[Council Warning] Failed to load Conformer-CTC acoustic anchor ({e}).")
                self._conformer_model = None
        return self._conformer_model

    def _get_parakeet_model(self):
        """Lazy loader for Parakeet-TDT transducer."""
        if self._parakeet_model is None:
            try:
                import nemo.collections.asr as nemo_asr
                print(f"[Council] Loading Transducer Cross-Examiner ({self.parakeet_model_id}) in FP16 onto {self.device}...")
                self._parakeet_model = nemo_asr.models.ASRModel.from_pretrained(
                    model_name=self.parakeet_model_id,
                    map_location="cpu"
                )
                if self.device.startswith("cuda") and torch.cuda.is_available():
                    self._parakeet_model = self._parakeet_model.half().to(self.device)
                self._parakeet_model.eval()
                self._is_parakeet_loaded = True
            except Exception as e:
                print(f"[Council Warning] Parakeet-TDT could not be loaded ({e}).")
                self._parakeet_model = None
        return self._parakeet_model

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

    def transcribe_batch_whisper(self, wav_paths: list[str | Path], batch_size: Optional[int] = None) -> list[str]:
        """Transcribes a batch of audio chunks using Whisper cross-examiner."""
        if not wav_paths:
            return []
        pipe = self._get_whisper_pipeline()
        if pipe is None:
            return [""] * len(wav_paths)
        if batch_size is None:
            batch_size = 4 if "large" in str(self.whisper_model_id).lower() else 8
        try:
            resolved_paths = [str(Path(p).resolve()) for p in wav_paths]
            kwargs = {}
            if hasattr(pipe.model, "generation_config") and getattr(pipe.model.generation_config, "is_multilingual", False):
                kwargs["generate_kwargs"] = {"language": "en", "task": "transcribe"}

            results = pipe(resolved_paths, batch_size=batch_size, **kwargs)
            texts = []
            for r in results:
                t = r.get("text", "") if isinstance(r, dict) else str(r)
                texts.append(t.strip())
            return texts
        except Exception as e:
            print(f"[Council Warning] Whisper batch transcription failed: {e}")
            fallback = []
            for p in wav_paths:
                fallback.append(self.transcribe_with_whisper(p))
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

    def transcribe_batch_conformer(self, wav_paths: list[str | Path], batch_size: int = 16) -> list[str]:
        """Transcribes a list of audio chunk paths using Conformer-CTC in parallel batches."""
        if not wav_paths:
            return []
        model = self._get_conformer_model()
        if model is None:
            return [""] * len(wav_paths)
        try:
            resolved_paths = [str(Path(p).resolve()) for p in wav_paths]
            with torch.inference_mode():
                results = model.transcribe(resolved_paths, batch_size=batch_size, return_hypotheses=False)
            texts = []
            for r in results:
                t = getattr(r, "text", str(r))
                texts.append(t.strip())
            return texts
        except Exception as e:
            print(f"[Council Warning] Conformer batch transcription failed: {e}")
            fallback = []
            for p in wav_paths:
                fallback.append(self.transcribe_with_conformer(p))
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

    def transcribe_batch_parakeet(self, wav_paths: list[str | Path], batch_size: int = 16) -> list[str]:
        """Transcribes a list of audio chunk paths using Parakeet-TDT in parallel batches."""
        if not wav_paths:
            return []
        model = self._get_parakeet_model()
        if model is None:
            return [""] * len(wav_paths)
        try:
            resolved_paths = [str(Path(p).resolve()) for p in wav_paths]
            with torch.inference_mode():
                results = model.transcribe(resolved_paths, batch_size=batch_size, return_hypotheses=False)
            texts = []
            for r in results:
                t = getattr(r, "text", str(r))
                texts.append(t.strip())
            return texts
        except Exception as e:
            print(f"[Council Warning] Parakeet batch transcription failed: {e}")
            fallback = []
            for p in wav_paths:
                fallback.append(self.transcribe_with_parakeet(p))
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
        sample_rate: int = 16000
    ) -> CouncilDeliberation:
        """
        Arbitrates trilateral/quadrilateral consensus across all available juror hypotheses.
        """
        votes: List[CouncilVote] = []

        # 1. Lead Justice Vote (Canary-Qwen)
        h_canary = sanitize_canary_output(canary_text or "", chunk_waveform=waveform)
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

        # 2. Cross-Examiner Vote (Whisper)
        h_whisper = (whisper_text or "").strip()
        whisper_conf = 0.92 if h_whisper else 0.15
        if not h_whisper or any(m in h_whisper for m in ["???", "..."]):
            whisper_conf = 0.45

        votes.append(CouncilVote(
            member="Whisper-CrossExaminer",
            role="Cross-Examiner",
            hypothesis=h_whisper,
            confidence=whisper_conf,
            weight=1.2
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

        # Deliberation Analysis

        # Case A: Silence / Non-speech Anchor
        if not h_conformer and not h_parakeet:
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
                    deliberation_notes="Acoustic Anchor (CTC) verified non-speech. Autoregressive hallucination suppressed."
                )

        # Case B: Canary Prompt Leak Neutralization
        if not h_canary and (h_whisper or h_parakeet):
            primary_alt = h_whisper or h_parakeet
            sim_w_c = calculate_similarity(primary_alt, h_conformer)
            verdict = primary_alt
            notes = f"Canary prompt leak neutralized. Adopted Cross-Examiner anchored by Conformer-CTC ({round(sim_w_c * 100)}% acoustic match)."
            return CouncilDeliberation(
                verdict=verdict,
                consensus_score=round(max(0.85, sim_w_c), 3),
                agreement_type="MAJORITY",
                votes=[v.to_dict() for v in votes],
                disputed_tokens=find_disputed_words([primary_alt, h_conformer]),
                needs_human_review=False,
                deliberation_notes=notes
            )

        # Case C: High Agreement between Canary & Cross-Examiner
        sim_canary_whisper = calculate_similarity(h_canary, h_whisper)
        sim_canary_conformer = calculate_similarity(h_canary, h_conformer)
        sim_whisper_conformer = calculate_similarity(h_whisper, h_conformer)

        active_hyps = [h for h in [h_canary, h_whisper, h_parakeet, h_conformer] if h]
        avg_consensus = (sim_canary_whisper + sim_canary_conformer + sim_whisper_conformer) / 3.0

        if sim_canary_whisper >= 0.85 and canary_conf >= 0.75 and whisper_conf >= 0.75:
            verdict = h_canary if len(h_canary) >= len(h_whisper) else h_whisper
            agreement_type = "UNANIMOUS" if (sim_canary_conformer >= 0.70 or sim_whisper_conformer >= 0.70) else "MAJORITY"
            return CouncilDeliberation(
                verdict=verdict,
                consensus_score=round(sim_canary_whisper, 3),
                agreement_type=agreement_type,
                votes=[v.to_dict() for v in votes],
                disputed_tokens=find_disputed_words(active_hyps),
                needs_human_review=False,
                deliberation_notes=f"{agreement_type} consensus ({round(sim_canary_whisper * 100)}%) across Lead Justice and Cross-Examiner."
            )

        # Whisper + Conformer overrule Canary
        if sim_whisper_conformer >= 0.65 and sim_canary_whisper < 0.65:
            return CouncilDeliberation(
                verdict=h_whisper,
                consensus_score=round(sim_whisper_conformer, 3),
                agreement_type="MAJORITY",
                votes=[v.to_dict() for v in votes],
                disputed_tokens=find_disputed_words(active_hyps),
                needs_human_review=False,
                deliberation_notes=f"Majority consensus ({round(sim_whisper_conformer * 100)}%): Whisper supported by Conformer-CTC overrules Canary."
            )

        # Canary + Conformer overrule Whisper
        if sim_canary_conformer >= 0.65 and sim_canary_whisper < 0.65:
            return CouncilDeliberation(
                verdict=h_canary,
                consensus_score=round(sim_canary_conformer, 3),
                agreement_type="MAJORITY",
                votes=[v.to_dict() for v in votes],
                disputed_tokens=find_disputed_words(active_hyps),
                needs_human_review=False,
                deliberation_notes=f"Majority consensus ({round(sim_canary_conformer * 100)}%): Canary supported by Conformer-CTC overrules Whisper."
            )

        # Case D: Parakeet alignment
        if h_parakeet:
            sim_parakeet_whisper = calculate_similarity(h_parakeet, h_whisper)
            sim_parakeet_canary = calculate_similarity(h_parakeet, h_canary)
            if sim_parakeet_whisper >= 0.80:
                return CouncilDeliberation(
                    verdict=h_whisper,
                    consensus_score=round(sim_parakeet_whisper, 3),
                    agreement_type="MAJORITY",
                    votes=[v.to_dict() for v in votes],
                    disputed_tokens=find_disputed_words(active_hyps),
                    needs_human_review=False,
                    deliberation_notes=f"Majority consensus ({round(sim_parakeet_whisper * 100)}%): Parakeet-TDT confirmed Whisper hypothesis."
                )

        # Case E: Low Consensus -> Invoke Auditor if audio available
        h_auditor = ""
        auditor_conf = 0.88
        if audio_path and Path(audio_path).exists():
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
                    h_auditor = self.transcribe_with_whisper(slow_audio_path)
                    votes.append(CouncilVote(
                        member="Acoustic-Auditor-0.75x",
                        role="Time-Stretch Auditor",
                        hypothesis=h_auditor,
                        confidence=auditor_conf,
                        weight=1.2
                    ))
            except Exception as e:
                print(f"[Council Warning] Slow auditor pass error: {e}")

        # Resolve Split Decision
        if h_auditor and calculate_similarity(h_auditor, h_whisper) >= 0.70:
            verdict = h_whisper
            agreement_type = "MAJORITY"
            notes = "Acoustic Auditor (0.75x) confirmed Whisper hypothesis."
            needs_review = False
        elif h_auditor and calculate_similarity(h_auditor, h_canary) >= 0.70:
            verdict = h_canary
            agreement_type = "MAJORITY"
            notes = "Acoustic Auditor (0.75x) confirmed Canary hypothesis."
            needs_review = False
        else:
            agreement_type = "SPLIT_DECISION"
            best_vote = max(votes, key=lambda v: v.confidence * v.weight if v.hypothesis else -1)
            verdict = best_vote.hypothesis
            notes = f"Split decision across jurors (consensus: {round(avg_consensus * 100)}%). Resolved via weighted acoustic score ({best_vote.member})."
            needs_review = (avg_consensus < 0.55)

        return CouncilDeliberation(
            verdict=verdict or h_whisper or h_canary,
            consensus_score=round(avg_consensus, 3),
            agreement_type=agreement_type,
            votes=[v.to_dict() for v in votes],
            disputed_tokens=find_disputed_words(active_hyps),
            needs_human_review=needs_review,
            deliberation_notes=notes
        )

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
        """1-pass deliberate wrapper for concurrent mode."""
        audio_path = Path(audio_path).resolve()

        h_canary = canary_text
        if h_canary is None and self.canary_transcriber:
            h_canary = self.canary_transcriber.transcribe_chunk(str(audio_path))

        h_whisper = self.transcribe_with_whisper(audio_path)
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
            sample_rate=sample_rate
        )

    def deliberate_waveform_segment(
        self,
        waveform_slice: torch.Tensor,
        sr: int = 16000,
        canary_text: Optional[str] = None,
        seg_idx: int = 0,
        chunks_dir: Optional[Path] = None
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
