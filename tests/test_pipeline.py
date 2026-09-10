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

def test_processed_audio_export():
    from transcript_suite.audio.loader import AudioLoader
    from transcript_suite.audio.enhancer import GPUSpeechEnhancer

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        wav_file = tmp_path / "test.wav"
        processed_file = tmp_path / "processed.wav"

        sr = 16000
        t = np.linspace(0, 1.0, sr, endpoint=False)
        data = 0.5 * np.sin(2 * np.pi * 440 * t)
        sf.write(str(wav_file), data, sr, subtype="PCM_16")

        loader = AudioLoader(target_sr=sr)
        tensor, loaded_sr, duration = loader.load_audio(wav_file)
        enhancer = GPUSpeechEnhancer(sample_rate=sr)
        enhanced = enhancer.enhance(tensor)

        sf.write(str(processed_file), enhanced.squeeze(0).cpu().numpy(), sr, subtype="PCM_16")
        assert processed_file.exists()
        info = sf.info(str(processed_file))
        assert info.samplerate == 16000
        assert info.channels == 1
        print("✓ Processed model-ingested audio export test passed.")

if __name__ == "__main__":
    test_audio_loader_and_ffmpeg()
    test_export_formatting()
    test_vram_manager()
    test_processed_audio_export()
    print("\nAll component smoke tests passed successfully!")
