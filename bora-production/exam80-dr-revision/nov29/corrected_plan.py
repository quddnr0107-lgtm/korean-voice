"""Full source proof for DR-059 and one pronunciation-only DR-038 boundary."""
from pathlib import Path
import json
from batch3 import corrected_plan as ledger
from supply38 import corrected_plan as supply
from . import config

SCOPE=json.loads(Path(__file__).with_name('059-unit-scope.json').read_text())
PINS={config.NEW:(SCOPE['original_source_sha256'],SCOPE['source_sha256']),**supply.PINS}
SPOKEN='5407329e4e84f70dbf2fef8f2d3154a20b3b9bc3a620b41533f5dd940c982668'


def select(plan, original_text, corrected_text, lesson):
    if lesson == config.NEW:
        return ledger.select_scope(plan,original_text,corrected_text,lesson,SCOPE)
    ledger.require(lesson==supply.LESSON,'unknown_reading_fix')
    (slot,start,end,units),=supply.select(plan,original_text,corrected_text,lesson)
    (unit_id,unit),=units
    old=unit['spoken']
    # The visible official script remains fixed; only a word boundary is supplied to the voice engine.
    reading=old[:2]+' '+old[2:]
    ledger.require(ledger.digest(old)==supply.SENTENCE[1] and ledger.digest(reading)==SPOKEN and
                   reading[:2]+reading[3:]==old and len(reading)==len(old)+1,
                   'unreviewed_pronunciation_edit')
    proof=dict(method='source-bound spoken whitespace',offset=2,before=supply.SENTENCE[1],after=SPOKEN)
    normalized=dict(unit['normalization'],spoken_sha256=SPOKEN,supplemental_pronunciation=proof)
    revised=dict(unit,spoken=reading,spoken_sha256=SPOKEN,normalization=normalized)
    return [(slot,start,end,[(unit_id,revised)])]


def extra(lesson,slot):
    if lesson == config.NEW:
        return ledger.extra_scope(SCOPE,slot)
    return supply.extra(lesson,slot)
