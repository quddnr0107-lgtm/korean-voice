"""Match reviewed full manuscripts to the original producer's changed speech units."""
from difflib import SequenceMatcher
from hashlib import sha256
import json
from pathlib import Path
import re

PARAGRAPH = re.compile(r"[^\r\n]+(?:\r?\n(?![ \t]*\r?\n)[^\r\n]+)*")
SENTENCE_END = re.compile(r"(?<=[.!?。？！])(?=\s+)")
SCOPES = json.loads(Path(__file__).with_name("unit-scopes.json").read_text())
PINS = {lesson: (scope["original_source_sha256"], scope["source_sha256"]) for lesson, scope in SCOPES.items()}


def digest(value):
    return sha256(value.encode("utf-8")).hexdigest()


def require(test, message):
    if not test:
        raise ValueError(message)


def by_paragraph(plan, match):
    return [(idx, u) for idx, u in enumerate(plan["units"])
            if match.start() <= u["start_cp"] < match.end()]


def bounds(text, rows, end):
    a, b = rows[0][1]["start_cp"], min(rows[-1][1]["end_cp"], end)
    while a < b and text[a].isspace():
        a += 1
    while b > a and text[b-1].isspace():
        b -= 1
    return a, b


def sentence_edges(source, a, b):
    cuts = [a] + [m.start() for m in SENTENCE_END.finditer(source, a, b)] + [b]
    return [(s + len(source[s:e]) - len(source[s:e].lstrip()), e) for s, e in zip(cuts, cuts[1:])]


def edge_spans(texts, regions):
    tokens = [sentence_edges(t,*r) for t,r in zip(texts, regions)]
    values = [[t[a:b] for a,b in ts] for t,ts in zip(texts,tokens)]
    blocks = [z for z in SequenceMatcher(None,*values,autojunk=False).get_opcodes() if z[0] != "equal"]
    require(bool(blocks), "empty_semantic_change")
    # A pure insertion/deletion without a matching acoustic span on one side
    # must be edited as part of the neighboring sentence, never at a guessed point.
    if any(a==b or c==d for _,a,b,c,d in blocks):
        return [{"old":list(regions[0]), "new":list(regions[1])}]
    return [{"old":[tokens[0][a][0], tokens[0][b-1][1]],
             "new":[tokens[1][c][0], tokens[1][d-1][1]]} for _,a,b,c,d in blocks]


def derive(before, after):
    texts = [p["source"] for p in (before,after)]
    pars = [[*PARAGRAPH.finditer(t)] for t in texts]
    require(len(pars[0]) == len(pars[1]), "unreviewed_paragraph_structure")
    rows = []
    for para, (a,b) in enumerate(zip(*pars)):
        if a[0] == b[0]:
            continue
        groups = [by_paragraph(p,m) for p,m in zip((before,after),(a,b))]
        seq = [[(u["source"].strip(),u["spoken"].strip(),u["mode"]) for _,u in g] for g in groups]
        for kind,i,j,k,l in SequenceMatcher(None,*seq,autojunk=False).get_opcodes():
            if kind == "equal":
                continue
            require(i<j and k<l, "requires_individual_insertion_mapping")
            old, new = groups[0][i:j], groups[1][k:l]
            ao,bo = bounds(texts[0],old,a.end())
            an,bn = bounds(texts[1],new,b.end())
            rows.append(dict(slot=new[0][0], paragraph_index=para,
                original_unit_indices=[idx for idx,_ in old],unit_indices=[idx for idx,_ in new],
                old_span=[ao,bo],new_span=[an,bn],
                old_unit_hashes=[[u["source_sha256"],u["spoken_sha256"]] for _,u in old],
                new_unit_hashes=[[u["source_sha256"],u["spoken_sha256"]] for _,u in new],
                replace_spans=edge_spans(texts,[(ao,bo),(an,bn)])))
    require(rows and len({r["slot"] for r in rows})==len(rows), "duplicate_or_empty_scope")
    return rows


def select(plan, original_text, corrected_text, lesson):
    import planning
    import dr_pronunciation
    original = original_text.strip("\r\n") + "\n"
    scope = SCOPES[lesson]
    require((digest(original),digest(corrected_text)) == PINS[lesson] and plan["source"] == corrected_text
            and plan["source_sha256"] == PINS[lesson][1] and plan["lesson_id"] == lesson,
            "source_identity_mismatch")
    old = dr_pronunciation.apply_to_plan(planning.build_plan(lesson,original))
    require(old["review_unit_count"] == 0 and plan["review_unit_count"] == 0,
            "unapproved_pronunciation_or_reading")
    require(len(old["units"]) == scope["original_count"] and len(plan["units"]) == scope["new_count"],
            "unit_count_mismatch")
    rows = derive(old,plan)
    require(rows == scope["groups"], "archived_units_or_semantic_scope_changed")
    selected=[]
    for row in rows:
        units=[(i,plan["units"][i]) for i in row["unit_indices"]]
        for _,u in units:
            require(u["reading_mode_verified"] and not u["review"] and
                    u["source"] == corrected_text[u["start_cp"]:u["end_cp"]]
                    and digest(u["source"]) == u["source_sha256"] and digest(u["spoken"]) == u["spoken_sha256"],
                    "unverified_render_unit")
        selected.append((row["slot"],*row["new_span"],units))
    return selected


def extra(lesson, slot):
    row=next(r for r in SCOPES[lesson]["groups"] if r["slot"] == slot)
    return {k:row[k] for k in ("paragraph_index","original_unit_indices","unit_indices","old_span","new_span","replace_spans")}
