"""Select authenticated, reviewed source spans for the two directive audio corrections."""
import hashlib
import re


PINS = {
    "DR-052": (
        "09fa7f04a92f047298b141b2659a4bf4ebfb5260a8e6f9fffcb3606ec610763e",
        "7c8b3a4140d6e2c33c7671a470909decf858c06479338ab47bb66fed221a8dcc",
        (26, 32, 41, 46, 58), (26, 32, 41, 46, 58, 71),
    ),
    "DR-054": (
        "1a3289c6e64971170d302817facad9a9d3c64bc8b87e885983454280ee1f579f",
        "be5637d9242e760f5e1f25f745f662150f7f37b2e49b78de2c51021da20fa37c",
        (30, 33, 56, 58, 59, 62, 70), (30, 33, 56, 58, 59, 62, 70),
    ),
}
PARAGRAPH = re.compile(r"(?:[^\r\n]+(?:\r?\n(?![ \t]*\r?\n)[^\r\n]+)*)")


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def require(condition, code):
    if not condition:
        raise ValueError(code)


def select(plan, original_text, corrected_text, lesson):
    before, after, changes, requested = PINS[lesson]
    # The authenticated course input may carry outer blank lines left by packaging.
    original = original_text.strip("\r\n") + "\n"
    require(digest(original) == before and digest(corrected_text) == after, "source_hash_mismatch")
    require(plan["lesson_id"] == lesson and plan["source"] == corrected_text and
            plan["source_sha256"] == after, "plan_identity_mismatch")
    require(plan["review_unit_count"] == 0, "source_review_pending")
    old_spans = list(PARAGRAPH.finditer(original))
    spans = list(PARAGRAPH.finditer(corrected_text))
    require(len(old_spans) == len(spans), "paragraph_structure_changed")
    require(tuple(i for i, (a, b) in enumerate(zip(old_spans, spans)) if a[0] != b[0]) == changes,
            "unexpected_changed_paragraphs")
    # DR-052 paragraph 71 is additionally rerecorded because the existing audio
    # differed from its already-correct manuscript.
    selected = []
    for number in requested:
        start, end = spans[number].span()
        units = [(i, u) for i, u in enumerate(plan["units"])
                 if u["start_cp"] < end and u["end_cp"] > start]
        require(units and units[0][1]["start_cp"] == start, "paragraph_missing_start")
        cursor = start
        for _, unit in units:
            a, b = unit["start_cp"], unit["end_cp"]
            require(a == cursor and a < b and not unit["review"] and unit["reading_mode_verified"]
                    and corrected_text[a:b] == unit["source"] and digest(unit["source"]) == unit["source_sha256"],
                    "paragraph_unit_identity")
            cursor = b
        # Production includes separator newlines in its last unit. They are
        # deliberately excluded from the private video paragraph coordinates.
        require(end <= cursor and not corrected_text[end:cursor].strip(), "paragraph_missing_end")
        selected.append((number, start, end, units))
    return selected
