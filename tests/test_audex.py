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
    adjudicator._is_loaded = True
    
    adjudicator.unload()
    assert adjudicator._model is None
    assert adjudicator._tokenizer is None
    assert adjudicator._is_loaded is False
