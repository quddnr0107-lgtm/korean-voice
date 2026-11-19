"""Exact-utterance and donor checks for the published RL-027 repair candidate."""
import hashlib
import re
import unicodedata

TARGET = 44
DONOR = 97
UNIT_IDS = ("RL-027:p0032:u001", "RL-027:p0070:u003")
SOURCE_DIGEST = "4fc4dd84afda8054d9baabb92df27f491cce7bbf80370db4c48afc630f04db09"
SPOKEN_DIGEST = "8ee928644c8eb1a1db9b4ee76b9268479cfe951b9beddc7d63f52abcd22bc768"

# Normalize only typographic and numeric spellings of the spoken legal terms.
# A missing paragraph marker, negation, new modality, or other lexical change
# must still fail the whole-utterance check.
TERMS = (
    (r"제3항", "제삼항"),
    (r"제12조", "제십이조"),
    (r"제2항", "제이항"),
    (r"3년", "삼년"),
    (r"(?:3천만|3000만|30000000)원", "삼천만원"),
)
PUNCTUATION = str.maketrans("", "", '.,，、。?!？！“”‘’「」『』"')


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_donor(record):
    """Validate both authenticated source slices and return original WAV hash equality."""
    if record["id"] != "RL-027":
        raise ValueError("unexpected source lesson")
    units = record["segments"]
    metrics = record["unit_measurements"]
    checksums = []
    for index, unit_id in zip((TARGET, DONOR), UNIT_IDS):
        unit = units[index]
        metric = metrics[index]
        text = unit["text"]
        if (unit["unit_id"] != unit_id or unit["mode"] != "reading"
                or record["narration_text"][unit["start_cp"]:unit["end_cp"]] != text
                or digest(text.strip()) != SOURCE_DIGEST):
            raise ValueError("unexpected source, marker or reading mode")
        if (metric["unit_index"] != index or metric["unit_id"] != unit_id
                or not re.fullmatch("[0-9a-f]{64}", metric["input_sha256"])):
            raise ValueError("unexpected source measurement")
        checksums.append(metric["input_sha256"])
    return checksums[0] == checksums[1]


def canonical_words(text):
    text = "".join(unicodedata.normalize("NFC", text).split())
    # Keep internal decimal points, signs and other marks with numeric meaning.
    text = re.sub(r"(?<=\d)[,，](?=\d{3}(?:[,，]\d{3})*(?!\d))", "", text)
    text = re.sub(r"(?<=\d)[.](?=\d)", "DECIMAL", text)
    text = re.sub(r"(?<=\d)[,，](?=\d)", "COMMA", text)
    text = text.translate(PUNCTUATION)
    for pattern, replacement in TERMS:
        text = re.sub(pattern, replacement, text)
    return text


def score_spoken_words(observed):
    """Return only numeric diagnostics and a strict content-match verdict."""
    words = canonical_words(observed)
    matches = {
        "paragraph_marker": int(words.startswith("제삼항")),
        "cross_reference": int("제십이조제이항을" in words),
        "prison_limit": int("삼년이하의징역" in words),
        "fine_limit": int("삼천만원이하의벌금" in words),
    }
    return {
        "accepted": all(matches.values()) and digest(words) == SPOKEN_DIGEST,
        "canonical_chars": len(words),
        **matches,
    }
