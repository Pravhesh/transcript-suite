"""
Phonetic matching engine for ASR Council Consensus.
Implements Lawrence Philips' Double Metaphone algorithm in pure Python (zero external dependencies)
for sound-alike disambiguation (e.g., 'their'/'there', 'site'/'cite', 'Diane'/'Diana').
"""

from typing import Tuple
import re
import difflib


def double_metaphone(word: str) -> Tuple[str, str]:
    """
    Computes primary and secondary Double-Metaphone phonetic codes for a word.
    Returns (primary_code, secondary_code). If secondary equals primary, secondary is empty string.
    """
    if not word:
        return ("", "")

    # Strip punctuation and numbers, convert to upper
    w = "".join(c for c in word.upper().strip() if c.isalpha())
    if not w:
        return ("", "")

    length = len(w)
    primary = []
    secondary = []
    current = 0

    # Initial silent combinations
    if length > 1 and w[:2] in ("GN", "KN", "PN", "WR", "PS"):
        current = 1
    elif current < length and w[current] == "X":
        primary.append("S")
        secondary.append("S")
        current += 1

    def char_at(idx: int, default: str = "") -> str:
        return w[idx] if 0 <= idx < length else default

    def substring(start: int, count: int) -> str:
        return w[start : start + count]

    def is_vowel(c: str) -> bool:
        return c in "AEIOUY"

    while current < length:
        ch = w[current]

        # Initial vowels -> 'A'
        if is_vowel(ch):
            if current == 0:
                primary.append("A")
                secondary.append("A")
            current += 1
            continue

        if ch == "B":
            primary.append("P")
            secondary.append("P")
            current += 2 if char_at(current + 1) == "B" else 1
            continue

        if ch == "C":
            if substring(current, 2) == "CH":
                primary.append("X")
                secondary.append("X")
                current += 2
            elif substring(current, 2) in ("CI", "CE", "CY"):
                primary.append("S")
                secondary.append("S")
                current += 2
            elif substring(current, 2) in ("CK", "CC"):
                primary.append("K")
                secondary.append("K")
                current += 2
            else:
                primary.append("K")
                secondary.append("K")
                current += 2 if char_at(current + 1) == "C" else 1
            continue

        if ch == "D":
            if substring(current, 2) == "DG":
                primary.append("J")
                secondary.append("J")
                current += 2
            else:
                primary.append("T")
                secondary.append("T")
                current += 2 if char_at(current + 1) in ("D", "T") else 1
            continue

        if ch in ("F", "V"):
            primary.append("F")
            secondary.append("F")
            current += 2 if char_at(current + 1) in ("F", "V") else 1
            continue

        if ch == "G":
            if substring(current, 2) == "GH":
                primary.append("K")
                secondary.append("K")
                current += 2
            elif substring(current, 2) == "GN":
                primary.append("N")
                secondary.append("N")
                current += 2
            elif substring(current, 2) in ("GE", "GI", "GY"):
                primary.append("J")
                secondary.append("K")
                current += 2
            else:
                primary.append("K")
                secondary.append("K")
                current += 2 if char_at(current + 1) == "G" else 1
            continue

        if ch == "H":
            if is_vowel(char_at(current + 1)) and (current == 0 or not is_vowel(char_at(current - 1))):
                primary.append("H")
                secondary.append("H")
            current += 1
            continue

        if ch == "J":
            primary.append("J")
            secondary.append("A")
            current += 2 if char_at(current + 1) == "J" else 1
            continue

        if ch in ("K", "Q"):
            primary.append("K")
            secondary.append("K")
            current += 2 if char_at(current + 1) in ("K", "Q") else 1
            continue

        if ch == "L":
            primary.append("L")
            secondary.append("L")
            current += 2 if char_at(current + 1) == "L" else 1
            continue

        if ch == "M":
            primary.append("M")
            secondary.append("M")
            current += 2 if char_at(current + 1) == "M" else 1
            continue

        if ch == "N":
            primary.append("N")
            secondary.append("N")
            current += 2 if char_at(current + 1) == "N" else 1
            continue

        if ch == "P":
            if substring(current, 2) == "PH":
                primary.append("F")
                secondary.append("F")
                current += 2
            else:
                primary.append("P")
                secondary.append("P")
                current += 2 if char_at(current + 1) in ("P", "B") else 1
            continue

        if ch == "R":
            primary.append("R")
            secondary.append("R")
            current += 2 if char_at(current + 1) == "R" else 1
            continue

        if ch == "S":
            if substring(current, 2) == "SH":
                primary.append("X")
                secondary.append("X")
                current += 2
            elif substring(current, 3) in ("SIA", "SIO"):
                primary.append("S")
                secondary.append("X")
                current += 3
            else:
                primary.append("S")
                secondary.append("S")
                current += 2 if char_at(current + 1) in ("S", "Z") else 1
            continue

        if ch == "T":
            if substring(current, 2) == "TH":
                primary.append("0")
                secondary.append("T")
                current += 2
            elif substring(current, 3) in ("TIA", "TIO", "TCH"):
                primary.append("X")
                secondary.append("X")
                current += 3
            else:
                primary.append("T")
                secondary.append("T")
                current += 2 if char_at(current + 1) in ("T", "D") else 1
            continue

        if ch == "W":
            if substring(current, 2) == "WR":
                primary.append("R")
                secondary.append("R")
                current += 2
            elif substring(current, 2) == "WH":
                primary.append("A")
                secondary.append("A")
                current += 2
            elif current == 0 and is_vowel(char_at(current + 1)):
                primary.append("A")
                secondary.append("F")
                current += 1
            else:
                current += 1
            continue

        if ch == "X":
            primary.append("KS")
            secondary.append("KS")
            current += 2 if char_at(current + 1) == "X" else 1
            continue

        if ch == "Z":
            primary.append("S")
            secondary.append("S")
            current += 2 if char_at(current + 1) == "Z" else 1
            continue

        current += 1

    p = "".join(primary)[:4]
    s = "".join(secondary)[:4]
    return (p, s if s != p else "")


def are_homophones(w1: str, w2: str) -> bool:
    """
    Checks if two words are phonetic homophones according to Double Metaphone.
    """
    if not w1 or not w2:
        return False
    clean1 = re.sub(r"[^\w]", "", w1).lower()
    clean2 = re.sub(r"[^\w]", "", w2).lower()
    if clean1 == clean2:
        return True

    c1 = double_metaphone(clean1)
    c2 = double_metaphone(clean2)
    if not c1[0] or not c2[0]:
        return False

    # Match if primary matches primary, or primary matches secondary
    return (
        (c1[0] == c2[0])
        or (bool(c1[1]) and c1[1] == c2[0])
        or (bool(c2[1]) and c1[0] == c2[1])
        or (bool(c1[1]) and bool(c2[1]) and c1[1] == c2[1])
    )


def phonetic_similarity(w1: str, w2: str) -> float:
    """
    Computes a composite phonetic and orthographic similarity score between 0.0 and 1.0.
    """
    clean1 = re.sub(r"[^\w]", "", w1).lower()
    clean2 = re.sub(r"[^\w]", "", w2).lower()
    if clean1 == clean2:
        return 1.0
    if not clean1 or not clean2:
        return 0.0

    if are_homophones(clean1, clean2):
        return 0.95

    # Levenshtein character similarity fallback
    char_ratio = difflib.SequenceMatcher(None, clean1, clean2).ratio()
    return round(char_ratio, 3)
