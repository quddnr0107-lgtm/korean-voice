"""Authenticated original-unit selections for the second directive correction batch."""
import hashlib
import re


PINS = {
    "DR-038": ("ba7e9636f8c7eed1251bac1a8c2cab95dd00d0d662a1de9ac4088a113b602540",
               "4fdf7b1f185027cd63218fe03b284a4297aadd67efe285e15969f1fea439e43c"),
    "DR-047": ("b518a0ae7f475744bd11d1111321fb00e62a0951366622cec33c4b1ee8b3aa5d",
               "fb0ab938e574a31c3c70549f4e76c6c3b7c6001e98d134b5b50e86aa83842700"),
}
PARAGRAPH = re.compile(r"(?:[^\r\n]+(?:\r?\n(?![ \t]*\r?\n)[^\r\n]+)*)")
TARGETS = {
    "DR-038": {
        113: (153, (11518, 11549), (11518, 11563), (11518, 11594),
              "28dc665f6bf900dbdc3905489d452492ad5ff7244a6f233b65a93da4c25d0c6d",
              "ea8ff208159022ff735960795ab83166e7aab7634e58aaba371eb859e25d881a",
              "d6385a8d81b56f614328ffab7ac11c84f0dbbc5d704035f1e4c2e04185f8a8c8",
              "89b5583ef1ed2d79820ecd389234cce143dfc7116e256ac9503272eb642825a3"),
    },
    "DR-047": {
        0: (0, (0, 28), (0, 36), (0, 38),
            "c4920b7bb95f295d1c7be0bc2acd4443539a01f9b52b86a5198d510f9900623e",
            "35fbff8f3e78113204d45f59a484d9025a4deb45c3304dded6e3d58dfd214d37",
            "dbc78b9e54d36e4fc2a05c54083b06a42f994dab0995c88a6c5e3572e7dcd13c",
            "8adb10c0ebc7c980780b5640e5b040a8ed71841944fd920d5b3846a4a319f9bd"),
        1: (1, (30, 78), (38, 94), (38, 96),
            "fe90d8cd233f0421adb2711f141e74457476f5dc5b2440c82b19f5d119b3555e",
            "4c419fb23170ac4f86feef6f546ed2d93ad9aeba0da429f9435bf05565bad8e8",
            "9c7ac5fa6724328e223fe1598dd141ebf489f70f201c17a711507dcfa47a40b4",
            "852b21b88a2db0fc5a6bb81468475d31ad49d82420754cf999c00cf05f08f7cb"),
    },
}


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def require(condition, category):
    if not condition:
        raise ValueError(category)


def extra(lesson, paragraph):
    """Record the narrower private splice bounds when a whole unit is rendered."""
    _, old, new, *_ = TARGETS[lesson][paragraph]
    if lesson == "DR-047":
        return {}
    return dict(replace_start_cp=new[0], replace_end_cp=new[1],
                original_replace_start_cp=old[0], original_replace_end_cp=old[1])


def select(plan, original_text, corrected_text, lesson):
    original = original_text.strip("\r\n") + "\n"
    require((digest(original), digest(corrected_text)) == PINS[lesson], "source_hash_mismatch")
    require(plan["lesson_id"] == lesson and plan["source"] == corrected_text and
            plan["source_sha256"] == PINS[lesson][1] and plan["review_unit_count"] == 0,
            "source_plan_mismatch")
    old_pars = list(PARAGRAPH.finditer(original))
    pars = list(PARAGRAPH.finditer(corrected_text))
    require(len(old_pars) == len(pars), "paragraph_count_changed")
    require([i for i, (a, b) in enumerate(zip(old_pars, pars)) if a[0] != b[0]]
            == list(TARGETS[lesson]), "unexpected_changed_paragraph")
    selected = []
    for number, target in TARGETS[lesson].items():
        idx, old, new, full, old_hash, new_hash, source_hash, spoken_hash = target
        unit = plan["units"][idx]
        require(old_pars[number].start() <= old[0] < old[1] <= old_pars[number].end()
                and pars[number].start() <= new[0] < new[1] <= pars[number].end(), "target_paragraph_mismatch")
        require(digest(original[slice(*old)]) == old_hash and digest(corrected_text[slice(*new)]) == new_hash
                and original[old_pars[number].start():old[0]] == corrected_text[pars[number].start():new[0]]
                and original[old[1]:old_pars[number].end()] == corrected_text[new[1]:pars[number].end()],
                "target_sentence_mismatch")
        require((unit["start_cp"], unit["end_cp"]) == full
                and unit["source"] == corrected_text[slice(*full)] and unit["source_sha256"] == source_hash
                and digest(unit["source"]) == source_hash and digest(unit["spoken"]) == spoken_hash
                and unit["spoken_sha256"] == spoken_hash and unit["mode"] == "explain"
                and unit["reading_mode_verified"] and not unit["review"], "unit_mapping_mismatch")
        if lesson == "DR-038":
            suffix = corrected_text[new[1]:full[1]]
            require(suffix == original[old[1]:11580] and digest(suffix) ==
                    "e2ed10f3241f554c33256d574d56cf49fd07274c693e55977b3d38b07ada0d0d",
                    "unchanged_sentence_suffix_mismatch")
        # Use the production unit intact: the DR-038 private editor keeps only
        # its first sentence after detecting the target boundary in actual speech.
        start, end = full if lesson == "DR-038" else new
        selected.append((number, start, end, [(idx, unit)]))
    return selected
