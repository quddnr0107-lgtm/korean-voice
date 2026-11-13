"""Hash-pinned read-only quality investigation of both completed TB-001 shards.

Only existing encrypted checkpoints are authenticated in the existing protected,
unprivileged offline environment. Nothing is synthesized, approved or published.
"""
import argparse, base64, hashlib, json, os, socket, tempfile
from pathlib import Path
import recovery_diagnostics as d

SCHEMA="bora-tb001-full-sealed-diagnostics/v1"
WORKFLOW=d.REPO+"/.github/workflows/bora-tb001-full-diagnostic.yml@refs/heads/"+d.BRANCH
PIN={
 "lesson_id":"TB-001","run_id":"43275117254","units":58,
 "plan_sha256":"63266c9ae5f234ab5852415952ca889b540b05a0348d6bc954d8613da5ec5f87",
 "recipe_sha256":"d3d35666712d276c0fa60bb5c5b1f7ab7d11c7042d490c95bb6fb8846682502d",
 "render_settings_sha256":"930c8341302f36e0058eeb0deb3a0821cc6177dc6edc162d41547f93299a0822",
 "assets":[
  {"shard":0,"start":0,"end":42,"passed":16,"blocked":26,"id":742113914,
   "name":"TB-001-s000-r43275117254-a1.zip","bytes":81028727,
   "sha256":"fa7dc1bb048009000fa07cf8ffa5a7ac30ec5b88d1395294ded2cddf80ce93eb"},
  {"shard":1,"start":42,"end":58,"passed":11,"blocked":5,"id":741720764,
   "name":"TB-001-s001-r43275117254-a1.zip","bytes":23244525,
   "sha256":"6fb9ed5546420a7b273636061cc9d56326da9d1d1e5dc4bfe1284991281ca657"}]}

def require(ok):
 if not ok: raise ValueError("tb001_diagnostic_integrity_failure")

def check_request():
 obj=json.loads(Path(__file__).with_name("tb001-full-diagnostic-request.json").read_text())
 require(obj=={"schema":SCHEMA,"purpose":"read-only-authenticated-diagnostics",
               "pin":PIN,"synthesis":False,"promotion":False})

def check_context(env,uid,euid):
 require(env.get("GITHUB_REPOSITORY")==d.REPO and env.get("GITHUB_REF")=="refs/heads/"+d.BRANCH
         and env.get("GITHUB_EVENT_NAME")=="push" and env.get("GITHUB_WORKFLOW_REF")==WORKFLOW
         and uid==euid and uid!=0)

def inspect_bundle(directory,asset):
 from transport import validate_bundle
 path=Path(directory)/asset["name"]
 require(path.is_file() and not path.is_symlink() and path.stat().st_size==asset["bytes"])
 with path.open("rb") as f:
  require(hashlib.file_digest(f,"sha256").hexdigest()==asset["sha256"])
 report=validate_bundle(path,"TB")
 require(report["lesson_id"]==PIN["lesson_id"] and str(report["run_id"])==PIN["run_id"]
  and all(report[key]==asset[key] for key in ("shard","start","end","passed","blocked"))
  and report["pending"]==report["timed_out"]==0
  and all(report[key]==PIN[key] for key in
    ("plan_sha256","recipe_sha256","render_settings_sha256")))
 return report

def inventory(directory):
 require([(a["start"],a["end"]) for a in PIN["assets"]]==[(0,42),(42,58)])
 require({p.name for p in Path(directory).glob("*.zip")}=={a["name"] for a in PIN["assets"]})
 reports=[inspect_bundle(directory,a) for a in PIN["assets"]]
 require(sum(x["passed"] for x in reports)==27 and sum(x["blocked"] for x in reports)==31)
 return reports

def download(directory):
 import transport as t
 t.assert_keyless()
 backend=t.GitHubBackend()
 release=t._release(backend,t.tag_for("TB"),create=False)
 require(release is not None)
 assets=t._assets(backend,release)
 output=Path(directory);require(not output.exists());output.mkdir(mode=0o700,parents=True)
 for pin in PIN["assets"]:
  a=assets.get(pin["name"])
  require(a is not None and a["id"]==pin["id"] and a["size"]==pin["bytes"]
          and a.get("digest")=="sha256:"+pin["sha256"])
  backend.download(a,output/pin["name"])
  inspect_bundle(output,pin)
 inventory(output)
 print(json.dumps({"status":"two_ciphertext_bundles_hash_verified",
    "units":58,"uploaded":0,"synthesized":0},sort_keys=True),flush=True)

def project(rows,plan):
 require(set(rows)==set(range(58)) and len(plan["units"])==58)
 sub=[]
 for a in PIN["assets"]:
  part={i:rows[i] for i in range(a["start"],a["end"])}
  s=d.summarize(part,58,0)
  require(s["counts"]["passed_receipts"]==a["passed"] and s["counts"]["blocked_receipts"]==a["blocked"])
  sub.append({"shard":a["shard"],"counts":s["counts"],
              "character_error_bins":s["candidate_character_error_bins"],
              "error_codes":s["candidate_error_codes"]})
 full=d.summarize(rows,58,0)
 require(full["missing_lesson_units"]==0 and full["counts"]["passed_receipts"]==27
         and full["counts"]["blocked_receipts"]==31)
 patterns=d.inspect_disagreements(rows,plan)
 return {"schema":SCHEMA,"status":"read_only_quality_diagnostics_not_a_release",
  "lesson_id":"TB-001","authenticated_units":58,"passed":27,"blocked":31,
  "input_review_units":0,"counts":full["counts"],
  "character_error_bins":full["candidate_character_error_bins"],
  "candidate_error_codes":full["candidate_error_codes"],
  "disagreement_patterns":patterns,"per_shard":sub,
  "production_eligible":False,"checkpoint_written":False,"audio_synthesized":False,
  "asr_rerun":False,"human_listening_verified":False,"causal_attribution_confirmed":False}

def inspect(directory,output):
 check_context(os.environ,os.getuid(),os.geteuid())
 from namespace_guard import validate_snapshot
 validate_snapshot(os.getuid(),os.getuid(),socket.if_nameindex(),
  Path("/proc/net/route").read_text(),os.readlink("/proc/self/ns/net"),
  os.environ.get("BORA_HOST_NETNS",""),Path("/proc/net/ipv6_route").read_text())
 vals=[os.environ.pop(n,"") for n in
  ("ADMIN_LAW_PREMIUM_SOURCE_KEY_B64","ADMIN_LAW_PREMIUM_MEDIA_KEY_B64")]
 source_key,media_key=[base64.b64decode(x,validate=True) for x in vals]
 del vals
 require(len(source_key)==len(media_key)==32 and source_key!=media_key)
 def deny(*args,**kwargs):raise RuntimeError("outbound_forbidden")
 socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.create_connection=deny
 import runtime as r
 campaign,recipe,_,_=r.config()
 require(r.identity(recipe)==PIN["recipe_sha256"])
 inventory(directory)
 with r.quiet(): plan,_=r.source_plan("TB-001",campaign,source_key)
 del source_key
 require(r.identity(plan)==PIN["plan_sha256"] and len(plan["units"])==58
   and not any(u.get("review") or u.get("review_flags") for u in plan["units"]))
 with tempfile.TemporaryDirectory(prefix="bora-private-diagnostic-",dir=os.environ["RUNNER_TEMP"]) as temp:
  with r.quiet():
   rows=r.restore(directory,media_key,plan,PIN["recipe_sha256"],
                  PIN["render_settings_sha256"],Path(temp))
   summary=project(rows,plan)
 del media_key
 d.dump(output,summary)
 print(json.dumps(summary,sort_keys=True),flush=True)

def main():
 os.umask(0o077)
 p=argparse.ArgumentParser();p.add_argument("mode",choices=("download","inspect"))
 p.add_argument("--directory",required=True);p.add_argument("--output")
 args=p.parse_args();check_request()
 if args.mode=="download":require(args.output is None);download(args.directory)
 else:require(bool(args.output));inspect(args.directory,args.output)

if __name__=="__main__":
 try:main()
 except Exception:
  print('{"status":"failed","reason":"tb001_full_diagnostic_validation_failed"}',flush=True)
  raise SystemExit(1)
