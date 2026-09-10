"""
Unit tests for Adaptive Ambiguity Resolver and Time-Stretch Slowdown.
"""

from pathlib import Path
import tempfile
import torch
import numpy as np
import soundfile as sf
from transcript_suite.asr.ambiguity import AmbiguityResolver

def test_ambiguity_scoring():
    resolver = AmbiguityResolver()

    # Clear speech
    score_clean = resolver.calculate_ambiguity("Hello everyone and welcome to our meeting today.", duration=3.5)
    assert score_clean < 0.25, f"Expected low ambiguity, got {score_clean}"

    # Ambiguous speech with inaudible tags & severe repetition
    score_inaudible = resolver.calculate_ambiguity("We should [inaudible] ??? because of reasons.", duration=3.0)
    assert score_inaudible >= 0.35, f"Expected higher ambiguity for inaudible tags, got {score_inaudible}"

    score_repetition = resolver.calculate_ambiguity("I think I think I think I think I think", duration=2.5)
    assert score_repetition >= 0.40, f"Expected high ambiguity for repetition, got {score_repetition}"

    print("✓ Ambiguity score heuristics verified.")

def test_time_stretch_slowdown():
    resolver = AmbiguityResolver(slow_factor=0.75, sample_rate=16000)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        orig_wav = tmppath / "orig.wav"
        slow_wav = tmppath / "slow.wav"

        # Generate 1.0s sine wave
        sr = 16000
        t = np.linspace(0, 1.0, sr, endpoint=False)
        data = 0.5 * np.sin(2 * np.pi * 440 * t)
        sf.write(str(orig_wav), data, sr, subtype="PCM_16")

        # Time stretch 0.75x (should lengthen duration to ~1.33s)
        resolver.time_stretch_audio(orig_wav, slow_wav, speed=0.75)
        assert slow_wav.exists()

        slow_data, slow_sr = sf.read(str(slow_wav))
        slow_duration = len(slow_data) / slow_sr
        assert abs(slow_duration - (1.0 / 0.75)) < 0.15, f"Expected ~1.33s duration, got {slow_duration}"
        print(f"✓ Time-stretch slowdown verified (1.0s -> {round(slow_duration, 2)}s at 0.75x speed).")

def test_evaluate_and_resolve_flow():
    resolver = AmbiguityResolver()

    # Fake waveform: 2 seconds
    sr = 16000
    waveform = torch.zeros(1, int(sr * 2.0))

    # Segment with clean speech
    clean_seg = {"start": 0.0, "end": 2.0, "duration": 2.0, "text": "This is a very clear sentence."}
    res_clean = resolver.evaluate_and_resolve(waveform, clean_seg, lambda p: "not called", sr=sr)
    assert res_clean["needs_review"] is False
    assert res_clean["slowed_audio_used"] is False

    # Segment with severe ambiguity that resolves on slowdown
    ambiguous_seg = {"start": 0.0, "end": 2.0, "duration": 2.0, "text": "uhh [inaudible] ???"}
    def mock_slow_transcribe(wav_path):
        return "Now clearly understood phrase."

    res_resolved = resolver.evaluate_and_resolve(waveform, ambiguous_seg, mock_slow_transcribe, sr=sr)
    assert res_resolved["slowed_audio_used"] is True
    assert res_resolved["needs_review"] is False
    assert res_resolved["text"] == "Now clearly understood phrase."
    print("✓ Ambiguity auto-resolution via audio slowdown verified.")

if __name__ == "__main__":
    test_ambiguity_scoring()
    test_time_stretch_slowdown()
    test_evaluate_and_resolve_flow()
    print("\nAll Ambiguity Resolver tests passed!")
