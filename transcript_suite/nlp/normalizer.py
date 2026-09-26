"""
Deterministic Number & Disfluency Normalizer (Sub-Phase 4.4.A).
Converts spoken numbers and currencies to standard numeric symbols,
and optionally strips disfluency filler words with clean punctuation preservation.
"""

import re
from typing import Dict, Any, Tuple, Optional, List, Union


# Spoken number dictionaries
UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19
}

TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90
}

SCALES = {
    "hundred": 100,
    "thousand": 1_000,
    "million": 1_000_000,
    "billion": 1_000_000_000,
    "trillion": 1_000_000_000_000
}

ORDINALS = {
    "first": "1st", "second": "2nd", "third": "3rd", "fourth": "4th", "fifth": "5th",
    "sixth": "6th", "seventh": "7th", "eighth": "8th", "ninth": "9th", "tenth": "10th",
    "eleventh": "11th", "twelfth": "12th", "thirteenth": "13th", "fourteenth": "14th",
    "fifteenth": "15th", "sixteenth": "16th", "seventeenth": "17th", "eighteenth": "18th",
    "nineteenth": "19th", "twentieth": "20th", "thirtieth": "30th"
}

CURRENCY_SYMBOLS = {
    "dollar": "$", "dollars": "$", "buck": "$", "bucks": "$",
    "euro": "€", "euros": "€",
    "pound": "£", "pounds": "£"
}

DEFAULT_DISFLUENCIES = [
    "you know", "i mean", "like", "um", "uh", "erm", "er", "ah", "umm", "uhh", "hmm"
]

NUMBER_WORDS_RE = r"(?:\d+(?:,\d+)*(?:\.\d+)?|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion|trillion)"
NUMBER_PHRASE_RE = rf"(?:{NUMBER_WORDS_RE}(?:[\s\-]+(?:and[\s\-]+)?{NUMBER_WORDS_RE})*)"


class NormalizedResult(str):
    """
    Dual-compatibility string result that behaves as a standard string
    (supporting substring checks like `assert '$100' in res` and `.lower()`)
    while supporting 2-tuple unpacking (`text, count = ...` or `text, metrics = ...`).
    """
    def __new__(cls, text: str, count: int = 0, metrics: Optional[Dict[str, Any]] = None):
        obj = str.__new__(cls, text)
        obj.text = text
        obj.count = count
        obj.metrics = metrics or {}
        return obj

    def __iter__(self):
        if self.metrics:
            return iter((str(self), self.metrics))
        return iter((str(self), self.count))

    def __getitem__(self, item):
        if isinstance(item, int):
            if item == 0:
                return str(self)
            elif item == 1:
                return self.metrics if self.metrics else self.count
        return super().__getitem__(item)


def words_to_number(words_str: str) -> Optional[int]:
    """Converts a phrase of number words (e.g. 'twenty five', 'one hundred') to an integer."""
    tokens = re.findall(r"\b\w+\b", words_str.lower().replace("-", " "))
    if not tokens:
        return None

    current_val = 0
    total = 0

    for token in tokens:
        if token in UNITS:
            current_val += UNITS[token]
        elif token in TENS:
            current_val += TENS[token]
        elif token == "hundred":
            current_val = (current_val if current_val > 0 else 1) * 100
        elif token in SCALES:
            scale = SCALES[token]
            current_val = (current_val if current_val > 0 else 1) * scale
            total += current_val
            current_val = 0
        elif token == "and":
            continue
        elif token.isdigit():
            current_val += int(token)
        else:
            return None

    return total + current_val


def normalize_currencies(text: str) -> NormalizedResult:
    """
    Normalizes spoken currencies:
    - 'one hundred dollars' -> '$100'
    - 'twenty dollars and fifty cents' -> '$20.50'
    - 'five million dollars' -> '$5M' or '$5,000,000'
    - 'fifty euros' -> '€50'
    - 'twenty pounds' -> '£20'
    """
    count = 0

    # Pattern 1: X dollars and Y cents
    def replace_dollar_cents(m):
        nonlocal count
        dol_phrase = m.group(1).strip()
        cent_phrase = m.group(3).strip()
        cur_unit = m.group(2).lower()
        sym = CURRENCY_SYMBOLS.get(cur_unit, "$")

        d_val = int(dol_phrase) if dol_phrase.isdigit() else words_to_number(dol_phrase)
        c_val = int(cent_phrase) if cent_phrase.isdigit() else words_to_number(cent_phrase)

        if d_val is not None and c_val is not None:
            count += 1
            return f"{sym}{d_val:,}.{c_val:02d}"
        return m.group(0)

    pattern_cents = re.compile(
        rf"\b({NUMBER_PHRASE_RE})\s+(dollars?|euros?|pounds?)\s+and\s+({NUMBER_PHRASE_RE})\s+cents?\b",
        re.IGNORECASE
    )
    text = pattern_cents.sub(replace_dollar_cents, text)

    # Pattern 2: X million/billion dollars
    def replace_scale_currency(m):
        nonlocal count
        num_str = m.group(1).strip()
        scale = m.group(2).lower()
        cur_unit = m.group(3).lower()
        sym = CURRENCY_SYMBOLS.get(cur_unit, "$")

        val = int(num_str) if num_str.isdigit() else words_to_number(num_str)
        if val is not None:
            count += 1
            scale_abbr = "M" if scale == "million" else ("B" if scale == "billion" else "T")
            return f"{sym}{val}{scale_abbr}"
        return m.group(0)

    pattern_scale = re.compile(
        rf"\b({NUMBER_PHRASE_RE})\s+(million|billion|trillion)\s+(dollars?|euros?|pounds?)\b",
        re.IGNORECASE
    )
    text = pattern_scale.sub(replace_scale_currency, text)

    # Pattern 3: Standard X dollars / euros / pounds
    def replace_simple_currency(m):
        nonlocal count
        phrase = m.group(1).strip()
        cur_unit = m.group(2).lower()
        sym = CURRENCY_SYMBOLS.get(cur_unit, "$")

        val = int(phrase) if phrase.isdigit() else words_to_number(phrase)
        if val is not None:
            count += 1
            return f"{sym}{val:,}"
        return m.group(0)

    pattern_simple = re.compile(
        rf"\b({NUMBER_PHRASE_RE})\s+(dollars?|euros?|pounds?|bucks?)\b",
        re.IGNORECASE
    )
    text = pattern_simple.sub(replace_simple_currency, text)

    return NormalizedResult(text, count)


def normalize_percentages(text: str) -> NormalizedResult:
    """
    Normalizes spoken percentages:
    - 'twenty five percent' -> '25%'
    - 'zero point five percent' -> '0.5%'
    - '100 percent' -> '100%'
    - '10 percentage point' -> '10%'
    """
    count = 0

    def replace_percent(m):
        nonlocal count
        num_phrase = m.group(1).strip().replace("-", " ")

        if "point" in num_phrase.lower():
            parts = num_phrase.lower().split("point")
            left = parts[0].strip()
            right = parts[1].strip()
            l_val = int(left) if left.isdigit() else words_to_number(left)
            r_val = int(right) if right.isdigit() else words_to_number(right)
            if l_val is not None and r_val is not None:
                count += 1
                return f"{l_val}.{r_val}%"

        val = int(num_phrase) if num_phrase.isdigit() else words_to_number(num_phrase)
        if val is not None:
            count += 1
            return f"{val}%"
        return m.group(0)

    pattern = re.compile(
        rf"\b({NUMBER_PHRASE_RE})\s*(?:percent|per\s+cent|percentage(?:\s+points?)?)\b",
        re.IGNORECASE
    )
    text = pattern.sub(replace_percent, text)
    return NormalizedResult(text, count)


def normalize_numbers_and_ordinals(text: str) -> NormalizedResult:
    """Normalizes simple standalone written numbers and ordinals."""
    count = 0

    # Ordinals (e.g. first -> 1st, second -> 2nd)
    for word, ord_str in ORDINALS.items():
        pattern = re.compile(rf"\b{word}\b", re.IGNORECASE)
        new_text, n = pattern.subn(ord_str, text)
        if n > 0:
            count += n
            text = new_text

    # Decimals like 'three point one four'
    def replace_decimal(m):
        nonlocal count
        w1 = m.group(1).lower()
        w2 = m.group(2).lower()
        v1 = UNITS.get(w1, int(w1) if w1.isdigit() else None)
        v2 = UNITS.get(w2, int(w2) if w2.isdigit() else None)
        if v1 is not None and v2 is not None:
            count += 1
            return f"{v1}.{v2}"
        return m.group(0)

    pattern_dec = re.compile(r"\b([a-zA-Z0-9]+)\s+point\s+([a-zA-Z0-9]+)\b", re.IGNORECASE)
    text = pattern_dec.sub(replace_decimal, text)

    # Standalone written numbers (e.g. "three participants" -> "3 participants")
    def replace_standalone_num(m):
        nonlocal count
        phrase = m.group(0).strip()
        if phrase.isdigit():
            return phrase
        val = words_to_number(phrase)
        if val is not None:
            count += 1
            return str(val)
        return phrase

    text = re.sub(rf"\b{NUMBER_PHRASE_RE}\b", replace_standalone_num, text, flags=re.IGNORECASE)

    return NormalizedResult(text, count)


def remove_disfluencies(text: str, custom_fillers: Optional[List[str]] = None) -> NormalizedResult:
    """
    Detects and cleanly removes verbal filler words:
    'um', 'uh', 'erm', 'er', 'ah', 'umm', 'uhh', 'hmm', 'you know', 'i mean', 'like'.
    Preserves clean punctuation without orphan commas or double spaces.
    """
    fillers = custom_fillers if custom_fillers else DEFAULT_DISFLUENCIES
    filler_regex = r"\b(?:" + "|".join(re.escape(f) for f in sorted(fillers, key=len, reverse=True)) + r")\b"

    # Count occurrences
    matches = list(re.finditer(filler_regex, text, re.IGNORECASE))
    count = len(matches)
    if count == 0:
        return NormalizedResult(text, 0)

    # Remove fillers and attached comma spacing cleanly
    cleaned = re.sub(rf"(?:,\s*)?{filler_regex}(?:,\s*)?", " ", text, flags=re.IGNORECASE)
    cleaned = re.sub(r",\s*,+", ",", cleaned)
    cleaned = re.sub(r"^\s*,\s*", "", cleaned)
    cleaned = re.sub(r"\s+([,\.\?!])", r"\1", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

    # Capitalize first letter if needed
    if cleaned and text and text[0].isupper() and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]

    return NormalizedResult(cleaned, count)


def normalize_transcript_text(
    text: str,
    normalize_numbers: bool = True,
    remove_fillers: bool = False,
    custom_fillers: Optional[List[str]] = None,
    convert_numbers: Optional[bool] = None,
    normalize_currencies_flag: Optional[bool] = None,
    normalize_percentages_flag: Optional[bool] = None,
    remove_disfluencies_flag: Optional[bool] = None,
    **kwargs: Any
) -> NormalizedResult:
    """
    Main entrypoint for transcript text normalization (Feature 4.4.A).
    Applies currencies, percentages, numbers/ordinals, and disfluency stripping.
    Returns NormalizedResult which behaves as both a str and an unpackable 2-tuple (text, metrics).
    """
    # Resolve aliases
    if convert_numbers is not None:
        do_numbers = convert_numbers
    else:
        do_numbers = normalize_numbers

    do_currencies = kwargs.get("normalize_currencies", normalize_currencies_flag)
    if do_currencies is None:
        do_currencies = do_numbers

    do_percentages = kwargs.get("normalize_percentages", normalize_percentages_flag)
    if do_percentages is None:
        do_percentages = do_numbers

    do_disfluencies = kwargs.get("remove_disfluencies", remove_disfluencies_flag)
    if do_disfluencies is None:
        do_disfluencies = remove_fillers

    if not text:
        metrics = {
            "currency_replacements": 0,
            "percent_replacements": 0,
            "number_replacements": 0,
            "fillers_removed": 0,
            "changed": False
        }
        return NormalizedResult("", 0, metrics)

    orig_text = text
    curr_count = 0
    pct_count = 0
    num_count = 0
    filler_count = 0

    if do_currencies:
        text, curr_count = normalize_currencies(text)

    if do_percentages:
        text, pct_count = normalize_percentages(text)

    if do_numbers:
        text, num_count = normalize_numbers_and_ordinals(text)

    if do_disfluencies:
        text, filler_count = remove_disfluencies(text, custom_fillers)

    metrics = {
        "currency_replacements": curr_count,
        "percent_replacements": pct_count,
        "number_replacements": num_count,
        "fillers_removed": filler_count,
        "total_changes": curr_count + pct_count + num_count + filler_count,
        "changed": text != orig_text
    }

    return NormalizedResult(text, count=metrics["total_changes"], metrics=metrics)
