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


def test_sequential_synthesis_arbitration():
    council = ModelCouncil()

    # 1. Unanimous agreement
    delib1 = council.synthesize_deliberation(
        canary_text="this implementation you will have to detail it out in your work",
        whisper_text="this implementation you will have to detail it out in your work",
        conformer_text="this implementation you will have to detail it of in your work",
        parakeet_text="this implementation you will have to detail it out in your work"
    )
    assert delib1.agreement_type == "UNANIMOUS"
    assert "implementation" in delib1.verdict
    assert delib1.consensus_score >= 0.85
    assert not delib1.needs_human_review

    # 2. Canary prompt leak overruled by Whisper + Conformer
    delib2 = council.synthesize_deliberation(
        canary_text="Transcript the following text and put it in the box",
        whisper_text="the mentor meeting is scheduled for tomorrow",
        conformer_text="the mentor meeting is scheduled for tomorrow"
    )
    assert delib2.agreement_type == "MAJORITY"
    assert "mentor meeting" in delib2.verdict
    assert "Transcript" not in delib2.verdict

    # 3. CTC silence confirmation
    delib3 = council.synthesize_deliberation(
        canary_text="yeah",
        whisper_text="",
        conformer_text="",
        parakeet_text=""
    )
    assert delib3.agreement_type in ("CTC_ANCHORED", "UNANIMOUS")
    assert delib3.verdict == ""
    print("✓ synthesize_deliberation trilateral & quadrilateral tests passed.")


def test_batch_methods_and_staged_unloading():
    council = ModelCouncil()
    assert council.transcribe_batch_whisper([]) == []
    assert council.transcribe_batch_conformer([]) == []
    assert council.transcribe_batch_parakeet([]) == []

    # Verify unloading functions execute cleanly
    council.unload_conformer()
    council.unload_parakeet()
    council.unload_whisper()
    council.unload_parakeet_and_ctc()
    council.unload_members()
    print("✓ Council batched signatures and staged unloads verified.")


if __name__ == "__main__":
    test_prompt_leak_sanitization()
    test_similarity_and_consensus()
    test_sequential_synthesis_arbitration()
    test_batch_methods_and_staged_unloading()
    print("\nAll Council & Sanitizer unit tests passed!")

