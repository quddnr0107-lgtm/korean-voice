import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.exceptions import InvalidTag

import run
import publish_cipher


def fixture():
    private = X25519PrivateKey.generate()
    raw = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    cfg = dict(schema=run.SCHEMA, lesson_id='TB-001', source_sha256=run.SOURCE_HASH,
        expected_plan_sha256='a'*64, publication='encrypted_requester_preview_only',
        replace_existing=False, unit_count=2, paragraph_count=2, groups=[[0,1],[1,2]],
        receiver_public=run.b64(public), max_chars=180, spoken_replacements=[], critical_terms=[])
    return cfg, raw


class BoundaryTests(unittest.TestCase):
    def test_delivery_rejects_mixed_text_maps_or_runs_but_allows_job_retry_attempts(self):
        cfg,_=fixture()
        cfg['accepted_sources']=[dict(source_sha256='b'*64,expected_plan_sha256='c'*64)]
        header=dict(source_sha256=cfg['source_sha256'],plan_sha256=cfg['expected_plan_sha256'],run_id='123',attempt='1')
        publish_cipher.validate_group_context([header,dict(header,attempt='2')],cfg,'123')
        for other in [dict(header,source_sha256='b'*64,plan_sha256='c'*64),dict(header,run_id='456'),dict(header,attempt='0')]:
            with self.assertRaises(ValueError):
                publish_cipher.validate_group_context([header,other],cfg,'123')

    def test_explicit_request_is_bound_to_exact_configuration(self):
        cfg,_=fixture()
        request=dict(schema='kim-bora-tb-pilot-request/v1',purpose='one-lesson-private-preview',config_sha256=run.digest(run.enc(cfg)))
        run.validate_request(request,cfg)
        with self.assertRaises(run.PilotFailure):
            run.validate_request(request,dict(cfg,max_chars=179))
        with self.assertRaises(run.PilotFailure):
            run.validate_request(dict(request,purpose='replace_site'),cfg)

    def test_config_rejects_missing_overlap_other_lesson_or_replacement(self):
        cfg, _ = fixture()
        run.validate_config(cfg)
        bads = [dict(groups=[[0,1]]), dict(groups=[[0,2],[1,2]]),
                dict(lesson_id='TB-002'), dict(replace_existing=True),
                dict(publication='public_audio'), dict(groups=[[False,1],[1,2]]),
                dict(receiver_public='invalid'), dict(source_sha256='b'*64)]
        for overrides in bads:
            with self.subTest(overrides=overrides), self.assertRaises((ValueError, TypeError)):
                run.validate_config(dict(cfg, **overrides))

    def test_ref_event_repository_and_real_namespace_required(self):
        env = dict(GITHUB_REPOSITORY=run.REPO, GITHUB_REF=run.REF,
                   GITHUB_EVENT_NAME='push', BORA_HOST_NETNS='net:[parent]')
        with patch('run.os.readlink', return_value='net:[isolated]'):
            run.require_context(env)
            for field, bad in [('GITHUB_REPOSITORY','attacker/repo'),
                               ('GITHUB_REF','refs/heads/main'),
                               ('GITHUB_EVENT_NAME','pull_request'),
                               ('BORA_HOST_NETNS','net:[isolated]')]:
                with self.subTest(field=field), self.assertRaises(RuntimeError):
                    run.require_context(dict(env, **{field:bad}))

    def test_source_mismatch_stops_before_building_or_inference(self):
        cfg, _ = fixture()
        with patch('paragraph_plan.build_plan') as build:
            with self.assertRaises(ValueError):
                run.build_reviewed_plan('other manuscript', cfg)
            build.assert_not_called()

    def test_exact_reviewed_plan_and_critical_terms_are_bound(self):
        from paragraph_plan import build_plan
        source = '첫 문단입니다.\n\n다음 문단입니다.'
        cfg, _ = fixture()
        cfg.update(source_sha256=run.digest(source.encode()),
                   spoken_replacements=[['첫','제일']], critical_terms=['제일','없음'])
        expected = build_plan(source, 'TB-001', 180)
        for unit in expected['units']:
            unit['spoken'] = unit['spoken'].replace('첫','제일')
            unit['critical_terms'] = ['제일'] if '제일' in unit['spoken'] else []
        cfg['expected_plan_sha256'] = run.digest(run.enc(expected))
        self.assertEqual(run.build_reviewed_plan(source, cfg), expected)
        cfg['spoken_replacements'] = []
        with self.assertRaises(ValueError):
            run.build_reviewed_plan(source, cfg)

    def test_only_explicit_source_and_plan_pairs_allow_whitespace_variants(self):
        from paragraph_plan import build_plan
        source='첫 문단입니다.\n\n다음 문단입니다.\n'
        cfg,_=fixture()
        cfg.update(source_sha256=run.digest(source.encode()),reading_spans=[])
        def reviewed(value):
            plan=build_plan(value,'TB-001',180)
            for unit in plan['units']:unit['critical_terms']=[]
            return plan
        base=reviewed(source);cfg['expected_plan_sha256']=run.digest(run.enc(base))
        variant=source.removesuffix('\n');p=reviewed(variant)
        cfg['accepted_sources']=[dict(label='remove_final_lf',source_sha256=run.digest(variant.encode()),
            expected_plan_sha256=run.digest(run.enc(p)),reading_spans=[],unit_count=2,paragraph_count=2)]
        self.assertEqual(run.build_reviewed_plan(variant,cfg),p)
        for altered in [source+'\n',source.replace('첫','둘'),source.replace('\n\n','\n')]:
            with self.assertRaises(run.SourceMismatch):run.build_reviewed_plan(altered,cfg)
        bad=copy.deepcopy(cfg);bad['accepted_sources'][0]['expected_plan_sha256']='f'*64
        with self.assertRaises(run.PilotFailure):run.build_reviewed_plan(variant,bad)

    def test_unknown_source_diagnostic_roundtrip_and_domain_separation(self):
        cfg,private=fixture();source='다른 원고입니다.\n'
        envelope=run.seal_source_diagnostic(source,cfg,'123','1')
        decoded=run.open_source_diagnostic(envelope,cfg,private)
        self.assertEqual(decoded['actual_source'],source)
        self.assertEqual(decoded['actual_source_sha256'],run.digest(source.encode()))
        self.assertEqual(decoded['status'],'source_mismatch_render_not_started')
        self.assertNotIn(source,json.dumps(envelope,ensure_ascii=False))
        with self.assertRaises(ValueError):run.open_result(envelope,cfg,0,private)
        audio=run.seal_result(b'audio archive',cfg,0,'123','1')
        with self.assertRaises(ValueError):run.open_source_diagnostic(audio,cfg,private)
        changed=copy.deepcopy(envelope);changed['header']['actual_source_sha256']='f'*64
        with self.assertRaises(InvalidTag):run.open_source_diagnostic(changed,cfg,private)

    def test_source_mismatch_check_emits_only_sealed_diagnostic_no_matrix(self):
        cfg,_=fixture()
        request=dict(schema='kim-bora-tb-pilot-request/v1',purpose='one-lesson-private-preview',config_sha256=run.digest(run.enc(cfg)))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'config.json').write_text(json.dumps(cfg));(root/'request.json').write_text(json.dumps(request))
            output=root/'out';argv=['run.py','--config',str(root/'config.json'),'--check-only','--output',str(output)]
            with patch.object(run,'HERE',root),patch.object(run,'open_plan',side_effect=run.SourceMismatch('unknown source')):
                with patch('sys.argv',argv),patch.dict(os.environ,{'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'}):
                    with patch('sys.stdout',new=io.StringIO()):run.main()
            public=json.loads((output/'plan-public.json').read_text())
            self.assertEqual(public['status'],'source_mismatch_render_not_started')
            self.assertNotIn('matrix',public)
            self.assertEqual(sorted(p.name for p in output.iterdir()),['plan-public.json','source-diagnostic.sealed.json'])
            self.assertNotIn('unknown source',''.join(p.read_text() for p in output.iterdir()))

    def test_roundtrip_binds_group_config_recipient_and_ciphertext(self):
        cfg, private = fixture()
        envelope = run.seal_result(b'private archive', cfg, 0, '123','1')
        self.assertEqual(run.open_result(envelope,cfg,0,private),b'private archive')
        for bad_cfg, group in [(cfg,1),(dict(cfg,max_chars=179),0)]:
            with self.assertRaises(ValueError):
                run.open_result(envelope,bad_cfg,group,private)
        _, other = fixture()
        with self.assertRaises(ValueError):
            run.open_result(envelope,cfg,0,other)
        changed = copy.deepcopy(envelope)
        cipher = bytearray(run.unb(changed['cipher']));cipher[0] ^= 1
        changed['cipher'] = run.b64(bytes(cipher));changed['cipher_sha256'] = run.digest(bytes(cipher))
        with self.assertRaises(InvalidTag):
            run.open_result(changed,cfg,0,private)
        changed = copy.deepcopy(envelope);changed['header']['run_id'] = '456'
        with self.assertRaises(InvalidTag):
            run.open_result(changed,cfg,0,private)
        changed = copy.deepcopy(envelope);changed['header']['quality_status'] = 'blocked_requires_review'
        with self.assertRaises(InvalidTag):
            run.open_result(changed,cfg,0,private)

    def test_archive_whitelist_excludes_candidates_and_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'units/0000').mkdir(parents=True)
            (root/'subset.json').write_text('{}')
            (root/'units/0000/final.wav').write_bytes(b'wave')
            (root/'units/0000/candidate.wav').write_bytes(b'should not be copied')
            manifest={'units':[dict(unit_index=0,relative_file='units/0000/final.wav',blocked=False),dict(blocked=True)]}
            data=run.archive_subset(root,manifest)
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                self.assertEqual(archive.namelist(),['subset.json','units/0000/final.wav'])
            manifest['units'][0]['relative_file']='../escape.wav'
            with self.assertRaises(ValueError):
                run.archive_subset(root,manifest)


if __name__ == '__main__':
    unittest.main()
