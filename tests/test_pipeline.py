"""
Component verification and smoke tests for Transcript Suite.
"""

from pathlib import Path
import tempfile
import numpy as np
import soundfile as sf
import subprocess
from unittest.mock import patch, MagicMock

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
    assert "sys_ram_used_gb" in stats
    assert "proc_ram_used_gb" in stats
    assert "sys_ram_without_suite_gb" in stats
    assert stats["sys_ram_without_suite_gb"] >= 0.0
    print(f"✓ VRAMManager test passed (Device: {stats.get('device_name')}, Total: {stats.get('total_gb')} GB, Suite: {stats.get('proc_ram_used_gb')} GB, w/o Suite: {stats.get('sys_ram_without_suite_gb')} GB).")

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

def test_transcription_pipeline_attributes():
    from unittest.mock import patch
    from transcript_suite.pipeline import TranscriptionPipeline
    from transcript_suite.config import config

    with patch("transcript_suite.pipeline.SileroVADSegmenter"), \
         patch("transcript_suite.pipeline.CanaryQwenTranscriber"), \
         patch("transcript_suite.pipeline.ModelCouncil"), \
         patch("transcript_suite.pipeline.NeMoTitaNetDiarizer"), \
         patch("transcript_suite.pipeline.GPUSpeechEnhancer"):

        pipeline = TranscriptionPipeline()
        assert hasattr(pipeline, "config")
        assert pipeline.config == config
        assert hasattr(pipeline, "device")
        assert hasattr(pipeline, "enable_audex_adjudicator")
        assert hasattr(pipeline, "audex_model_id")
        assert pipeline.audex_model_id == "nvidia/Nemotron-Labs-Audex-2B"
        print("✓ TranscriptionPipeline attributes and config attachment verified.")


def test_pipeline_stage_5_audex_adjudicates_all_chunks():
    """Verify that Stage 5 Audex adjudicates all chunks when enabled."""
    from transcript_suite.pipeline import TranscriptionPipeline

    with patch("transcript_suite.pipeline.SileroVADSegmenter"), \
         patch("transcript_suite.pipeline.CanaryQwenTranscriber"), \
         patch("transcript_suite.pipeline.ModelCouncil"), \
         patch("transcript_suite.pipeline.NeMoTitaNetDiarizer"), \
         patch("transcript_suite.pipeline.GPUSpeechEnhancer"):

        pipeline = TranscriptionPipeline(enable_audex_adjudicator=True)
        assert pipeline.enable_audex_adjudicator is True

        mock_seg1 = MagicMock(start=0.0, end=2.0, duration=2.0, speaker="Speaker 0")
        mock_seg2 = MagicMock(start=2.0, end=4.0, duration=2.0, speaker="Speaker 0")
        speech_segments = [mock_seg1, mock_seg2]

        delib1 = MagicMock(verdict="first chunk", agreement_type="CONSENSUS", deliberation_notes="", needs_human_review=False, consensus_score=0.9, votes=[])
        delib2 = MagicMock(verdict="second chunk", agreement_type="MAJORITY", deliberation_notes="", needs_human_review=False, consensus_score=0.88, votes=[])
        deliberations = [delib1, delib2]

        with patch("transcript_suite.asr.audex.AudexAdjudicator") as MockAudex:
            instance = MockAudex.return_value
            instance.adjudicate_chunk.side_effect = [
                ("audex verdict 1", "<think>reason 1</think>"),
                ("audex verdict 2", "<think>reason 2</think>")
            ]

            from transcript_suite.asr.audex import AudexAdjudicator
            audex = AudexAdjudicator(model_id=pipeline.audex_model_id, device=pipeline.device)
            audex.load_model()
            for d_count, seg in enumerate(speech_segments):
                delib = deliberations[d_count]
                verdict, reasoning = audex.adjudicate_chunk(
                    audio_path_or_slice=np.zeros(16000),
                    votes=delib.votes,
                    previous_verdict=delib.verdict,
                    sample_rate=16000
                )
                if verdict:
                    delib.verdict = verdict
                delib.agreement_type = "AUDEX_ADJUDICATED"
                if reasoning:
                    delib.deliberation_notes = (delib.deliberation_notes + "\n" + reasoning).strip()

            assert delib1.verdict == "audex verdict 1"
            assert delib2.verdict == "audex verdict 2"
            assert delib1.agreement_type == "AUDEX_ADJUDICATED"
            assert delib2.agreement_type == "AUDEX_ADJUDICATED"
            assert instance.load_model.call_count == 1
            assert instance.adjudicate_chunk.call_count == 2


if __name__ == "__main__":
    test_audio_loader_and_ffmpeg()
    test_export_formatting()
    test_vram_manager()
    test_processed_audio_export()
    test_transcription_pipeline_attributes()
    print("\nAll component smoke tests passed successfully!")
