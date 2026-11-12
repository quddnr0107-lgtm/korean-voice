import copy
import unittest

import run
from test_run import fixture
from paragraph_plan import build_plan


def planned(source, prefixes, reading_spans=(), max_chars=180):
    cfg,_=fixture()
    plan=build_plan(source,'TB-001',max_chars,reading_spans)
    for unit in plan['units']:unit['critical_terms']=[]
    for item in prefixes:
        unit=plan['units'][item['unit_index']]
        unit['source_spoken']=unit['spoken']
        unit['delivery_prefix']=item['prefix']
        unit['spoken']=item['prefix']+unit['source_spoken']
    cfg.update(source_sha256=run.digest(source.encode()),max_chars=max_chars,
        unit_count=len(plan['units']),paragraph_count=len(plan['paragraphs']),
        groups=[[i,i+1] for i in range(len(plan['units']))],
        reading_spans=list(reading_spans),delivery_prefixes=prefixes,
        expected_plan_sha256=run.digest(run.enc(plan)))
    return cfg,plan


class DeliveryPrefixTests(unittest.TestCase):
    def test_two_explicit_prefixes_preserve_exact_raw_source_offsets_and_modes(self):
        source='질문을 시작합니다.\n문장을 나누어 보겠습니다.'
        cfg,expected=planned(source,[{'unit_index':0,'prefix':'자, '},{'unit_index':1,'prefix':'음, '}])
        actual=run.build_reviewed_plan(source,cfg)
        self.assertEqual(actual,expected)
        base=build_plan(source,'TB-001',180)
        self.assertEqual(actual['source_sha256'],base['source_sha256'])
        for before,after in zip(base['units'],actual['units']):
            for field in ['source','source_sha256','start_utf16','end_utf16','mode','paragraph_id']:
                self.assertEqual(before[field],after[field])
            self.assertEqual(after['source_spoken'],before['spoken'])
            self.assertEqual(after['spoken'],after['delivery_prefix']+before['spoken'])

    def test_config_rejects_duplicates_out_of_range_boolean_and_arbitrary_prefixes(self):
        cfg,_=fixture()
        cases=[None,[{'unit_index':0,'prefix':'그래서, '}],
            [{'unit_index':False,'prefix':'자, '}],[{'unit_index':-1,'prefix':'자, '}],
            [{'unit_index':2,'prefix':'자, '}],
            [{'unit_index':0,'prefix':'자, '},{'unit_index':0,'prefix':'음, '}],
            [{'unit_index':0,'prefix':'자, ','spoken':'다른 대본'}],
            [{'unit_index':i,'prefix':'자, '} for i in range(5)]]
        for value in cases:
            with self.subTest(value=value),self.assertRaises(ValueError):
                run.validate_config(dict(cfg,delivery_prefixes=value))

    def test_reading_and_later_unit_in_same_paragraph_are_rejected(self):
        reading='법문을 읽습니다.'
        cfg,_=planned(reading,[{'unit_index':0,'prefix':'자, '}],[(0,len(reading))])
        with self.assertRaises(ValueError):run.build_reviewed_plan(reading,cfg)
        source='첫 문장을 충분히 길게 이어서 설명하는 문장입니다. 다음 문장은 이어지는 설명입니다.'
        cfg,plan=planned(source,[{'unit_index':1,'prefix':'음, '}],max_chars=40)
        self.assertGreater(len(plan['units']),1)
        with self.assertRaises(ValueError):run.build_reviewed_plan(source,cfg)

    def test_existing_filler_is_not_repeated(self):
        for text in ['자, 다시 보겠습니다.','음, 다시 보겠습니다.','음… 다시 보겠습니다.']:
            cfg,_=planned(text,[{'unit_index':0,'prefix':'자, '}])
            with self.subTest(text=text),self.assertRaises(ValueError):
                run.build_reviewed_plan(text,cfg)

    def test_source_rewrite_or_unreviewed_prefix_change_rejected_by_pinned_plan(self):
        source='설명입니다.'
        cfg,_=planned(source,[{'unit_index':0,'prefix':'자, '}])
        wrong=copy.deepcopy(cfg);wrong['delivery_prefixes'][0]['prefix']='음, '
        with self.assertRaises(run.PilotFailure):run.build_reviewed_plan(source,wrong)
        with self.assertRaises(run.SourceMismatch):run.build_reviewed_plan(source+' 추가 문장.',cfg)


if __name__=='__main__':unittest.main()
