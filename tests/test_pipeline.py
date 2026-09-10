"""
Component verification and smoke tests for Transcript Suite.
"""

from pathlib import Path
import tempfile
import numpy as np
import soundfile as sf
import subprocess

def test_audio_loader_and_ffmpeg():
    from transcript_suite.audio.loader import AudioLoader
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        wav_file = tmp_path / "test.wav"
        aac_file = tmp_path / "test.aac"

        # 1. Generate 3 seconds of synthetic audio (sine wave 440 Hz)
        sr = 16000
        t = np.linspace(0, 3.0, int(sr * 3.0), endpoint=False)
        audio_data = 0.5 * np.sin(2 * np.pi * 440 * t)
        sf.write(str(wav_file), audio_data, sr, subtype="PCM_16")

        # 2. Encode to AAC via ffmpeg
        subprocess.run(["ffmpeg", "-y", "-i", str(wav_file), "-c:a", "aac", str(aac_file)], check=True, capture_output=True)
        assert aac_file.exists(), "AAC file creation failed"

        # 3. Load via AudioLoader
        loader = AudioLoader(target_sr=16000)
        tensor, loaded_sr, duration = loader.load_audio(aac_file)

        assert loaded_sr == 16000
        assert abs(duration - 3.0) < 0.2, f"Duration unexpected: {duration}"
        assert tensor.shape[0] == 1
        print("✓ AudioLoader AAC decoding test passed.")

def test_export_formatting():
    from transcript_suite.export import TranscriptExporter
    segments = [
        {"start": 0.0, "end": 4.5, "speaker": "Speaker 0", "text": "Hello world."},
        {"start": 5.0, "end": 9.2, "speaker": "Speaker 1", "text": "Welcome to Transcript Suite."}
    ]
    aliases = {"Speaker 0": "Alice", "Speaker 1": "Bob"}

    result = TranscriptExporter.export_text(
        segments=segments,
        speaker_aliases=aliases,
        include_timestamps=True,
        include_speakers=True
    )
    assert "[00:00] Alice: Hello world." in result
    assert "[00:05] Bob: Welcome to Transcript Suite." in result
    print("✓ TranscriptExporter formatting test passed.")

def test_vram_manager():
    from transcript_suite.asr.memory import VRAMManager
    mgr = VRAMManager()
    stats = mgr.get_stats()
    assert "total_gb" in stats
    assert "reserved_gb" in stats
    print(f"✓ VRAMManager test passed (Device: {stats.get('device_name')}, Total: {stats.get('total_gb')} GB).")

if __name__ == "__main__":
    test_audio_loader_and_ffmpeg()
    test_export_formatting()
    test_vram_manager()
    print("\nAll component smoke tests passed successfully!")
