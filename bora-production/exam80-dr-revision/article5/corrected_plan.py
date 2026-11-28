"""Render only one independently diagnosed sentence in the archived DR-054 source."""
from hashlib import sha256

LESSON = "DR-054"
KEY = "DR-054-A5"
PATH = "production303/corrections/incoming-supplements"
SOURCE_COMMIT = "367750f745f0d1873290b9885f790f5484be3cd7"
PINS = {LESSON: ("1a3289c6e64971170d302817facad9a9d3c64bc8b87e885983454280ee1f579f",
                 "be5637d9242e760f5e1f25f745f662150f7f37b2e49b78de2c51021da20fa37c")}
UNIT_SCOPE = ("eee0d48144cde005fa1dc4f309fc6dacafaeef3baa1ad1f9ac4d6269befe5311",
              "8c8c3ea5f5afba6e9467fbe6bf35c207e0eeedce0a988e33a2ab1ae51cbac740")
SENTENCE = ("dd5f6ff6da44a944219ae5e4d72b4cb491ece3e57ac8d9bb7c89bc61484e2576",
            "09333e5a271bdc554bf56cb75cf1f9167f667609df9ac3f7357cb0fad59a2361")
BEGIN, END = 2645, 2652
PARENT_UNIT = 29
REQUEST = dict(schema="exam80-private-acoustic-supplement/v1",lesson=LESSON,source_commit=SOURCE_COMMIT,
               source_sha256=PINS[LESSON][1],unit=PARENT_UNIT,source_span=[BEGIN,END],scope="article5")


def digest(text):
    return sha256(text.encode()).hexdigest()


def require(value):
    if not value:
        raise ValueError("supplement_source_scope")


def select(plan, original_text, corrected_text, lesson):
    return select_sentence(plan,original_text,corrected_text,lesson,LESSON,PINS,UNIT_SCOPE,SENTENCE,BEGIN,END,PARENT_UNIT)


def select_sentence(plan,original_text,corrected_text,lesson,expected_lesson,pins,unit_scope,sentence,begin,end,parent_unit):
    import planning
    import dr_pronunciation
    original=original_text.strip("\r\n")+"\n"
    require(lesson==expected_lesson and (digest(original),digest(corrected_text))==pins[lesson]
            and plan["lesson_id"]==lesson and plan["source"]==corrected_text
            and plan["source_sha256"]==pins[lesson][1] and plan["review_unit_count"]==0)
    parent=plan["units"][parent_unit]
    require((parent["source_sha256"],parent["spoken_sha256"])==unit_scope
            and parent["start_cp"] < begin < end < parent["end_cp"]
            and not parent["review"] and parent["reading_mode_verified"])
    phrase=corrected_text[begin:end]
    require(phrase==original[begin:end] and digest(phrase)==sentence[0])
    sub=dr_pronunciation.apply_to_plan(planning.build_plan(lesson,phrase))
    require(len(sub["units"])==1 and sub["review_unit_count"]==0)
    unit=sub["units"][0]
    require((unit["source_sha256"],unit["spoken_sha256"])==sentence and unit["source"]==phrase
            and parent["mode"]==unit["mode"]=="explain" and unit["reading_mode_verified"] and not unit["review"])
    start16=len(corrected_text[:begin].encode("utf-16-le"))//2
    # Preserve original identity and record coordinates in the canonical manuscript.
    # The scoped sentence has a different source hash and never replaces an original unit checkpoint.
    unit=dict(unit,id=parent["id"],paragraph_id=parent["paragraph_id"],start_cp=begin,end_cp=end,
              start_utf16=start16,end_utf16=start16+len(phrase.encode("utf-16-le"))//2)
    return [(parent_unit,begin,end,[(parent_unit,unit)])]


def extra(lesson,slot):
    return sentence_ranges(lesson,slot,LESSON,PARENT_UNIT,22,BEGIN,END)


def sentence_ranges(lesson,slot,expected_lesson,parent_unit,paragraph,begin,end):
    require(lesson==expected_lesson and slot==parent_unit)
    return dict(paragraph_index=paragraph,original_unit_indices=[slot],unit_indices=[slot],old_span=[begin,end],
                new_span=[begin,end],replace_spans=[dict(old=[begin,end],new=[begin,end])])
