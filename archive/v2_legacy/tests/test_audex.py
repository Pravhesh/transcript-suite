"""
Tests for Audex-2B Supreme Audio Adjudicator (Stage 5).
"""

import pytest
from unittest.mock import MagicMock, patch
import numpy as np
import torch

from transcript_suite.asr.audex import AudexAdjudicator


def test_audex_adjudicator_initialization():
    """Verify AudexAdjudicator instance initialization."""
    adjudicator = AudexAdjudicator(model_id="nvidia/Nemotron-Labs-Audex-2B", device="cpu")
    assert adjudicator.model_id == "nvidia/Nemotron-Labs-Audex-2B"
    assert adjudicator.device == "cpu"
    assert adjudicator._is_loaded is False
    assert adjudicator._model is None


def test_audex_adjudicate_chunk_fallback_reasoning():
    """Verify chunk adjudication generates structured <think> reasoning and verdict."""
    adjudicator = AudexAdjudicator(model_id="nvidia/Nemotron-Labs-Audex-2B", device="cpu")
    
    votes = [
        {"role": "Lead Justice (Canary)", "hypothesis": "The quick brown fox", "confidence": 0.95},
        {"role": "Cross-Examiner (Whisper)", "hypothesis": "The quick brown box", "confidence": 0.72},
        {"role": "Acoustic Anchor (Conformer)", "hypothesis": "quick brown fox", "confidence": 0.88}
    ]
    
    dummy_waveform = np.zeros(16000, dtype=np.float32)
    verdict, reasoning = adjudicator.adjudicate_chunk(
        audio_path_or_slice=dummy_waveform,
        votes=votes,
        previous_verdict="The quick brown fox",
        sample_rate=16000
    )
    
    assert verdict == "The quick brown fox"
    assert "<think>" in reasoning
    assert "</think>" in reasoning
    assert "Supreme Audio Adjudicator" in reasoning
    assert "fox" in reasoning


def test_audex_adjudicator_unload():
    """Verify unload safely purges models and tensors."""
    adjudicator = AudexAdjudicator(device="cpu")
    adjudicator._model = MagicMock()
    adjudicator._tokenizer = MagicMock()
    adjudicator._feature_extractor = MagicMock()
    adjudicator._is_loaded = True
    
    adjudicator.unload()
    assert adjudicator._model is None
    assert adjudicator._tokenizer is None
    assert adjudicator._feature_extractor is None
    assert adjudicator._is_loaded is False


def test_audex_adjudicate_chunk_neural_mock():
    """Verify neural adjudication branch when Audex model is loaded."""
    adjudicator = AudexAdjudicator(model_id="nvidia/Nemotron-Labs-Audex-2B", device="cpu")
    adjudicator._is_loaded = True
    adjudicator._model = MagicMock()
    adjudicator._model.dtype = torch.float32
    adjudicator._tokenizer = MagicMock()
    adjudicator._tokenizer.convert_tokens_to_ids.return_value = 100
    adjudicator._tokenizer.unk_token_id = 99
    adjudicator._tokenizer.pad_token_id = 0
    adjudicator._feature_extractor = MagicMock()
    adjudicator._config = MagicMock()
    adjudicator._config.sound_clip_duration = 30.0
    adjudicator._config.sound_embedding_size = 750

    mock_input_ids = torch.zeros((1, 10), dtype=torch.long)
    adjudicator._tokenizer.return_value = MagicMock(
        input_ids=mock_input_ids,
        attention_mask=torch.ones((1, 10), dtype=torch.long)
    )
    adjudicator._model.generate.return_value = torch.zeros((1, 25), dtype=torch.long)
    adjudicator._tokenizer.decode.return_value = "Acoustic reasoning verified.\n</think>\nThe quick brown fox"

    with patch("transcript_suite.asr.audex.sys.path", list()), \
         patch.dict("sys.modules", {"audio_utils": MagicMock()}):
        import sys
        mock_audio_utils = sys.modules["audio_utils"]
        mock_audio_utils.build_prompt_template = MagicMock(return_value="<sound>")
        mock_audio_utils.expand_sound_placeholder = MagicMock(return_value="<expanded>")
        mock_audio_utils.extract_whisper_features = MagicMock(return_value=torch.zeros((1, 80, 3000)))
        mock_audio_utils.IM_END_TOKEN = "<|im_end|>"
        mock_audio_utils.split_thinking = MagicMock(return_value=("<think>\nAcoustic reasoning verified.\n</think>", "The quick brown fox"))
        mock_audio_utils.build_attention_mask = MagicMock(return_value=torch.ones((1, 10), dtype=torch.long))

        verdict, reasoning = adjudicator.adjudicate_chunk(
            audio_path_or_slice=np.zeros(16000, dtype=np.float32),
            votes=[{"role": "Juror 1", "hypothesis": "The quick brown fox", "confidence": 0.9}],
            previous_verdict="The quick brown fox"
        )
        assert verdict == "The quick brown fox"
        assert "<think>" in reasoning
        assert "</think>" in reasoning
