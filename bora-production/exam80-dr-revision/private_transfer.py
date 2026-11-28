"""Transport the two verified Exam80 correction packages to their private production branch."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


OWNER = "quddnr0107-lgtm"
PRIVATE = OWNER + "/yebijun-muhanhoidok234"
BRANCH = "video/exam80-tb001-professional"
PRODUCER = OWNER + "/korean-voice"
PRODUCER_REF = "refs/heads/integrated-lectures-premium-20261028"
SOURCE_COMMIT = "367750f745f0d1873290b9885f790f5484be3cd7"
PRIVATE_HELPER = "production303/corrections/unpack_audio.py"
EXPORTER_COMMIT = "3cd71f9d4578b625438bb1d084ed464cf97e2800"
EXPORTER_SHA256 = "04e2d8c04e06bb30ac958b4a277e332fd887c1e17c7151b4a76c4f5da83857f0"
LESSONS = {
    "DR-052": ("docs/merged-lectures/훈령/본훈_2-8.txt",
               "7c8b3a4140d6e2c33c7671a470909decf858c06479338ab47bb66fed221a8dcc",
               [26, 32, 41, 46, 58, 71]),
    "DR-054": ("docs/merged-lectures/훈령/본훈_2-10.txt",
               "be5637d9242e760f5e1f25f745f662150f7f37b2e49b78de2c51021da20fa37c",
               [30, 33, 56, 58, 59, 62, 70]),
}
from batch3 import config as forms
from batch3.corrected_plan import SCOPES
from batch3.exporter import PRIVATE_PATH, COMMIT as FORMS_EXPORTER_COMMIT, SHA256 as FORMS_EXPORTER_SHA256
for ident, scope_data in SCOPES.items():
    LESSONS[ident] = (forms.PATHS[ident], scope_data["source_sha256"], [r["slot"] for r in scope_data["groups"]])
LIMIT = 32 * 1024**2
PRIVATE_GIT_URL = f"https://github.com/{PRIVATE}.git"


class Invalid(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Invalid(message)


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def git_blob(value):
    return hashlib.sha1(f"blob {len(value)}\0".encode() + value).hexdigest()


def sha1_id(value):
    require(isinstance(value, str) and re.fullmatch("[0-9a-f]{40}", value), "Invalid Git object ID")
    return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class GitHub:
    def __init__(self, token):
        self.opener = build_opener(NoRedirect())
        self.headers = {"Accept": "application/vnd.github+json", "Authorization": "Bearer " + token,
                        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "exam80-private-corrections"}

    def request(self, path):
        base = "/repos/" + PRIVATE
        require(path == base or path.startswith(base + "/"), "GitHub read outside private destination")
        request = Request("https://api.github.com" + path, headers=self.headers)
        try:
            with self.opener.open(request, timeout=90) as response:
                data = response.read(LIMIT * 2 + 1)
            require(len(data) <= LIMIT * 2, "Oversized GitHub response")
            return json.loads(data)
        except HTTPError as exc:
            raise Invalid("GitHub HTTP " + str(exc.code)) from None
        except (URLError, TimeoutError):
            raise Invalid("GitHub transport unavailable") from None

    def api(self, path=""):
        return self.request("/repos/" + PRIVATE + path)

    def check_private(self):
        repo = self.api()
        require(repo.get("full_name") == PRIVATE and repo.get("private") is True
                and repo.get("visibility", "private") == "private" and not repo.get("archived"),
                "Invalid private destination")

    def head(self):
        obj = self.api("/git/ref/heads/" + BRANCH)
        require(obj["ref"] == "refs/heads/" + BRANCH and obj["object"]["type"] == "commit",
                "Private branch identity mismatch")
        return sha1_id(obj["object"]["sha"])

    def fetch_pinned(self, commit, path, digest):
        file = self.api("/contents/" + quote(path, safe="/") + "?" + urlencode({"ref": sha1_id(commit)}))
        require(file.get("type") == "file" and file.get("path") == path
                and file.get("encoding") == "base64" and 0 < file["size"] <= 1024**2,
                "Unexpected private source file")
        data = base64.b64decode("".join(file["content"].splitlines()), validate=True)
        require(len(data) == file["size"] and sha256(data) == digest and git_blob(data) == file["sha"],
                "Private source file identity mismatch")
        return data

    def folder(self, commit, path):
        tree = self.api("/git/commits/" + sha1_id(commit))["tree"]["sha"]
        # Traverse immutable trees, without treating permission errors as missing paths.
        for name in path.split("/") + [None]:
            obj = self.api("/git/trees/" + sha1_id(tree))
            require(not obj["truncated"], "Cannot verify truncated private tree")
            rows = {row["path"]: row for row in obj["tree"]}
            if name is None:
                return rows
            if name not in rows:
                return {}
            require(rows[name]["type"] == "tree" and rows[name]["mode"] == "040000",
                    "Private destination path conflict")
            tree = rows[name]["sha"]

    def read_blob(self, oid):
        obj = self.api("/git/blobs/" + sha1_id(oid))
        require(obj["sha"] == oid and obj["encoding"] == "base64" and 0 < obj["size"] <= LIMIT,
                "Unexpected private blob")
        data = base64.b64decode("".join(obj["content"].splitlines()), validate=True)
        require(len(data) == obj["size"] and git_blob(data) == oid, "Private blob identity mismatch")
        return data

    def append(self, head, lesson, additions):
        return append_with_git(head, lesson, additions, self.headers["Authorization"].removeprefix("Bearer "))


def append_with_git(head, lesson, additions, token):
    """Push one direct child. The local pre-push hook validates Git's exact advertised old OID."""
    sha1_id(head)
    require(lesson in LESSONS and additions, "Invalid private additions")
    dest = f"production303/corrections/incoming/{lesson}/{LESSONS[lesson][1]}/"
    allowed = {"index.json"} | {f"p{p:03}.mp3" for p in LESSONS[lesson][2]}
    require(set(additions) <= {dest + name for name in allowed}, "Private addition outside correction scope")
    ref = "refs/heads/" + BRANCH
    env = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], "LC_ALL": "C",
           "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_AUTHOR_NAME": "github-actions", "GIT_COMMITTER_NAME": "github-actions",
           "GIT_AUTHOR_EMAIL": "github-actions@users.noreply.github.com",
           "GIT_COMMITTER_EMAIL": "github-actions@users.noreply.github.com"}

    with tempfile.TemporaryDirectory(prefix="exam80-git-", dir=os.environ.get("RUNNER_TEMP")) as tmp:
        repo = Path(tmp)

        def git(stage, *args, data=None, network=False, lease=None):
            child = env.copy()
            if network:
                child.update(GH_TOKEN=token, GH_HOST="github.com")
            if lease:
                child.update(EXAM80_REF=ref, EXAM80_PARENT=head, EXAM80_NEW=lease)
            try:
                process = subprocess.run(
                    ["git", "-c", "credential.helper=", "-c", "credential.https://github.com.helper=!gh auth git-credential",
                     "-c", "http.followRedirects=false", "-C", tmp, *args],
                    input=data, capture_output=True, env=child, timeout=180)
            except (OSError, subprocess.TimeoutExpired):
                raise Invalid("Private Git transport unavailable at " + stage) from None
            if process.returncode:
                # Classify only fixed strings. Remote output may contain privileged context.
                diagnostic = (process.stderr + process.stdout).lower()
                labels = [(b"exam80-lease", "head_changed"), (b"non-fast-forward", "head_changed"),
                          (b"fetch first", "head_changed"), (b"stale info", "head_changed"),
                          (b"gh013", "branch_rules"), (b"gh006", "branch_rules"),
                          (b"protected branch", "branch_rules"), (b"authentication failed", "authentication"),
                          (b"permission denied", "permission"), (b"403", "permission"),
                          (b"repository not found", "repository_access"), (b"remote rejected", "remote_rejected")]
                reason = next((label for needle, label in labels if needle in diagnostic), "git_exit")
                raise Invalid(f"Private Git {stage} failed ({reason}, exit {process.returncode})")
            return process.stdout

        git("initialize", "init", "-q", "--bare", ".")
        git("configure", "remote", "add", "origin", PRIVATE_GIT_URL)
        # No working tree, smudge filters, or checkout of existing private premium files.
        git("fetch", "fetch", "-q", "--filter=blob:none", "--no-tags", "--depth=1", "origin", ref, network=True)
        require(git("verify_parent", "rev-parse", "FETCH_HEAD").decode().strip() == head,
                "Private Git head_changed during fetch")
        git("load_tree", "read-tree", head)
        for path, value in sorted(additions.items()):
            oid = git("write_blob", "hash-object", "-w", "--stdin", data=value).decode().strip()
            require(sha1_id(oid) == git_blob(value), "Private Git local blob differs")
            git("stage", "update-index", "--add", "--cacheinfo", f"100644,{oid},{path}")
        tree = sha1_id(git("write_tree", "write-tree", "--missing-ok").decode().strip())
        commit = sha1_id(git("commit", "commit-tree", tree, "-p", head, "-m",
                            "Store verified correction audio for " + lesson).decode().strip())
        require(git("verify_commit", "rev-parse", commit + "^").decode().strip() == head,
                "Private commit must be a direct child of the expected parent")
        changed = git("verify_paths", "diff-tree", "-r", "--no-renames", "--no-commit-id",
                      "--name-status", "-z", head, commit).split(b"\0")
        require(changed[-1] == b"" and changed[:-1:2] == [b"A"] * len(additions) and
                sorted(p.decode() for p in changed[1:-1:2]) == sorted(additions),
                "Private commit may only add specified correction paths")
        hook = repo / "hooks" / "pre-push"
        hook.write_text("#!/bin/sh\nset -eu\n"
                        "read -r localref new target old\n"
                        "if [ \"$target\" != \"$EXAM80_REF\" ] || [ \"$new\" != \"$EXAM80_NEW\" ] || "
                        "[ \"$old\" != \"$EXAM80_PARENT\" ] || read -r extra; then\n"
                        " echo exam80-lease >&2; exit 1\nfi\n")
        hook.chmod(0o700)
        git("push", "push", "--porcelain", "origin", f"{commit}:{ref}", network=True, lease=commit)
        return commit


def scope(path):
    root = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    require(path.is_absolute() and path.resolve().is_relative_to(root) and path.resolve() != root
            and all(not part.is_symlink() for part in (path, *path.parents)), "Unexpected temporary path")


def fetch(gh, args):
    gh.check_private()
    source_path, source_hash, _ = LESSONS[args.lesson]
    commit, digest, helper = EXPORTER_COMMIT, EXPORTER_SHA256, PRIVATE_HELPER
    if args.lesson in forms.COMMITS:
        commit, digest, helper = FORMS_EXPORTER_COMMIT, FORMS_EXPORTER_SHA256, PRIVATE_PATH
    require((args.exporter_commit or commit) == commit and (args.exporter_sha256 or digest) == digest,
            "Invalid private exporter revision")
    files = {
        "unpack_audio.py": gh.fetch_pinned(commit, helper, digest),
        "corrected-source.txt": gh.fetch_pinned(forms.COMMITS.get(args.lesson, SOURCE_COMMIT), source_path, source_hash),
    }
    args.output.mkdir(mode=0o700, parents=False, exist_ok=False)
    for name, data in files.items():
        (args.output / name).write_bytes(data)


def verified_files(folder, lesson):
    _, source_hash, paragraphs = LESSONS[lesson]
    expected = {"index.json"} | {f"p{p:03}.mp3" for p in paragraphs}
    entries = list(folder.iterdir())
    require(set(p.name for p in entries) == expected and all(p.is_file() and not p.is_symlink()
            for p in entries) and sum(p.stat().st_size for p in entries) <= LIMIT,
            "Unexpected verified export files")
    contents = {p.name: p.read_bytes() for p in entries}
    index = json.loads(contents["index.json"])
    require(index["schema"] == "exam80-dr-correction-audio/v1" and index["lesson_id"] == lesson and
            index["source_sha256"] == source_hash and index["private_source_commit"] == forms.COMMITS.get(lesson, SOURCE_COMMIT)
            and [s["paragraph"] for s in index["segments"]] == paragraphs, "Private export index mismatch")
    for row in index["segments"]:
        name = f"p{row['paragraph']:03}.mp3"
        require(row["file"] == name and row["sha256"] == sha256(contents[name]) and
                type(row["decoded_samples"]) is int and row["decoded_samples"] > 0 and row["unit_checks"],
                "Private export segment mismatch")
    return contents


def store(gh, lesson, contents):
    gh.check_private()
    dest = f"production303/corrections/incoming/{lesson}/{LESSONS[lesson][1]}"
    receipts = {name: git_blob(data) for name, data in contents.items()}

    def existing(commit):
        rows = gh.folder(commit, dest)
        require(rows.keys() <= contents.keys() and all(row["type"] == "blob" and row["mode"] == "100644"
                and row["sha"] == receipts[name] for name, row in rows.items()),
                "Private destination already contains a different verified package")
        return rows

    for attempt in range(5):
        head = gh.head()
        rows = existing(head)
        if len(rows) == len(receipts):
            result = head
            break
        additions = {dest + "/" + name: contents[name] for name in contents.keys() - rows.keys()}
        try:
            result = gh.append(head, lesson, additions)
            break
        except Invalid:
            # Reconcile a lost response or another editor's commit; never force or retry an unchanged error.
            if gh.head() == head or attempt == 4:
                raise
    rows = existing(result)
    require(rows.keys() == receipts.keys(), "Incomplete read-back at private commit")
    for name, row in rows.items():
        require(gh.read_blob(row["sha"]) == contents[name], "Bytes differ at private commit")
    current = gh.head()
    if current != result:
        require(existing(current).keys() == receipts.keys(), "Private branch no longer contains verified package")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    get = actions.add_parser("fetch")
    get.add_argument("--output", required=True, type=Path)
    get.add_argument("--exporter-commit")
    get.add_argument("--exporter-sha256")
    put = actions.add_parser("store")
    put.add_argument("--verified", required=True, type=Path)
    for action in (get, put):
        action.add_argument("--lesson", required=True, choices=LESSONS)
    args = parser.parse_args()
    os.umask(0o077)
    require(os.environ.get("GITHUB_REPOSITORY") == PRODUCER and os.environ.get("GITHUB_REF") == PRODUCER_REF
            and os.environ.get("GITHUB_EVENT_NAME") == "push" and os.environ.get("GITHUB_WORKFLOW_REF") ==
            f"{PRODUCER}/.github/workflows/bora-fp32-single-pass.yml@{PRODUCER_REF}",
            "Private transfer outside original producer workflow")
    require(not any(os.environ.get(k) for k in ("MEDIA_KEY", "ADMIN_LAW_PREMIUM_MEDIA_KEY_B64",
            "ADMIN_LAW_PREMIUM_SOURCE_KEY_B64")), "Media or source key present during network step")
    token = os.environ.pop("YEBIJUN_AUDIO_WRITE_TOKEN", "")
    require(bool(token), "Private destination credential unavailable")
    gh = GitHub(token)
    if args.action == "fetch":
        scope(args.output)
        fetch(gh, args)
    else:
        scope(args.verified)
        store(gh, args.lesson, verified_files(args.verified, args.lesson))
        print(f"{args.lesson} verified private package stored and read back")


if __name__ == "__main__":
    try:
        main()
    except Invalid as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except Exception:
        print("Private correction transport failed validation", file=sys.stderr)
        sys.exit(1)
