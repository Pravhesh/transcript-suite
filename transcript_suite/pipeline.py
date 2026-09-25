"""
End-to-end orchestration pipeline for Transcript Suite.
Coordinates Audio Loader -> Diarization -> VAD -> Canary-Qwen ASR -> Speaker Alignment.
"""

from typing import List, Dict, Any, Optional, Callable, Iterator
from pathlib import Path
import time
import tempfile
import soundfile as sf
import torch
import numpy as np

from .config import config
from .audio.loader import AudioLoader
from .audio.vad import SileroVADSegmenter, SpeechSegment
from .audio.chunker import PseudoStreamChunker, StreamChunk, deduplicate_chunk_boundary
from .audio.enhancer import GPUSpeechEnhancer
from .asr.canary import CanaryQwenTranscriber
from .asr.memory import VRAMManager, get_subsystem_supervisor
from .asr.ambiguity import AmbiguityResolver
from .asr.council import ModelCouncil
from .diarization.nemo_titanet import NeMoTitaNetDiarizer
from .diarization.pyannote import PyAnnoteDiarizer
from .diarization.base import SpeakerTurn
from .export import TranscriptExporter

_active_pipeline: Optional["TranscriptionPipeline"] = None


def get_active_pipeline() -> Optional["TranscriptionPipeline"]:
    """Returns the currently instantiated pipeline instance if available."""
    global _active_pipeline
    return _active_pipeline


class TranscriptionPipeline:
    def __init__(
        self,
        diarizer_type: Optional[str] = None,
        hf_token: Optional[str] = None,
        model_name: Optional[str] = None,
        whisper_model: Optional[str] = None,
        conformer_model: Optional[str] = None,
        parakeet_model: Optional[str] = None,
        vocal_boost_level: Optional[str] = None,
        enable_audex_adjudicator: Optional[bool] = None,
        audex_model_id: Optional[str] = None,
        attention_backend: Optional[str] = None,
        custom_glossary: Optional[List[str]] = None,
        pipeline_config: Optional[Any] = None
    ):
        self.config = pipeline_config or config
        self.device = getattr(self.config, "device", "cuda" if torch.cuda.is_available() else "cpu")
        self.vocal_boost_level = vocal_boost_level or getattr(self.config, "vocal_boost_level", "adaptive")
        self.enable_audex_adjudicator = (
            enable_audex_adjudicator
            if enable_audex_adjudicator is not None
            else getattr(self.config, "enable_audex_adjudicator", False)
        )
        self.audex_model_id = audex_model_id or getattr(self.config, "audex_model_id", "nvidia/Nemotron-Labs-Audex-2B")
        self.attention_backend = (
            attention_backend
            if attention_backend is not None
            else getattr(self.config, "attention_backend", "sdpa")
        )
        self.custom_glossary = (
            list(custom_glossary)
            if custom_glossary is not None
            else list(getattr(self.config, "custom_glossary", []))
        )

        self.enable_lufs_normalization = getattr(self.config, "enable_lufs_normalization", True)
        self.target_lufs = getattr(self.config, "target_lufs", -16.0)
        self.chunk_overlap_s = getattr(self.config, "chunk_overlap_s", 0.5)
        self.enable_boundary_dedup = getattr(self.config, "enable_boundary_dedup", True)

        self.audio_loader = AudioLoader(
            target_sr=getattr(self.config, "sample_rate", 16000),
            enable_lufs_norm=self.enable_lufs_normalization,
            target_lufs=self.target_lufs
        )
        self.enhancer = GPUSpeechEnhancer(
            sample_rate=getattr(self.config, "sample_rate", 16000),
            device=self.device,
            boost_level=self.vocal_boost_level
        )
        self.ambiguity_resolver = AmbiguityResolver(sample_rate=getattr(self.config, "sample_rate", 16000))
        self.vad = SileroVADSegmenter(
            sample_rate=getattr(self.config, "sample_rate", 16000),
            max_chunk_duration=getattr(self.config, "max_chunk_duration_s", 25.0),
            min_chunk_duration=getattr(self.config, "min_chunk_duration_s", 1.0),
            padding_duration=getattr(self.config, "vad_padding_s", 0.3),
            device=self.device
        )
        self.transcriber = CanaryQwenTranscriber(
            model_name=model_name or getattr(self.config, "model_name", "nvidia/canary-qwen-2.5b"),
            device=self.device,
            dtype=getattr(self.config, "dtype", torch.float16),
            attention_backend=self.attention_backend,
            glossary=self.custom_glossary
        )
        self.council = ModelCouncil(
            canary_transcriber=self.transcriber,
            whisper_model_id=whisper_model or getattr(self.config, "whisper_model", "openai/whisper-large-v3"),
            parakeet_model_id=parakeet_model or getattr(self.config, "parakeet_model", "nvidia/parakeet-tdt-1.1b"),
            conformer_model_id=conformer_model or getattr(self.config, "conformer_model", "nvidia/stt_en_conformer_ctc_xlarge"),
            device=self.device,
            attention_backend=self.attention_backend,
            glossary=self.custom_glossary
        )
        self.vram_manager = VRAMManager()
        self.supervisor = get_subsystem_supervisor()
        
        # Select diarizer route
        active_diarizer = diarizer_type or getattr(self.config, "default_diarizer", "nemo")
        if active_diarizer == "pyannote":
            self.diarizer = PyAnnoteDiarizer(hf_token=hf_token or getattr(self.config, "hf_token", None), device=self.device)
        else:
            self.diarizer = NeMoTitaNetDiarizer(model_name=getattr(self.config, "nemo_diarizer_model", "titanet_large"), device=self.device)

        global _active_pipeline
        _active_pipeline = self

    def _reclaim_memory(self):
        """
        Aggressively reclaims memory between pipeline stages.
        Runs double gc pass, empties CUDA cache, and forces glibc to return pages to OS.
        """
        import gc
        gc.collect()
        gc.collect()  # Second pass catches ref cycles freed by first pass
        self.vram_manager.clear_cache()
        try:
            import ctypes
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except Exception:
            pass

    def _unload_and_log(
        self,
        model_name: str,
        unload_fn: Callable[[], None],
        report_cb: Optional[Callable[[str, float], None]] = None,
        progress_frac: Optional[float] = None,
        subsystem_id: Optional[str] = None
    ):
        """
        Unloads model, aggressively clears caches, calculates reclaimed VRAM delta,
        and emits an explicit [MEM] log message. Also updates SubsystemSupervisor.
        """
        before_stats = self.vram_manager.get_stats()
        before_res = before_stats.get("reserved_gb", 0.0)
        before_ram = before_stats.get("proc_ram_used_gb", 0.0)

        try:
            unload_fn()
        except Exception as e:
            print(f"[Pipeline Warning] Error unloading {model_name}: {e}")

        self._reclaim_memory()

        after_stats = self.vram_manager.get_stats()
        after_res = after_stats.get("reserved_gb", 0.0)
        after_ram = after_stats.get("proc_ram_used_gb", 0.0)
        reclaimed_vram = max(0.0, round(before_res - after_res, 2))
        reclaimed_ram = max(0.0, round((before_ram - after_ram) * 1024, 1))
        curr_vram = after_stats.get("reserved_gb", 0.0)
        tot_vram = after_stats.get("total_gb", 0.0)
        app_ram = after_stats.get("proc_ram_used_gb", 0.0)

        if subsystem_id:
            self.supervisor.record_stage_unload(
                subsystem_id,
                reclaimed_vram_mb=reclaimed_vram * 1024,
                reclaimed_ram_mb=reclaimed_ram
            )

        log_msg = f"[MEM] 🔄 Unloaded {model_name}. Reclaimed {reclaimed_vram:.2f} GB VRAM. Current VRAM: {curr_vram:.2f}G / {tot_vram:.2f}G | App RAM: {app_ram:.2f} GB."
        print(log_msg)
        if report_cb and progress_frac is not None:
            try:
                report_cb(log_msg, progress_frac)
            except Exception:
                pass

    def force_eject(self, stage: str = "all") -> Dict[str, Any]:
        """
        Force unloads a specific model stage or all models to reclaim VRAM immediately.
        """
        stage_clean = stage.lower().strip()
        ejected = []

        if stage_clean in ("canary", "stage_1_canary", "all"):
            if hasattr(self.transcriber, "unload_model"):
                self._unload_and_log(
                    "Canary-Qwen (Lead Justice)",
                    self.transcriber.unload_model,
                    subsystem_id="stage_1_canary"
                )
                ejected.append("Canary-Qwen")

        if stage_clean in ("whisper", "stage_2_whisper", "all"):
            if hasattr(self.council, "unload_whisper"):
                self._unload_and_log(
                    "Whisper (Cross-Examiner)",
                    self.council.unload_whisper,
                    subsystem_id="stage_2_whisper"
                )
                ejected.append("Whisper")

        if stage_clean in ("conformer", "stage_3a_conformer", "all"):
            if hasattr(self.council, "unload_conformer"):
                self._unload_and_log(
                    "Conformer-CTC (Anchor)",
                    self.council.unload_conformer,
                    subsystem_id="stage_3a_conformer"
                )
                ejected.append("Conformer-CTC")

        if stage_clean in ("parakeet", "stage_3b_parakeet", "all"):
            if hasattr(self.council, "unload_parakeet"):
                self._unload_and_log(
                    "Parakeet-TDT (Transducer)",
                    self.council.unload_parakeet,
                    subsystem_id="stage_3b_parakeet"
                )
                ejected.append("Parakeet-TDT")

        if stage_clean in ("diarizer", "stage_4_diarizer", "all"):
            unload_fn = getattr(self.diarizer, "unload_model", getattr(self.diarizer, "unload", None))
            if unload_fn:
                self._unload_and_log(
                    "Speaker Diarizer",
                    unload_fn,
                    subsystem_id="stage_4_diarizer"
                )
                ejected.append("Diarizer")

        if stage_clean in ("vad", "all"):
            if hasattr(self.vad, "unload_model"):
                self._unload_and_log("Silero VAD", self.vad.unload_model)
                ejected.append("VAD")

        self._reclaim_memory()
        stats = self.vram_manager.get_stats()
        return {
            "success": True,
            "ejected_models": ejected,
            "current_vram_gb": stats.get("reserved_gb", 0.0),
            "free_vram_gb": stats.get("free_gb", 0.0),
            "app_ram_gb": stats.get("proc_ram_used_gb", 0.0)
        }

    def _assign_speaker_to_segment(self, seg_start: float, seg_end: float, speaker_turns: List[SpeakerTurn]) -> str:
        """
        Determines the dominant speaker in a speech interval based on overlap duration.
        """
        if not speaker_turns:
            return "Speaker 0"

        best_speaker = "Speaker 0"
        max_overlap = 0.0

        for turn in speaker_turns:
            # Overlap between [seg_start, seg_end] and [turn.start, turn.end]
            overlap_start = max(seg_start, turn.start)
            overlap_end = min(seg_end, turn.end)
            overlap = max(0.0, overlap_end - overlap_start)

            if overlap > max_overlap:
                max_overlap = overlap
                best_speaker = turn.speaker

        return best_speaker

    def _reconcile_segments_with_speaker_turns(
        self,
        speech_segments: List[SpeechSegment],
        speaker_turns: List[SpeakerTurn],
        min_duration: float = 0.8
    ) -> List[SpeechSegment]:
        """
        Sub-splits monolithic VAD speech segments at speaker turn boundaries.
        Prevents continuous multi-speaker conversations from collapsing into a single speaker.
        """
        if not speaker_turns:
            for s in speech_segments:
                s.speaker = "Speaker 0"
            return speech_segments

        # If only 1 speaker detected across entire file, assign and keep segments
        unique_speakers = {t.speaker for t in speaker_turns}
        if len(unique_speakers) <= 1:
            spk = next(iter(unique_speakers)) if unique_speakers else "Speaker 0"
            for s in speech_segments:
                s.speaker = spk
            return speech_segments

        reconciled: List[SpeechSegment] = []
        for seg in speech_segments:
            # Find all speaker turns that overlap with this VAD segment
            overlapping = [
                t for t in speaker_turns
                if max(seg.start, t.start) < min(seg.end, t.end)
            ]
            if not overlapping:
                seg.speaker = self._assign_speaker_to_segment(seg.start, seg.end, speaker_turns)
                reconciled.append(seg)
                continue

            # If all overlapping turns are the same speaker, no sub-splitting needed
            turn_speakers = {t.speaker for t in overlapping}
            if len(turn_speakers) == 1:
                seg.speaker = overlapping[0].speaker
                reconciled.append(seg)
                continue

            # Multiple speakers in this VAD chunk: partition chunk at speaker boundaries
            curr_start = seg.start
            for i, t in enumerate(overlapping):
                is_last = (i == len(overlapping) - 1)
                if is_last:
                    sub_end = seg.end
                else:
                    next_t = overlapping[i + 1]
                    if t.speaker == next_t.speaker:
                        continue  # Same speaker continues
                    midpoint = (t.end + next_t.start) / 2.0
                    sub_end = max(t.start + min_duration, min(midpoint, next_t.start))
                    sub_end = min(seg.end, max(curr_start + min_duration, sub_end))

                dur = round(sub_end - curr_start, 2)
                if dur >= min_duration or is_last:
                    reconciled.append(SpeechSegment(
                        start=round(curr_start, 2),
                        end=round(sub_end, 2),
                        duration=dur,
                        speaker=t.speaker
                    ))
                    curr_start = sub_end
                if curr_start >= seg.end:
                    break

        # Coalesce contiguous segments of the same speaker if gap < 0.5s
        coalesced: List[SpeechSegment] = []
        for s in reconciled:
            if coalesced and coalesced[-1].speaker == s.speaker and (s.start - coalesced[-1].end) < 0.5:
                coalesced[-1].end = s.end
                coalesced[-1].duration = round(coalesced[-1].end - coalesced[-1].start, 2)
            else:
                coalesced.append(s)

        return coalesced if coalesced else speech_segments

    def process_file(
        self,
        file_path: str | Path,
        enable_diarization: bool = True,
        enable_enhancer: bool = True,
        enable_ambiguity_resolver: bool = True,
        enable_council: bool = True,
        council_mode: str = "sequential",
        speaker_aliases: Optional[Dict[str, str]] = None,
        progress_callback: Optional[Callable[[str, float, Optional[Dict[str, Any]]], None]] = None,
        pause_event: Optional[any] = None,
        stop_event: Optional[any] = None,
        output_orig_path: Optional[str | Path] = None,
        output_processed_path: Optional[str | Path] = None,
        chunks_dir: Optional[str | Path] = None,
        enable_lufs_norm: Optional[bool] = None,
        target_lufs: Optional[float] = None,
        chunk_overlap_s: Optional[float] = None,
        enable_boundary_dedup: Optional[bool] = None,
        glossary: Optional[List[str]] = None,
        attention_backend: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Processes an audio file end-to-end with EBU R128 LUFS normalization (1.2.A), SoX VHQ
        resampling (1.2.B), 500ms sliding chunk overlap & boundary deduplication (1.3.A),
        GPU enhancement, ambiguity slowdown, and pause/stop support.
        Exports model-ingested processed waveform and chunk samples for synchronized audio comparison.
        """
        start_time = time.time()
        file_path = Path(file_path).resolve()

        active_glossary = list(glossary) if glossary is not None else self.custom_glossary
        if attention_backend is not None and attention_backend != self.attention_backend:
            self.attention_backend = attention_backend
            if hasattr(self.transcriber, "attention_backend"):
                self.transcriber.attention_backend = attention_backend
            if hasattr(self.council, "attention_backend"):
                self.council.attention_backend = attention_backend

        use_lufs = self.enable_lufs_normalization if enable_lufs_norm is None else enable_lufs_norm
        use_target_lufs = self.target_lufs if target_lufs is None else target_lufs
        use_overlap_s = self.chunk_overlap_s if chunk_overlap_s is None else chunk_overlap_s
        use_dedup = self.enable_boundary_dedup if enable_boundary_dedup is None else enable_boundary_dedup

        def check_stop():
            if stop_event and stop_event.is_set():
                raise InterruptedError("Transcription stopped by user.")

        def report(stage: str, frac: float, current_seg: Optional[Dict[str, Any]] = None):
            check_stop()
            try:
                self.supervisor.sample_telemetry()
            except Exception:
                pass
            if progress_callback:
                progress_callback(stage, frac, current_seg)

        try:
            # 1. Load, resample (1.2.B), and normalize loudness (1.2.A)
            t_prep_start = time.time()
            self.supervisor.record_stage_start("audio_preprocessor")
            report("Loading audio & analyzing pre-flight health...", 0.04)
            waveform, sr, duration = self.audio_loader.load_audio(
                file_path,
                enable_lufs_norm=use_lufs,
                target_lufs=use_target_lufs
            )
            check_stop()

            health_report = getattr(self.audio_loader, "last_health_report", None)
            health_dict = health_report.to_dict() if health_report else {}
            if health_report:
                h_msg = f"Pre-Flight Health: {health_report.health_grade} | SNR: {health_report.snr_db}dB | LUFS: {health_report.lufs} | Clip: {health_report.clipping_pct}% | Phase: ρ={health_report.phase_correlation}"
                if health_report.phase_inverted:
                    h_msg += " (Phase Remedied)"
                report(h_msg, 0.06, {"audio_health": health_dict})

            # Save synchronized original 16kHz waveform for Track 1 playback if requested
            saved_orig_path = None
            if output_orig_path:
                out_orig_p = Path(output_orig_path).resolve()
                out_orig_p.parent.mkdir(parents=True, exist_ok=True)
                sf.write(str(out_orig_p), waveform.squeeze(0).cpu().numpy(), sr, subtype="PCM_16")
                saved_orig_path = str(out_orig_p)

            # 2. GPU Speech Enhancer & Noise Filter
            if enable_enhancer:
                report("Applying GPU speech noise filter & vocal amplifier...", 0.08)
                waveform = self.enhancer.enhance(waveform, boost_level=self.vocal_boost_level)
                check_stop()
            self._reclaim_memory()
            self.supervisor.record_stage_end("audio_preprocessor", time.time() - t_prep_start, duration)

            # Save processed waveform for UI playback and synchronization
            saved_processed_path = None
            if output_processed_path:
                out_p = Path(output_processed_path).resolve()
                out_p.parent.mkdir(parents=True, exist_ok=True)
                sf.write(str(out_p), waveform.squeeze(0).cpu().numpy(), sr, subtype="PCM_16")
                saved_processed_path = str(out_p)
            self._reclaim_memory()

            # 3. VAD speech segmentation
            report("Performing Voice Activity Detection (VAD)...", 0.16)
            speech_segments = self.vad.segment(waveform, duration)
            # Unload VAD immediately with explicit log
            self._unload_and_log("Silero-VAD Segmenter", self.vad.unload_model, report, 0.17)
            check_stop()

            if not speech_segments:
                print("[Pipeline Warning] No speech detected in file.")
                return {
                    "file_path": str(file_path),
                    "file_name": file_path.name,
                    "duration": round(duration, 2),
                    "segments": [],
                    "full_text": "",
                    "processed_audio_path": saved_processed_path,
                    "chunks_dir": str(chunks_dir) if chunks_dir else None,
                    "vram_stats": self.vram_manager.get_stats(),
                    "elapsed_seconds": round(time.time() - start_time, 2)
                }

            # 4. Speaker Diarization
            speaker_turns = []
            if enable_diarization:
                t_diar_start = time.time()
                self.supervisor.record_stage_start("stage_4_diarizer", self.diarizer.__class__.__name__)
                report("Performing Speaker Diarization...", 0.24)
                try:
                    speaker_turns = self.diarizer.diarize(waveform, sr)
                except Exception as e:
                    print(f"[Diarization Warning] Diarization failed ({e}), falling back to single speaker.")
                    speaker_turns = [SpeakerTurn(start=0.0, end=duration, speaker="Speaker 0")]
                finally:
                    self.supervisor.record_stage_end("stage_4_diarizer", time.time() - t_diar_start, duration)
                    # Crucial RAM optimization: unload diarizer model immediately with explicit log
                    diar_name = f"Speaker Diarizer ({self.diarizer.__class__.__name__})"
                    self._unload_and_log(
                        diar_name,
                        getattr(self.diarizer, "unload_model", getattr(self.diarizer, "unload", lambda: None)),
                        report,
                        0.25,
                        subsystem_id="stage_4_diarizer"
                    )
            else:
                speaker_turns = [SpeakerTurn(start=0.0, end=duration, speaker="Speaker 0")]

            check_stop()

            # Reconcile speech segments with speaker turns if diarization is enabled
            if enable_diarization and speaker_turns:
                speech_segments = self._reconcile_segments_with_speaker_turns(speech_segments, speaker_turns)
            else:
                for seg in speech_segments:
                    seg.speaker = self._assign_speaker_to_segment(seg.start, seg.end, speaker_turns)

            # 5. Multi-Model Inference Council Transcription
            total_chunks = len(speech_segments)
            transcribed_segments = []

            if enable_council:
                if council_mode == "sequential":
                    t_c1_start = time.time()
                    self.supervisor.record_stage_start("stage_1_canary", self.transcriber.model_name)
                    report("Pass 1/3: Canary-Qwen Context Pass...", 0.38)
                    canary_hyps = []
                    chunk_wavs = []

                    # 1. Prepare temporary chunk files and run Canary-Qwen
                    for idx, seg in enumerate(speech_segments):
                        check_stop()
                        if pause_event:
                            while not pause_event.is_set():
                                if stop_event and stop_event.is_set():
                                    raise InterruptedError("Transcription stopped by user.")
                                time.sleep(0.2)

                        # 1.3.A: 500ms Sliding Window Context Padding across chunk boundaries
                        c_audio_start = max(0.0, seg.start - (use_overlap_s if idx > 0 else 0.0))
                        c_audio_end = min(duration, seg.end + (use_overlap_s if idx < total_chunks - 1 else 0.0))
                        start_sample = max(0, int(c_audio_start * sr))
                        end_sample = min(waveform.shape[1], int(c_audio_end * sr))
                        chunk_slice = waveform[:, start_sample:end_sample]

                        c_file = Path(tempfile.NamedTemporaryFile(suffix=f"_seq_{idx}.wav", delete=False).name)
                        sf.write(str(c_file), chunk_slice.squeeze(0).cpu().numpy(), sr, subtype="PCM_16")
                        chunk_wavs.append(c_file)

                        try:
                            c_h = self.transcriber.transcribe_waveform_chunk(chunk_slice, sr=sr, glossary=active_glossary)
                        except Exception as e:
                            print(f"[Pipeline Warning] Canary chunk {idx} error: {e}")
                            c_h = ""
                        canary_hyps.append(c_h)

                        frac = 0.38 + ((idx + 1) / total_chunks) * 0.18
                        report(f"Pass 1/3 (Canary-Qwen): Chunk {idx + 1}/{total_chunks}", frac)

                    self.supervisor.record_stage_end("stage_1_canary", time.time() - t_c1_start, duration)

                    # Unload Canary-Qwen with explicit log to reclaim VRAM for Pass 2
                    canary_display = self.transcriber.model_name.split("/")[-1].title()
                    self._unload_and_log(
                        f"{canary_display} (Lead Justice)",
                        getattr(self.transcriber, "unload_model", lambda: None),
                        report,
                        0.56,
                        subsystem_id="stage_1_canary"
                    )

                    # 2. Pass 2/3: Whisper Cross-Examination (Batched)
                    t_w_start = time.time()
                    self.supervisor.record_stage_start("stage_2_whisper", str(self.council.whisper_model_id))
                    report("Pass 2/3: Whisper Cross-Examination (Batched)...", 0.56)
                    check_stop()
                    try:
                        base_w = 2 if "large" in str(self.council.whisper_model_id).lower() else 4
                        w_batch = self.supervisor.get_suggested_batch_size("whisper", base_w)
                        def on_whisper_prog(completed: int, total: int, ratio: float):
                            check_stop()
                            frac = 0.56 + ratio * 0.18
                            batch_num = int(np.ceil(completed / w_batch))
                            tot_batches = int(np.ceil(total / w_batch))
                            report(f"Pass 2/3 (Whisper): Chunk {completed}/{total} (Batch {batch_num}/{tot_batches})", frac)

                        whisper_hyps = self.council.transcribe_batch_whisper(chunk_wavs, batch_size=w_batch, progress_cb=on_whisper_prog, glossary=active_glossary)
                    except Exception as e:
                        print(f"[Pipeline Warning] Batched Whisper error ({e}), falling back to sequential...")
                        whisper_hyps = []
                        for idx, c_wav in enumerate(chunk_wavs):
                            check_stop()
                            try:
                                wh = self.council.transcribe_with_whisper(c_wav, glossary=active_glossary)
                            except Exception:
                                wh = ""
                            whisper_hyps.append(wh)
                            frac = 0.56 + ((idx + 1) / total_chunks) * 0.18
                            report(f"Pass 2/3 (Whisper): Chunk {idx + 1}/{total_chunks}", frac)

                    self.supervisor.record_stage_end("stage_2_whisper", time.time() - t_w_start, duration)

                    # Unload Whisper with explicit log
                    whisper_display = self.council.whisper_model_id.split("/")[-1].title()
                    self._unload_and_log(
                        f"{whisper_display} (Cross-Examiner)",
                        self.council.unload_whisper,
                        report,
                        0.74,
                        subsystem_id="stage_2_whisper"
                    )

                    # 3. Pass 3/3: Acoustic Anchor & Transducer Verification (Staged, Batched)
                    # Phase 3A: Conformer-CTC Acoustic Anchor
                    t_c_start = time.time()
                    self.supervisor.record_stage_start("stage_3a_conformer", str(self.council.conformer_model_id))
                    report("Pass 3/3 (Phase A): Conformer-CTC Acoustic Anchor (Batched)...", 0.74)
                    check_stop()
                    try:
                        base_c = 2 if "xlarge" in str(self.council.conformer_model_id).lower() else 4
                        c_batch = self.supervisor.get_suggested_batch_size("conformer", base_c)
                        def on_conformer_prog(completed: int, total: int, ratio: float):
                            check_stop()
                            frac = 0.74 + ratio * 0.07
                            batch_num = int(np.ceil(completed / c_batch))
                            tot_batches = int(np.ceil(total / c_batch))
                            report(f"Pass 3A (Conformer): Chunk {completed}/{total} (Batch {batch_num}/{tot_batches})", frac)

                        ctc_hyps = self.council.transcribe_batch_conformer(chunk_wavs, batch_size=c_batch, progress_cb=on_conformer_prog)
                    except Exception as e:
                        print(f"[Pipeline Warning] Batched Conformer error ({e}), falling back to sequential...")
                        ctc_hyps = []
                        for idx, c_wav in enumerate(chunk_wavs):
                            check_stop()
                            try:
                                ctc_h = self.council.transcribe_with_conformer(c_wav)
                            except Exception:
                                ctc_h = ""
                            ctc_hyps.append(ctc_h)
                            frac = 0.74 + ((idx + 1) / total_chunks) * 0.07
                            report(f"Pass 3A (Conformer): Chunk {idx + 1}/{total_chunks}", frac)

                    self.supervisor.record_stage_end("stage_3a_conformer", time.time() - t_c_start, duration)

                    # Immediately unload Conformer before loading Parakeet (prevents VRAM co-location)
                    conf_display = self.council.conformer_model_id.split("/")[-1].title()
                    self._unload_and_log(
                        f"{conf_display} (Acoustic Anchor)",
                        self.council.unload_conformer,
                        report,
                        0.81,
                        subsystem_id="stage_3a_conformer"
                    )

                    # Phase 3B: Parakeet-TDT Transducer Verification
                    t_p_start = time.time()
                    self.supervisor.record_stage_start("stage_3b_parakeet", str(self.council.parakeet_model_id))
                    report("Pass 3/3 (Phase B): Parakeet-TDT Transducer Verification (Batched)...", 0.81)
                    check_stop()
                    try:
                        p_batch = self.supervisor.get_suggested_batch_size("parakeet", 8)
                        def on_parakeet_prog(completed: int, total: int, ratio: float):
                            check_stop()
                            frac = 0.81 + ratio * 0.07
                            batch_num = int(np.ceil(completed / p_batch))
                            tot_batches = int(np.ceil(total / p_batch))
                            report(f"Pass 3B (Parakeet): Chunk {completed}/{total} (Batch {batch_num}/{tot_batches})", frac)

                        parakeet_hyps = self.council.transcribe_batch_parakeet(chunk_wavs, batch_size=p_batch, progress_cb=on_parakeet_prog)
                    except Exception as e:
                        print(f"[Pipeline Warning] Batched Parakeet error ({e}), falling back to sequential...")
                        parakeet_hyps = []
                        for idx, c_wav in enumerate(chunk_wavs):
                            check_stop()
                            try:
                                pk_h = self.council.transcribe_with_parakeet(c_wav)
                            except Exception:
                                pk_h = ""
                            parakeet_hyps.append(pk_h)
                            frac = 0.81 + ((idx + 1) / total_chunks) * 0.07
                            report(f"Pass 3B (Parakeet): Chunk {idx + 1}/{total_chunks}", frac)

                    self.supervisor.record_stage_end("stage_3b_parakeet", time.time() - t_p_start, duration)

                    # Unload Parakeet transducer
                    pk_display = self.council.parakeet_model_id.split("/")[-1].title()
                    self._unload_and_log(
                        f"{pk_display} (Transducer)",
                        self.council.unload_parakeet,
                        report,
                        0.88,
                        subsystem_id="stage_3b_parakeet"
                    )

                    # 4. Council Consensus Synthesis & Disputed Chunk Identification
                    report("Adjudicating Supreme Council Consensus...", 0.88)
                    deliberations = []
                    disputed_indices = []
                    for idx, seg in enumerate(speech_segments):
                        start_sample = max(0, int(seg.start * sr))
                        end_sample = min(waveform.shape[1], int(seg.end * sr))
                        chunk_slice = waveform[:, start_sample:end_sample]
                        c_wav = chunk_wavs[idx]

                        delib = self.council.synthesize_deliberation(
                            canary_text=canary_hyps[idx],
                            whisper_text=whisper_hyps[idx],
                            conformer_text=ctc_hyps[idx],
                            parakeet_text=parakeet_hyps[idx] if parakeet_hyps[idx] else None,
                            audio_path=c_wav,
                            waveform=chunk_slice,
                            seg_idx=idx,
                            chunks_dir=Path(chunks_dir) if chunks_dir else None,
                            sample_rate=sr,
                            glossary=active_glossary
                        )
                        deliberations.append(delib)
                        if delib.needs_human_review or delib.consensus_score < 0.85:
                            disputed_indices.append(idx)

                    # Stage 5: Audex-2B Supreme Audio Adjudicator (if enabled, adjudicates all chunks)
                    enable_audex = getattr(self, "enable_audex_adjudicator", getattr(self.config, "enable_audex_adjudicator", False))
                    if enable_audex and speech_segments:
                        t_audex_start = time.time()
                        audex_id = getattr(self, "audex_model_id", getattr(self.config, "audex_model_id", "nvidia/Nemotron-Labs-Audex-2B"))
                        self.supervisor.record_stage_start("stage_5_audex", audex_id)
                        report(f"Stage 5: Audex-2B Adjudicating all {len(speech_segments)} segments...", 0.88)
                        try:
                            from .asr.audex import AudexAdjudicator
                            audex = AudexAdjudicator(model_id=audex_id, device=self.device)
                            audex.load_model()
                            for d_count, seg in enumerate(speech_segments):
                                check_stop()
                                delib = deliberations[d_count]
                                start_sample = max(0, int(seg.start * sr))
                                end_sample = min(waveform.shape[1], int(seg.end * sr))
                                chunk_slice = waveform[:, start_sample:end_sample]

                                verdict, reasoning = audex.adjudicate_chunk(
                                    audio_path_or_slice=chunk_slice,
                                    votes=delib.votes,
                                    previous_verdict=delib.verdict,
                                    sample_rate=sr
                                )
                                if verdict:
                                    delib.verdict = verdict
                                delib.agreement_type = "AUDEX_ADJUDICATED"
                                if reasoning:
                                    delib.deliberation_notes = (delib.deliberation_notes + "\n" + reasoning).strip()
                                delib.needs_human_review = False
                                frac = 0.88 + ((d_count + 1) / len(speech_segments)) * 0.07
                                report(f"Stage 5 (Audex): Adjudicated chunk {d_count + 1}/{len(speech_segments)}", frac)

                            self._unload_and_log(
                                "Audex-2B Adjudicator",
                                audex.unload,
                                report,
                                0.95,
                                subsystem_id="stage_5_audex"
                            )
                        except Exception as e:
                            print(f"[Pipeline Warning] Audex Stage 5 adjudication error: {e}")
                        finally:
                            self.supervisor.record_stage_end("stage_5_audex", time.time() - t_audex_start, duration)

                    for idx, seg in enumerate(speech_segments):
                        delib = deliberations[idx]
                        verdict_text = delib.verdict
                        if use_dedup and transcribed_segments:
                            if seg.start - transcribed_segments[-1]["end"] < 0.4:
                                verdict_text = deduplicate_chunk_boundary(
                                    transcribed_segments[-1]["text"],
                                    verdict_text
                                )
                        seg_dict = {
                            "start": round(seg.start, 2),
                            "end": round(seg.end, 2),
                            "duration": round(seg.duration, 2),
                            "speaker": getattr(seg, "speaker", "Speaker 0"),
                            "text": verdict_text,
                            "council": delib.to_dict(),
                            "needs_review": delib.needs_human_review,
                            "ambiguity_score": round(1.0 - delib.consensus_score, 3),
                            "loop_circuit_breaker_tripped": delib.loop_circuit_breaker_tripped,
                            "confidence_decomposition": delib.confidence_decomposition
                        }
                        transcribed_segments.append(seg_dict)
                        frac = 0.95 + ((idx + 1) / total_chunks) * 0.04
                        report(f"Deliberated chunk {idx + 1}/{total_chunks}: {delib.agreement_type}", frac, seg_dict)

                    for cw in chunk_wavs:
                        cw.unlink(missing_ok=True)

                else:
                    # Concurrent 1-pass streaming mode
                    report("Deliberating with Multi-Model Council (Concurrent)...", 0.38)
                    for idx, seg in enumerate(speech_segments):
                        check_stop()
                        if pause_event:
                            while not pause_event.is_set():
                                if stop_event and stop_event.is_set():
                                    raise InterruptedError("Transcription stopped by user.")
                                time.sleep(0.2)

                        # 1.3.A: 500ms Sliding Window Context Padding across chunk boundaries
                        c_audio_start = max(0.0, seg.start - (use_overlap_s if idx > 0 else 0.0))
                        c_audio_end = min(duration, seg.end + (use_overlap_s if idx < total_chunks - 1 else 0.0))
                        start_sample = max(0, int(c_audio_start * sr))
                        end_sample = min(waveform.shape[1], int(c_audio_end * sr))
                        chunk_slice = waveform[:, start_sample:end_sample]

                        # Juror 1: Canary-Qwen
                        try:
                            canary_h = self.transcriber.transcribe_waveform_chunk(chunk_slice, sr=sr, glossary=active_glossary)
                        except Exception as e:
                            print(f"[Pipeline Warning] Canary chunk {idx} error: {e}")
                            canary_h = ""

                        # Council Deliberation (Whisper + Conformer-CTC + Auditor)
                        delib = self.council.deliberate_waveform_segment(
                            waveform_slice=chunk_slice,
                            sr=sr,
                            canary_text=canary_h,
                            seg_idx=idx,
                            chunks_dir=Path(chunks_dir) if chunks_dir else None,
                            glossary=active_glossary
                        )

                        verdict_text = delib.verdict
                        if use_dedup and transcribed_segments:
                            if seg.start - transcribed_segments[-1]["end"] < 0.4:
                                verdict_text = deduplicate_chunk_boundary(
                                    transcribed_segments[-1]["text"],
                                    verdict_text
                                )

                        seg_dict = {
                            "start": round(seg.start, 2),
                            "end": round(seg.end, 2),
                            "duration": round(seg.duration, 2),
                            "speaker": getattr(seg, "speaker", "Speaker 0"),
                            "text": verdict_text,
                            "council": delib.to_dict(),
                            "needs_review": delib.needs_human_review,
                            "ambiguity_score": round(1.0 - delib.consensus_score, 3),
                            "loop_circuit_breaker_tripped": delib.loop_circuit_breaker_tripped,
                            "confidence_decomposition": delib.confidence_decomposition
                        }
                        transcribed_segments.append(seg_dict)

                        if (idx + 1) % 5 == 0:
                            self.vram_manager.clear_cache()

                        frac = 0.38 + ((idx + 1) / total_chunks) * 0.55
                        report(f"Council deliberated chunk {idx + 1}/{total_chunks}: {delib.agreement_type}", frac, seg_dict)

            else:
                # Fallback: Canary-Qwen solo transcription
                report("Transcribing speech chunks with Canary-Qwen-2.5B...", 0.38)

                def asr_progress(current_idx: int, total_idx: int, seg_dict: Dict[str, Any]):
                    check_stop()
                    frac = 0.38 + (current_idx / total_idx) * 0.50
                    report(f"Transcribed chunk {current_idx}/{total_idx}", frac, seg_dict)

                transcribed_segments = self.transcriber.transcribe_waveform_segments(
                    waveform=waveform,
                    segments=speech_segments,
                    sr=sr,
                    progress_callback=asr_progress,
                    pause_event=pause_event,
                    stop_event=stop_event,
                    glossary=active_glossary
                )

                check_stop()

                if enable_ambiguity_resolver:
                    report("Evaluating ambiguity & auto-slowing tricky audio frames...", 0.90)
                    for idx, seg in enumerate(transcribed_segments):
                        check_stop()
                        seg = self.ambiguity_resolver.evaluate_and_resolve(
                            waveform=waveform,
                            seg=seg,
                            transcribe_fn=lambda p: self.transcriber.transcribe_chunk(p, glossary=active_glossary),
                            sr=sr,
                            output_chunks_dir=chunks_dir,
                            seg_idx=idx
                        )

            check_stop()

            # Unload models after transcription is complete to free VRAM
            if hasattr(self, "council") and hasattr(self.council, "unload_members"):
                self.council.unload_members()
            if hasattr(self.transcriber, "unload_model"):
                self.transcriber.unload_model()
            self.vram_manager.clear_cache()

            # 7. Format & Finalize
            report("Finalizing transcript...", 0.98)
            formatted_text = TranscriptExporter.export_text(
                transcribed_segments,
                speaker_aliases=speaker_aliases,
                include_timestamps=True,
                include_speakers=True
            )

            elapsed = round(time.time() - start_time, 2)
            vram_stats = self.vram_manager.get_stats()
            report("Complete", 1.0, None)

            return {
                "file_path": str(file_path),
                "file_name": file_path.name,
                "duration": round(duration, 2),
                "segments": transcribed_segments,
                "full_text": formatted_text,
                "orig_audio_path": saved_orig_path,
                "processed_audio_path": saved_processed_path,
                "chunks_dir": str(chunks_dir) if chunks_dir else None,
                "audio_health": health_dict,
                "vram_stats": vram_stats,
                "elapsed_seconds": elapsed
            }
        finally:
            if hasattr(self, "council") and hasattr(self.council, "unload_members"):
                self.council.unload_members()
            if hasattr(self.transcriber, "unload_model"):
                self.transcriber.unload_model()
            self.vram_manager.clear_cache()
            if hasattr(self, "supervisor"):
                self.supervisor.finalize_pipeline()

    def stream_transcribe(
        self,
        file_path: str | Path,
        model_choice: str = "canary",
        enable_enhancer: bool = False,
        min_chunk_duration: float = 3.0,
        max_chunk_duration: float = 12.0,
        speaker_alias: Optional[str] = None,
        stop_event: Optional[Any] = None,
        glossary: Optional[List[str]] = None,
        attention_backend: Optional[str] = None
    ) -> Iterator[Dict[str, Any]]:
        """
        Yields progressive transcription events for an audio file.
        Enables low-latency live streaming output to UI clients via SSE.
        """
        start_time = time.time()
        file_path = Path(file_path).resolve()
        if not file_path.exists():
            yield {"event": "error", "data": {"error": f"Audio file not found: {file_path}"}}
            return

        active_glossary = list(glossary) if glossary is not None else self.custom_glossary
        if attention_backend is not None and attention_backend != self.attention_backend:
            self.attention_backend = attention_backend
            if hasattr(self.transcriber, "attention_backend"):
                self.transcriber.attention_backend = attention_backend
            if hasattr(self.council, "attention_backend"):
                self.council.attention_backend = attention_backend

        def is_stopped() -> bool:
            return stop_event is not None and stop_event.is_set()

        try:
            # 1. Load audio
            waveform, sr, duration = self.audio_loader.load_audio(file_path)
            if is_stopped():
                yield {"event": "stopped", "data": {"message": "Transcription stopped by user"}}
                return

            if enable_enhancer:
                waveform = self.enhancer.enhance(waveform, boost_level=self.vocal_boost_level)
                if is_stopped():
                    yield {"event": "stopped", "data": {"message": "Transcription stopped by user"}}
                    return

            health_report = getattr(self.audio_loader, "last_health_report", None)
            health_dict = health_report.to_dict() if health_report else {}

            yield {
                "event": "init",
                "data": {
                    "file_path": str(file_path),
                    "file_name": file_path.name,
                    "duration": round(duration, 2),
                    "sample_rate": sr,
                    "model": model_choice,
                    "audio_health": health_dict
                }
            }

            # 2. Pseudo-stream chunker with 500ms sliding window overlap (1.3.A)
            chunker = PseudoStreamChunker(
                sample_rate=sr,
                min_chunk_duration=min_chunk_duration,
                max_chunk_duration=max_chunk_duration,
                overlap_duration=self.chunk_overlap_s,
                device=self.device
            )

            segments = []
            full_text_parts = []

            for chunk in chunker.stream_chunks(waveform, sample_rate=sr):
                if is_stopped():
                    yield {"event": "stopped", "data": {"message": "Transcription stopped by user"}}
                    return

                t_chunk_start = time.time()
                yield {
                    "event": "chunk_start",
                    "data": {
                        "chunk_idx": chunk.chunk_idx,
                        "start": chunk.start,
                        "end": chunk.end,
                        "duration": chunk.duration,
                        "is_final": chunk.is_final
                    }
                }

                # 3. Transcribe chunk
                text = ""
                try:
                    if model_choice.lower() == "whisper" and hasattr(self.council, "transcribe_with_whisper"):
                        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                            sf.write(tf.name, chunk.waveform.squeeze(0).cpu().numpy(), sr, subtype="PCM_16")
                            text = self.council.transcribe_with_whisper(tf.name, glossary=active_glossary)
                            Path(tf.name).unlink(missing_ok=True)
                    else:
                        # Default: Canary-Qwen fast pass
                        if hasattr(self.transcriber, "transcribe_waveform_chunk"):
                            text = self.transcriber.transcribe_waveform_chunk(chunk.waveform, sr=sr, glossary=active_glossary)
                        elif hasattr(self.transcriber, "transcribe_chunk"):
                            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                                sf.write(tf.name, chunk.waveform.squeeze(0).cpu().numpy(), sr, subtype="PCM_16")
                                text = self.transcriber.transcribe_chunk(tf.name, glossary=active_glossary)
                                Path(tf.name).unlink(missing_ok=True)
                except Exception as e:
                    print(f"[Stream Warning] Chunk {chunk.chunk_idx} transcription error: {e}")
                    text = ""

                speaker_name = speaker_alias or "Speaker 0"
                raw_text = text.strip()
                cleaned_text = raw_text
                if self.enable_boundary_dedup and segments:
                    cleaned_text = deduplicate_chunk_boundary(segments[-1]["text"], raw_text)

                seg_dict = {
                    "index": chunk.chunk_idx,
                    "start": chunk.start,
                    "end": chunk.end,
                    "duration": chunk.duration,
                    "speaker": speaker_name,
                    "text": cleaned_text
                }
                segments.append(seg_dict)
                if cleaned_text:
                    full_text_parts.append(cleaned_text)

                yield {
                    "event": "chunk_done",
                    "data": {
                        "chunk_idx": chunk.chunk_idx,
                        "start": chunk.start,
                        "end": chunk.end,
                        "duration": chunk.duration,
                        "text": cleaned_text,
                        "speaker": speaker_name,
                        "is_final": chunk.is_final,
                        "elapsed": round(time.time() - t_chunk_start, 3)
                    }
                }

            full_text = " ".join(full_text_parts)
            yield {
                "event": "complete",
                "data": {
                    "full_text": full_text,
                    "segments": segments,
                    "total_chunks": len(segments),
                    "duration": round(duration, 2),
                    "elapsed_seconds": round(time.time() - start_time, 2)
                }
            }
        except Exception as err:
            print(f"[Stream Error] {err}")
            yield {"event": "error", "data": {"error": str(err)}}
