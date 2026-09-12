"""
End-to-end orchestration pipeline for Transcript Suite.
Coordinates Audio Loader -> Diarization -> VAD -> Canary-Qwen ASR -> Speaker Alignment.
"""

from typing import List, Dict, Any, Optional, Callable
from pathlib import Path
import time
import tempfile
import soundfile as sf
import torch
import numpy as np

from .config import config
from .audio.loader import AudioLoader
from .audio.vad import SileroVADSegmenter
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
        vocal_boost_level: Optional[str] = None
    ):
        self.vocal_boost_level = vocal_boost_level or config.vocal_boost_level
        self.audio_loader = AudioLoader(target_sr=config.sample_rate)
        self.enhancer = GPUSpeechEnhancer(
            sample_rate=config.sample_rate,
            device=config.device,
            boost_level=self.vocal_boost_level
        )
        self.ambiguity_resolver = AmbiguityResolver(sample_rate=config.sample_rate)
        self.vad = SileroVADSegmenter(
            sample_rate=config.sample_rate,
            max_chunk_duration=config.max_chunk_duration_s,
            min_chunk_duration=config.min_chunk_duration_s,
            padding_duration=config.vad_padding_s,
            device=config.device
        )
        self.transcriber = CanaryQwenTranscriber(
            model_name=model_name or config.model_name,
            device=config.device,
            dtype=config.dtype
        )
        self.council = ModelCouncil(
            canary_transcriber=self.transcriber,
            whisper_model_id=whisper_model or config.whisper_model,
            parakeet_model_id=parakeet_model or config.parakeet_model,
            conformer_model_id=conformer_model or config.conformer_model,
            device=config.device
        )
        self.vram_manager = VRAMManager()
        self.supervisor = get_subsystem_supervisor()
        
        # Select diarizer route
        active_diarizer = diarizer_type or config.default_diarizer
        if active_diarizer == "pyannote":
            self.diarizer = PyAnnoteDiarizer(hf_token=hf_token or config.hf_token, device=config.device)
        else:
            self.diarizer = NeMoTitaNetDiarizer(model_name=config.nemo_diarizer_model, device=config.device)

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
        chunks_dir: Optional[str | Path] = None
    ) -> Dict[str, Any]:
        """
        Processes an audio file end-to-end with GPU enhancement, ambiguity slowdown, and pause/stop support.
        Exports model-ingested processed waveform and chunk samples for synchronized audio comparison.
        """
        start_time = time.time()
        file_path = Path(file_path).resolve()

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
            # 1. Load and normalize audio
            t_prep_start = time.time()
            self.supervisor.record_stage_start("audio_preprocessor")
            report("Loading audio & converting to 16kHz mono...", 0.04)
            waveform, sr, duration = self.audio_loader.load_audio(file_path)
            check_stop()

            # Save synchronized original 16kHz waveform for Track 1 playback if requested
            saved_orig_path = None
            if output_orig_path:
                out_orig_p = Path(output_orig_path).resolve()
                out_orig_p.parent.mkdir(parents=True, exist_ok=True)
                import soundfile as sf
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
                import soundfile as sf
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

            # Assign speakers to VAD speech segments
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

                        start_sample = max(0, int(seg.start * sr))
                        end_sample = min(waveform.shape[1], int(seg.end * sr))
                        chunk_slice = waveform[:, start_sample:end_sample]

                        c_file = Path(tempfile.NamedTemporaryFile(suffix=f"_seq_{idx}.wav", delete=False).name)
                        sf.write(str(c_file), chunk_slice.squeeze(0).cpu().numpy(), sr, subtype="PCM_16")
                        chunk_wavs.append(c_file)

                        try:
                            c_h = self.transcriber.transcribe_waveform_chunk(chunk_slice, sr=sr)
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

                        whisper_hyps = self.council.transcribe_batch_whisper(chunk_wavs, batch_size=w_batch, progress_cb=on_whisper_prog)
                    except Exception as e:
                        print(f"[Pipeline Warning] Batched Whisper error ({e}), falling back to sequential...")
                        whisper_hyps = []
                        for idx, c_wav in enumerate(chunk_wavs):
                            check_stop()
                            try:
                                wh = self.council.transcribe_with_whisper(c_wav)
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
                        base_c = 8 if "xlarge" in str(self.council.conformer_model_id).lower() else 16
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
                            sample_rate=sr
                        )
                        deliberations.append(delib)
                        if delib.needs_human_review or delib.consensus_score < 0.85:
                            disputed_indices.append(idx)

                    # Stage 5: Audex-2B Supreme Audio Adjudicator (if enabled and disputes exist)
                    enable_audex = getattr(self.config, "enable_audex_adjudicator", False)
                    if enable_audex and disputed_indices:
                        t_audex_start = time.time()
                        audex_id = getattr(self.config, "audex_model_id", "nvidia/Nemotron-Labs-Audex-2B")
                        self.supervisor.record_stage_start("stage_5_audex", audex_id)
                        report(f"Stage 5: Audex-2B Adjudicating {len(disputed_indices)} disputed segments...", 0.90)
                        try:
                            from .asr.audex import AudexAdjudicator
                            audex = AudexAdjudicator(model_id=audex_id, device=self.device)
                            for d_count, d_idx in enumerate(disputed_indices):
                                check_stop()
                                seg = speech_segments[d_idx]
                                delib = deliberations[d_idx]
                                start_sample = max(0, int(seg.start * sr))
                                end_sample = min(waveform.shape[1], int(seg.end * sr))
                                chunk_slice = waveform[:, start_sample:end_sample]

                                verdict, reasoning = audex.adjudicate_chunk(
                                    audio_path_or_slice=chunk_slice,
                                    votes=delib.votes,
                                    previous_verdict=delib.verdict,
                                    sample_rate=sr
                                )
                                delib.verdict = verdict
                                delib.agreement_type = "AUDEX_ADJUDICATED"
                                delib.deliberation_notes = (delib.deliberation_notes + "\n" + reasoning).strip()
                                delib.needs_human_review = False
                                frac = 0.90 + ((d_count + 1) / len(disputed_indices)) * 0.05
                                report(f"Audex adjudicated disputed chunk {d_count + 1}/{len(disputed_indices)}", frac)

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
                        seg_dict = {
                            "start": round(seg.start, 2),
                            "end": round(seg.end, 2),
                            "duration": round(seg.duration, 2),
                            "speaker": getattr(seg, "speaker", "Speaker 0"),
                            "text": delib.verdict,
                            "council": delib.to_dict(),
                            "needs_review": delib.needs_human_review,
                            "ambiguity_score": round(1.0 - delib.consensus_score, 3)
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

                        start_sample = max(0, int(seg.start * sr))
                        end_sample = min(waveform.shape[1], int(seg.end * sr))
                        chunk_slice = waveform[:, start_sample:end_sample]

                        # Juror 1: Canary-Qwen
                        try:
                            canary_h = self.transcriber.transcribe_waveform_chunk(chunk_slice, sr=sr)
                        except Exception as e:
                            print(f"[Pipeline Warning] Canary chunk {idx} error: {e}")
                            canary_h = ""

                        # Council Deliberation (Whisper + Conformer-CTC + Auditor)
                        delib = self.council.deliberate_waveform_segment(
                            waveform_slice=chunk_slice,
                            sr=sr,
                            canary_text=canary_h,
                            seg_idx=idx,
                            chunks_dir=Path(chunks_dir) if chunks_dir else None
                        )

                        seg_dict = {
                            "start": round(seg.start, 2),
                            "end": round(seg.end, 2),
                            "duration": round(seg.duration, 2),
                            "speaker": getattr(seg, "speaker", "Speaker 0"),
                            "text": delib.verdict,
                            "council": delib.to_dict(),
                            "needs_review": delib.needs_human_review,
                            "ambiguity_score": round(1.0 - delib.consensus_score, 3)
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
                    stop_event=stop_event
                )

                check_stop()

                if enable_ambiguity_resolver:
                    report("Evaluating ambiguity & auto-slowing tricky audio frames...", 0.90)
                    for idx, seg in enumerate(transcribed_segments):
                        check_stop()
                        seg = self.ambiguity_resolver.evaluate_and_resolve(
                            waveform=waveform,
                            seg=seg,
                            transcribe_fn=self.transcriber.transcribe_chunk,
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
