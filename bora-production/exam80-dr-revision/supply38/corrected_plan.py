"""Select the independently verified DR-038 supply term sentence in its canonical source."""
from article5.corrected_plan import select_sentence, sentence_ranges

LESSON="DR-038"
KEY="DR-038-SUPPLY"
SOURCE_COMMIT="f543f423a92843a7e47b5dfeab9c428d0553716f"
PINS={LESSON:("ba7e9636f8c7eed1251bac1a8c2cab95dd00d0d662a1de9ac4088a113b602540",
              "4fdf7b1f185027cd63218fe03b284a4297aadd67efe285e15969f1fea439e43c")}
UNIT_SCOPE=("59b0c020a9bcf70158133c28cd6c58f7e5d5ff84828aa3e042b4dc6e11286efe",
            "24df14bfd2ca22ad204f82b1e349a011df2b8fcda5f04b3db53d8050fff91f2f")
SENTENCE=("31496177c4ce23632fb850ddf97a4e1e50f3873b8623dae88d97eae8d3342d27",
          "31496177c4ce23632fb850ddf97a4e1e50f3873b8623dae88d97eae8d3342d27")
BEGIN,END,PARENT_UNIT=2926,2951,36
VARIANT="supply-term"
PRIVATE_PATH="docs/merged-lectures/훈령/본훈_1-49.txt"
REQUEST=dict(schema="exam80-private-acoustic-supplement/v1",lesson=LESSON,source_commit=SOURCE_COMMIT,
             source_sha256=PINS[LESSON][1],unit=PARENT_UNIT,source_span=[BEGIN,END],scope=VARIANT)


def select(plan, original_text, corrected_text, lesson):
    return select_sentence(plan,original_text,corrected_text,lesson,LESSON,PINS,UNIT_SCOPE,SENTENCE,BEGIN,END,PARENT_UNIT)


def extra(lesson,slot):
    return sentence_ranges(lesson,slot,LESSON,PARENT_UNIT,30,BEGIN,END)
