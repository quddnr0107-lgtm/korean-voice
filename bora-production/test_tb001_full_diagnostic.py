"""Synthetic tests; not real synthesis or protected key validation."""
import json,copy,unittest
import tb001_full_diagnostic as f

class QualityTests(unittest.TestCase):
 def rows(self):
  out={}
  for a in f.PIN["assets"]:
   for i in range(a["start"],a["end"]):
    status="passed" if i<a["start"]+a["passed"] else "blocked_requires_review"
    out[i]={"receipt":{"status":status,"review_flags":[],
      "candidates":[{"seed":123,"natural_eos":True,"cap_reached":False,
       "signal":{"blocked":False},
       "asr":{"recognized":"PRIVATE_CANARY_NOT_FOR_PUBLIC_EXPORT",
        "character_errors":0 if status=="passed" else 1,
        "review_required":status!="passed","missing_critical":[]}}]}}
  return out
 def plan(self):
  return {"units":[{"spoken":"시험 문장","mode":"explain"} for _ in range(58)]}
 def test_projection_and_no_text_export(self):
  rows=self.rows();before=copy.deepcopy(rows)
  out=f.project(rows,self.plan())
  self.assertEqual((out["passed"],out["blocked"]),(27,31))
  self.assertEqual(len(out["per_shard"]),2)
  self.assertNotIn("PRIVATE_CANARY",json.dumps(out))
  self.assertFalse(out["production_eligible"])
  self.assertFalse(out["audio_synthesized"])
  self.assertFalse(out["checkpoint_written"])
  self.assertEqual(before,rows)
 def test_missing_or_altered_coverage_rejected(self):
  for change in (lambda r:r.pop(0),
                 lambda r:r.update({58:r[0]}),
                 lambda r:r[0]["receipt"].update(status="blocked_requires_review"),
                 lambda r:r[42]["receipt"].update(status="blocked_requires_review")):
   rows=self.rows();change(rows)
   with self.assertRaises(ValueError):f.project(rows,self.plan())
 def test_scope_is_locked(self):
  f.check_request()
  self.assertEqual([(a["start"],a["end"]) for a in f.PIN["assets"]],[(0,42),(42,58)])
  self.assertEqual(sum(a["passed"] for a in f.PIN["assets"]),27)
  self.assertEqual(sum(a["blocked"] for a in f.PIN["assets"]),31)
 def test_environment_gate(self):
  env={"GITHUB_REPOSITORY":f.d.REPO,"GITHUB_REF":"refs/heads/"+f.d.BRANCH,
       "GITHUB_EVENT_NAME":"push","GITHUB_WORKFLOW_REF":f.WORKFLOW}
  f.check_context(env,1001,1001)
  for key in env:
   wrong=dict(env);wrong[key]="wrong"
   with self.assertRaises(ValueError):f.check_context(wrong,1001,1001)
  with self.assertRaises(ValueError):f.check_context(env,0,0)
 def test_no_render_or_publish_interfaces(self):
  from pathlib import Path
  script=Path(f.__file__).read_text()
  for prohibited in ("FP32Renderer(", "render_unit(", "generate_custom_voice(",
                     "publish(", "delete_asset(", "retry_blocked=True"):
   self.assertNotIn(prohibited,script)

if __name__=="__main__":unittest.main()
