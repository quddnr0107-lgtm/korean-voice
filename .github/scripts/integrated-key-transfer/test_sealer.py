import hashlib,unittest
from unittest.mock import patch
import transfer_protocol as p
import sealer as s
MEDIA=bytes(range(32));NOW=1793180000
class SealerTests(unittest.TestCase):
    def world(self):
        receiver=p.Receiver('123','1',NOW,workflow_sha='a'*40);h=receiver.header
        env={'GITHUB_REPOSITORY':s.PUBLIC_REPO,'GITHUB_REF':s.PUBLIC_REF,'GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_RUN_ATTEMPT':'1','GITHUB_ACTOR':'quddnr0107-lgtm','GITHUB_TRIGGERING_ACTOR':'quddnr0107-lgtm','GITHUB_SHA':'b'*40,'GITHUB_WORKFLOW_SHA':'b'*40,'REVIEWED_SEALER_SHA':'b'*40,'OWNER_VERIFIED_PRIVATE_RUN':'true','PUBLIC_RECEIVER_HEADER':p.canonical(h).decode(),'VERIFIED_RECEIVER_RUN_ID':'123','VERIFIED_HEADER_SHA256':hashlib.sha256(p.canonical(h)).hexdigest(),'ADMIN_LAW_PREMIUM_MEDIA_KEY_B64':p.b64(MEDIA)}
        return receiver,env
    def armed(self):return patch.multiple(s,EXPECTED_RECEIVER_WORKFLOW_SHA='a'*40,WINDOW_START=NOW-10,WINDOW_END=NOW+600)
    def test_default_disabled_without_touching_MEDIA(self):
        _,env=self.world()
        with self.assertRaisesRegex(p.TransferError,'disabled'):s.prepare_packet(env,NOW)
        self.assertIn('ADMIN_LAW_PREMIUM_MEDIA_KEY_B64',env)
    def test_owner_verified_run_only_roundtrip(self):
        receiver,env=self.world()
        with self.armed():name,raw=s.prepare_packet(env,NOW)
        self.assertTrue(name.startswith('receiver-123-'));self.assertEqual(receiver.consume(raw,NOW),MEDIA);self.assertNotIn('ADMIN_LAW_PREMIUM_MEDIA_KEY_B64',env);self.assertNotIn(p.b64(MEDIA).encode(),raw)
    def test_other_actor_branch_or_event_no_export(self):
        for key,value in [('GITHUB_RUN_ATTEMPT','2'),('GITHUB_TRIGGERING_ACTOR','other'),('GITHUB_ACTOR','attacker'),('GITHUB_REF','refs/heads/main'),('GITHUB_EVENT_NAME','pull_request'),('GITHUB_REPOSITORY','other/repo'),('REVIEWED_SEALER_SHA','c'*40),('OWNER_VERIFIED_PRIVATE_RUN','false')]:
            _,env=self.world();env[key]=value
            with self.subTest(key=key),self.armed(),self.assertRaises(p.TransferError):s.prepare_packet(env,NOW)
            self.assertIn('ADMIN_LAW_PREMIUM_MEDIA_KEY_B64',env)
    def test_substituted_public_key_fails_independently_verified_receipt(self):
        _,env=self.world();attacker=p.Receiver('123','1',NOW,workflow_sha='a'*40);env['PUBLIC_RECEIVER_HEADER']=p.canonical(attacker.header).decode()
        with self.armed(),self.assertRaisesRegex(p.TransferError,'receipt'):s.prepare_packet(env,NOW)
        self.assertIn('ADMIN_LAW_PREMIUM_MEDIA_KEY_B64',env)
    def test_wrong_private_run_or_private_workflow_no_export(self):
        for field,value in [('VERIFIED_RECEIVER_RUN_ID','999'),('VERIFIED_HEADER_SHA256','0'*64)]:
            _,env=self.world();env[field]=value
            with self.armed(),self.assertRaises(p.TransferError):s.prepare_packet(env,NOW)
        _,env=self.world()
        with self.armed(),patch.object(s,'EXPECTED_RECEIVER_WORKFLOW_SHA','c'*40),self.assertRaises(p.TransferError):s.prepare_packet(env,NOW)
    def test_window_expiry_and_too_broad_window_no_export(self):
        _,env=self.world()
        with self.armed(),self.assertRaises(p.TransferError):s.prepare_packet(env,NOW+601)
        with self.armed(),patch.object(s,'WINDOW_END',NOW+1000),self.assertRaises(p.TransferError):s.prepare_packet(env,NOW)
    def test_malformed_duplicate_or_oversized_header_no_export(self):
        for raw in ['{}','[]','{','x'*2049,'{"version":"a","version":"b"}']:
            _,env=self.world();env['PUBLIC_RECEIVER_HEADER']=raw
            with self.armed(),self.assertRaises(p.TransferError):s.prepare_packet(env,NOW)
            self.assertIn('ADMIN_LAW_PREMIUM_MEDIA_KEY_B64',env)
if __name__=='__main__':unittest.main()
