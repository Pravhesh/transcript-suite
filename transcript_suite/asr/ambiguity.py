"""
Adaptive Ambiguity Resolver with pitch-preserved audio slowdown and minimal human intervention.
Identifies tricky/inaudible frames, slows them down to 0.75x for re-transcription,
and escalates to human review only as a minimal last resort.
"""

from typing import Dict, Any, Tuple, Optional
from pathlib import Path
import re
import subprocess
import tempfile
import torch
import soundfile as sf


class AmbiguityResolver:
    def __init__(
        self,
        ambiguity_threshold: float = 0.45,
        slow_factor: float = 0.75,
        sample_rate: int = 16000
    ):
        self.ambiguity_threshold = ambiguity_threshold
        self.slow_factor = slow_factor
        self.sample_rate = sample_rate

    def calculate_ambiguity(self, text: str, duration: float) -> float:
        """
        Computes an ambiguity score between 0.0 (high certainty) and 1.0 (highly ambiguous).
        """
        text = text.strip()
        if not text:
            # Silence or inaudible utterance for non-trivial duration
            return 0.8 if duration > 1.2 else 0.2

        score = 0.0
        words = text.split()
        num_words = len(words)

        # 1. Inaudible / hesitation / glitch tokens
        ambiguous_tokens = ["???", "[inaudible]", "(inaudible)", "[unintelligible]", "...", "umm", "uhh"]
        for tok in ambiguous_tokens:
            if tok in text.lower():
                score += 0.35

        # 2. Severe repetition hallucination (e.g. "I think I think I think")
        if num_words >= 4:
            word_counts = {}
            for w in words:
                w_lower = w.lower()
                word_counts[w_lower] = word_counts.get(w_lower, 0) + 1
            max_repeat = max(word_counts.values())
            if max_repeat / num_words > 0.45:
                score += 0.4

        # 3. Speech Rate Anomaly (too fast or too slow)
        if duration > 0.5:
            wps = num_words / duration  # words per second
            if wps < 0.4 and duration > 2.0:  # e.g. only 1 word spoken in 4 seconds
                score += 0.25
            elif wps > 6.0:  # unnaturally rapid gibberish
                score += 0.35

        # 4. Excessive punctuation or special characters
        non_alpha = len(re.findall(r"[^\w\s]", text))
        if len(text) > 0 and (non_alpha / len(text)) > 0.25:
            score += 0.2

        return min(1.0, round(score, 2))

    def time_stretch_audio(self, input_wav: Path, output_wav: Path, speed: float = 0.75) -> Path:
        """
        Slows down an audio chunk by speed factor (e.g. 0.75x) with pitch preservation using ffmpeg atempo.
        """
        output_wav = Path(output_wav).resolve()
        output_wav.parent.mkdir(parents=True, exist_ok=True)

        # atempo filter slows audio without pitch shift
        cmd = [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-i", str(input_wav),
            "-filter:a", f"atempo={speed}",
            "-ar", str(self.sample_rate),
            str(output_wav)
        ]

        try:
            subprocess.run(cmd, capture_output=True, check=True)
        except subprocess.CalledProcessError as e:
            # Fallback to copying if atempo fails
            output_wav.write_bytes(input_wav.read_bytes())

        return output_wav

    def evaluate_and_resolve(
        self,
        waveform: torch.Tensor,
        seg: Dict[str, Any],
        transcribe_fn: callable,
        sr: int = 16000,
        output_chunks_dir: Optional[Path | str] = None,
        seg_idx: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Evaluates a segment for ambiguity.
        If ambiguous, slows audio down to 0.75x, re-transcribes, and resolves or flags for review.
        Saves slowed sample if output_chunks_dir is provided.
        """
        orig_text = seg.get("text", "")
        duration = seg.get("duration", 0.0)
        initial_ambiguity = self.calculate_ambiguity(orig_text, duration)
        seg["ambiguity_score"] = initial_ambiguity
        seg["needs_review"] = False
        seg["slowed_audio_used"] = False

        # If clarity is good, accept without human or slowdown intervention
        if initial_ambiguity < self.ambiguity_threshold:
            return seg

        print(f"[Ambiguity Resolver] Segment ({seg['start']}s-{seg['end']}s) has ambiguity {initial_ambiguity}. Applying 0.75x slowdown...")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            orig_wav = tmppath / "orig_slice.wav"
            slow_wav = tmppath / "slow_slice.wav"

            # 1. Extract slice
            start_sample = max(0, int(seg["start"] * sr))
            end_sample = min(waveform.shape[1], int(seg["end"] * sr))
            slice_np = waveform[:, start_sample:end_sample].squeeze(0).cpu().numpy()
            sf.write(str(orig_wav), slice_np, sr, subtype="PCM_16")

            # 2. Time-stretch audio (slow down to 0.75x)
            self.time_stretch_audio(orig_wav, slow_wav, speed=self.slow_factor)

            # 3. Re-transcribe slowed audio
            try:
                slowed_text = transcribe_fn(slow_wav)
            except Exception as e:
                print(f"[Ambiguity Resolver Warning] Slowed re-transcription failed: {e}")
                slowed_text = orig_text

            slowed_ambiguity = self.calculate_ambiguity(slowed_text, duration / self.slow_factor)

            # 4. Compare confidence
            if slowed_ambiguity < initial_ambiguity and len(slowed_text.strip()) > 0:
                print(f"[Ambiguity Resolver] Resolved automatically! '{orig_text}' -> '{slowed_text}'")
                seg["text"] = slowed_text
                seg["ambiguity_score"] = slowed_ambiguity
                seg["slowed_audio_used"] = True
                seg["needs_review"] = False
            else:
                # Still ambiguous even after slowing down -> mark for minimal human intervention
                print(f"[Ambiguity Resolver] Still ambiguous ({slowed_ambiguity}). Escalating to minimal human review.")
                seg["needs_review"] = True
                seg["slowed_text_candidate"] = slowed_text
                seg["slowed_audio_used"] = True

            # Save slowed audio chunk for UI audition if requested
            if output_chunks_dir is not None and seg_idx is not None and seg["slowed_audio_used"]:
                out_dir = Path(output_chunks_dir)
                out_dir.mkdir(parents=True, exist_ok=True)
                chunk_dest = out_dir / f"chunk_{seg_idx}_slow.wav"
                import shutil
                shutil.copyfile(str(slow_wav), str(chunk_dest))
                seg["slowed_audio_file"] = chunk_dest.name

        return seg
