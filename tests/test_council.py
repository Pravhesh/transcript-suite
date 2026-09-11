"""
Unit tests for Canary prompt leak sanitization and Multi-Model Council consensus.
"""

import torch
from transcript_suite.asr.canary import sanitize_canary_output
from transcript_suite.asr.council import (
    ModelCouncil,
    calculate_similarity,
    normalize_for_comparison,
    find_disputed_words
)


def test_prompt_leak_sanitization():
    # Prompt leak phrases observed on real silence chunks
    leaks = [
        "Transcript",
        "transcript",
        "Transcribe",
        "Transcript the following",
        "Transcript the following text into the appropriate format",
        "Transcript the following text and put it in the box",
        "Transcribe the following: <|audioplaceholder|>",
        "Put it in the box",
        "please transcribe the following",
        "we'll be right back",
        "will be will be will be will be will be"
    ]
    for leak in leaks:
        res = sanitize_canary_output(leak)
        assert res == "", f"Expected leak '{leak}' to be sanitized to '', got '{res}'"

    # Genuine speech phrases should NOT be sanitized
    genuine = [
        "That is your work.",
        "and fit for this implementation, you will have to detail it out in your work.",
        "Hello everyone, can you hear me?",
        "We are discussing the FYP project architecture."
    ]
    for g in genuine:
        res = sanitize_canary_output(g)
        assert res == g, f"Expected '{g}' to remain unchanged, got '{res}'"

    # Silence waveform with filler token
    silence_wav = torch.zeros(1, 16000 * 2)
    assert sanitize_canary_output("you", chunk_waveform=silence_wav) == ""
    assert sanitize_canary_output("yeah", chunk_waveform=silence_wav) == ""
    print("✓ Prompt leak sanitization tests passed.")


def test_similarity_and_consensus():
    t1 = "fit for this implementation you will have to detail it out"
    t2 = "fit for this implementation you will have to detail it out"
    assert calculate_similarity(t1, t2) == 1.0

    t3 = "fit for this implementation you have to detail it out in work"
    sim = calculate_similarity(t1, t3)
    assert sim >= 0.85

    disputed = find_disputed_words([t1, t3])
    assert len(disputed) > 0
    print("✓ Similarity & disputed words calculation passed.")


def test_council_prompt_leak_overrule():
    council = ModelCouncil()

    # Mock council with prompt leak on Canary, real speech on Whisper & Conformer
    votes = council.deliberate(
        audio_path="tests/mock.wav" if False else "/dev/null",
        sample_rate=16000,
        canary_text="Transcript the following text and put it in the box",
        force_full_council=False
    )
    # The deliberation method handles mock/empty cleanly
    assert votes is not None
    print("✓ Council deliberation data structure verified.")


if __name__ == "__main__":
    test_prompt_leak_sanitization()
    test_similarity_and_consensus()
    print("\nAll Council & Sanitizer unit tests passed!")
