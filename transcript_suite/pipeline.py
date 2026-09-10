"""
End-to-end orchestration pipeline for Transcript Suite.
Coordinates Audio Loader -> Diarization -> VAD -> Canary-Qwen ASR -> Speaker Alignment.
"""

from typing import List, Dict, Any, Optional, Callable
from pathlib import Path
import time
import torch

from .config import config
from .audio.loader import AudioLoader
from .audio.vad import SileroVADSegmenter
from .asr.canary import CanaryQwenTranscriber
from .asr.memory import VRAMManager
from .diarization.nemo_titanet import NeMoTitaNetDiarizer
from .diarization.pyannote import PyAnnoteDiarizer
from .diarization.base import SpeakerTurn
from .export import TranscriptExporter


class TranscriptionPipeline:
    def __init__(
        self,
        diarizer_type: str = "nemo",
        hf_token: Optional[str] = None,
        model_name: Optional[str] = None
    ):
        self.audio_loader = AudioLoader(target_sr=config.sample_rate)
        self.vad = SileroVADSegmenter(
            sample_rate=config.sample_rate,
            max_chunk_duration=config.max_chunk_duration_s,
            min_chunk_duration=config.min_chunk_duration_s,
            padding_duration=config.vad_padding_s
        )
        self.transcriber = CanaryQwenTranscriber(model_name=model_name or config.model_name)
        self.vram_manager = VRAMManager()
        
        # Select diarizer route
        if diarizer_type == "pyannote":
            self.diarizer = PyAnnoteDiarizer(hf_token=hf_token or config.hf_token)
        else:
            self.diarizer = NeMoTitaNetDiarizer(model_name=config.nemo_diarizer_model)

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
        speaker_aliases: Optional[Dict[str, str]] = None,
        progress_callback: Optional[Callable[[str, float, Optional[Dict[str, Any]]], None]] = None,
        pause_event: Optional[any] = None,
        stop_event: Optional[any] = None
    ) -> Dict[str, Any]:
        """
        Processes an audio file end-to-end with pause/stop support and memory optimization.
        """
        start_time = time.time()
        file_path = Path(file_path).resolve()

        def check_stop():
            if stop_event and stop_event.is_set():
                raise InterruptedError("Transcription stopped by user.")

        def report(stage: str, frac: float, current_seg: Optional[Dict[str, Any]] = None):
            check_stop()
            if progress_callback:
                progress_callback(stage, frac, current_seg)

        # 1. Load and normalize audio
        report("Loading audio & converting to 16kHz mono...", 0.05)
        waveform, sr, duration = self.audio_loader.load_audio(file_path)
        check_stop()

        # 2. VAD speech segmentation
        report("Performing Voice Activity Detection (VAD)...", 0.15)
        speech_segments = self.vad.segment(waveform, duration)
        check_stop()

        if not speech_segments:
            return {
                "file_path": str(file_path),
                "duration": duration,
                "segments": [],
                "full_text": "",
                "elapsed_seconds": round(time.time() - start_time, 2)
            }

        # 3. Speaker Diarization
        speaker_turns: List[SpeakerTurn] = []
        if enable_diarization:
            report("Running speaker diarization...", 0.30)
            try:
                speaker_turns = self.diarizer.diarize(waveform, sr)
            except Exception as e:
                print(f"[Diarization Warning] Diarization failed ({e}), falling back to single speaker.")
                speaker_turns = [SpeakerTurn(start=0.0, end=duration, speaker="Speaker 0")]
            finally:
                # Crucial RAM optimization: unload diarizer model immediately
                if hasattr(self.diarizer, "unload_model"):
                    self.diarizer.unload_model()
                self.vram_manager.clear_cache()
        else:
            speaker_turns = [SpeakerTurn(start=0.0, end=duration, speaker="Speaker 0")]

        check_stop()

        # Assign speakers to VAD speech segments
        for seg in speech_segments:
            seg.speaker = self._assign_speaker_to_segment(seg.start, seg.end, speaker_turns)

        # 4. Canary-Qwen ASR Transcription
        report("Transcribing speech chunks with Canary-Qwen-2.5B...", 0.40)
        total_chunks = len(speech_segments)

        def asr_progress(current_idx: int, total_idx: int, seg_dict: Dict[str, Any]):
            check_stop()
            frac = 0.40 + (current_idx / total_idx) * 0.55
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

        # 5. Format & Finalize
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
            "vram_stats": vram_stats,
            "elapsed_seconds": elapsed
        }
