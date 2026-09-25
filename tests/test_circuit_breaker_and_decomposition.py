"""
Tests for Autoregressive Loop Circuit-Breaker (Feature 2.2.C)
and Orthogonal Confidence Decomposition (Feature 2.3.A).
"""

import pytest
import torch
import numpy as np
from transcript_suite.asr.council import (
    ModelCouncil,
    detect_autoregressive_loop,
    compute_orthogonal_confidence,
    CouncilDeliberation,
)


def test_detect_autoregressive_loop_ngram():
    # 1-gram loop (>= 4 repeats)
    is_loop, reason, m = detect_autoregressive_loop("thank thank thank thank you")
    assert is_loop is True
    assert "1-gram loop" in reason

    # 2-gram loop (>= 3 repeats)
    is_loop, reason, m = detect_autoregressive_loop("you know you know you know what I mean")
    assert is_loop is True
    assert "2-gram loop" in reason

    # 3-gram loop (>= 3 repeats)
    is_loop, reason, m = detect_autoregressive_loop("in the morning in the morning in the morning we left")
    assert is_loop is True
    assert "3-gram loop" in reason

    # 4-gram loop (>= 2 repeats)
    is_loop, reason, m = detect_autoregressive_loop("please subscribe to my channel please subscribe to my channel")
    assert is_loop is True
    assert "loop" in reason.lower() or "cycle" in reason.lower()


def test_detect_autoregressive_loop_tail_cycle():
    text = "We went for a walk and then I said goodbye my friend goodbye my friend"
    is_loop, reason, m = detect_autoregressive_loop(text)
    assert is_loop is True
    assert "cycle" in reason.lower() or "loop" in reason.lower()


def test_detect_autoregressive_loop_entropy():
    # Degenerate low entropy repetition
    text = "alpha beta alpha beta alpha beta alpha beta alpha beta"
    is_loop, reason, m = detect_autoregressive_loop(text)
    assert is_loop is True
    assert "diversity" in m
    assert m["diversity"] < 0.40 or m["entropy"] < 1.80 or "loop" in reason.lower()


def test_normal_text_does_not_trip_loop_detector():
    # Natural English repetition ("that that") should not trip
    normal_1 = "He said that that was not what he originally intended."
    is_loop, _, _ = detect_autoregressive_loop(normal_1)
    assert is_loop is False

    # Short phrases should not trip
    assert detect_autoregressive_loop("Yes.")[0] is False
    assert detect_autoregressive_loop("No, no, I agree.")[0] is False
    assert detect_autoregressive_loop("")[0] is False

    # Normal complex sentence
    normal_2 = "The Supreme Council incorporates Canary-Qwen, Whisper, Conformer-CTC and Parakeet to eliminate transcription ambiguity."
    is_loop, _, _ = detect_autoregressive_loop(normal_2)
    assert is_loop is False


def test_single_juror_loop_veto():
    council = ModelCouncil()
    delib = council.synthesize_deliberation(
        canary_text="hallucinate hallucinate hallucinate hallucinate hallucinate",
        whisper_text="The acoustic anchor was verified successfully.",
        conformer_text="the acoustic anchor was verified successfully"
    )

    # Canary loop should be vetoed
    canary_vote = next(v for v in delib.votes if "Canary" in v["member"])
    assert "[CANARY_LOOP_VETO]" in canary_vote["role"]
    assert canary_vote["confidence"] == 0.05
    assert canary_vote["weight"] == 0.0

    # Verdict should follow the valid consensus of Whisper and Conformer
    assert "acoustic anchor was verified" in delib.verdict.lower()
    assert delib.loop_circuit_breaker_tripped is False


def test_tripped_circuit_breaker_forces_ctc_anchor():
    council = ModelCouncil()
    # Both Canary and Whisper enter degenerate loops
    delib = council.synthesize_deliberation(
        canary_text="loop loop loop loop loop loop",
        whisper_text="you know you know you know you know you know",
        conformer_text="the board meeting will conclude at five pm"
    )

    assert delib.loop_circuit_breaker_tripped is True
    assert delib.agreement_type == "CTC_ANCHORED"
    assert "the board meeting will conclude at five pm" in delib.verdict.lower()
    assert "[LOOP_CIRCUIT_BREAKER_TRIPPED]" in delib.deliberation_notes


def test_single_juror_loop_with_empty_whisper_trips_breaker():
    council = ModelCouncil()
    delib = council.synthesize_deliberation(
        canary_text="thank thank thank thank you very much",
        whisper_text="",
        conformer_text="we have completed the audio test"
    )

    assert delib.loop_circuit_breaker_tripped is True
    assert "completed the audio test" in delib.verdict.lower()


def test_orthogonal_confidence_pristine_consensus():
    # Synthetic clean sine wave (clean speech simulation)
    t = torch.linspace(0, 1.0, 16000)
    waveform = (0.4 * torch.sin(2 * np.pi * 440.0 * t)).unsqueeze(0)

    decomp = compute_orthogonal_confidence(
        waveform=waveform,
        sr=16000,
        conformer_conf=0.96,
        lattice_score=0.98,
        canary_text="This is an exact transcript.",
        whisper_text="This is an exact transcript.",
        disputed_tokens=[],
        circuit_breaker_tripped=False
    )

    assert decomp["acoustic_score"] >= 0.70
    assert decomp["semantic_score"] >= 0.88
    assert decomp["quadrant"] == "HIGH_ACOUSTIC_HIGH_SEMANTIC"
    assert decomp["clipping_pct"] == 0.0


def test_orthogonal_confidence_jargon_dispute():
    # Pristine audio baseline (waveform=None uses 28dB clean baseline)
    decomp = compute_orthogonal_confidence(
        waveform=None,
        sr=16000,
        conformer_conf=0.95,
        lattice_score=0.45,
        canary_text="We used NeMo PyAnnote with CUDA.",
        whisper_text="We used Nemo Pie Annotate with Couda.",
        disputed_tokens=["pyannote", "pie", "annotate", "cuda", "couda"],
        circuit_breaker_tripped=False
    )

    assert decomp["acoustic_score"] >= 0.65  # High acoustic clarity
    assert decomp["semantic_score"] < 0.65   # Low semantic agreement due to jargon
    assert decomp["quadrant"] == "HIGH_ACOUSTIC_LOW_SEMANTIC"


def test_orthogonal_confidence_noisy_consensus():
    # Low SNR / inaudible noise floor audio
    low_snr_wave = torch.zeros(1, 16000)

    decomp = compute_orthogonal_confidence(
        waveform=low_snr_wave,
        sr=16000,
        conformer_conf=0.60,
        lattice_score=0.95,
        canary_text="Good morning everyone.",
        whisper_text="Good morning everyone.",
        disputed_tokens=[],
        circuit_breaker_tripped=False
    )

    assert decomp["semantic_score"] >= 0.65
    assert decomp["acoustic_score"] < 0.65
    assert decomp["acoustic_grade"] in ("MODERATE_NOISE", "POOR_SNR")
    assert decomp["quadrant"] == "LOW_ACOUSTIC_HIGH_SEMANTIC"


def test_deliberation_serialization():
    council = ModelCouncil()
    delib = council.synthesize_deliberation(
        canary_text="Testing transcription.",
        whisper_text="Testing transcription.",
        conformer_text="testing transcription"
    )

    d = delib.to_dict()
    assert "loop_circuit_breaker_tripped" in d
    assert d["loop_circuit_breaker_tripped"] is False
    assert "confidence_decomposition" in d
    assert "acoustic_score" in d["confidence_decomposition"]
    assert "semantic_score" in d["confidence_decomposition"]
    assert "quadrant" in d["confidence_decomposition"]
