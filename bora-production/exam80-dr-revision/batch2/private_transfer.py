"""Transport the two verified Exam80 correction packages to their private production branch."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


OWNER = "quddnr0107-lgtm"
PRIVATE = OWNER + "/yebijun-muhanhoidok234"
BRANCH = "video/exam80-tb001-professional"
PRODUCER = OWNER + "/korean-voice"
PRODUCER_REF = "refs/heads/integrated-lectures-premium-20261028"
SOURCE_COMMITS = {
    "DR-038": "f543f423a92843a7e47b5dfeab9c428d0553716f",
    "DR-047": "6ed18c9353a023b7b5287867a3ccefeef0c583a8",
}
PRIVATE_HELPER = "production303/corrections/unpack_audio.py"
EXPORTER_COMMIT = "2975f3c2edc6fb756a06bf36e7706e90774aa8c0"
EXPORTER_SHA256 = "4dc0345020792cbbdb275308b53c279597c3b193d4febb81e130399bd7ec4a62"
LESSONS = {
    "DR-038": ("docs/merged-lectures/훈령/본훈_1-49.txt",
               "4fdf7b1f185027cd63218fe03b284a4297aadd67efe285e15969f1fea439e43c",
               [113]),
    "DR-047": ("docs/merged-lectures/훈령/본훈_2-3의3.txt",
               "fb0ab938e574a31c3c70549f4e76c6c3b7c6001e98d134b5b50e86aa83842700",
               [0, 1]),
}
LIMIT = 32 * 1024**2
MUTATION = """mutation($input:CreateCommitOnBranchInput!){
    createCommitOnBranch(input:$input){commit{oid}}}"""


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

    def request(self, path, body=None):
        assert path == "/graphql" or path.startswith("/repos/" + PRIVATE)
        raw = None if body is None else json.dumps(body, separators=(",", ":")).encode()
        request = Request("https://api.github.com" + path, data=raw, headers=self.headers)
        if body is not None:
            request.add_header("Content-Type", "application/json")
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
        value = {"branch": {"repositoryNameWithOwner": PRIVATE, "refName": "refs/heads/" + BRANCH},
                 "expectedHeadOid": sha1_id(head),
                 "message": {"headline": "Store verified correction audio for " + lesson},
                 "fileChanges": {"additions": additions}}
        result = self.request("/graphql", {"query": MUTATION, "variables": {"input": value}})
        require(not result.get("errors"), "Private commit declined by GitHub")
        return sha1_id(result["data"]["createCommitOnBranch"]["commit"]["oid"])


def scope(path):
    root = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    require(path.is_absolute() and path.resolve().is_relative_to(root) and path.resolve() != root
            and all(not part.is_symlink() for part in (path, *path.parents)), "Unexpected temporary path")


def fetch(gh, args):
    gh.check_private()
    source_path, source_hash, _ = LESSONS[args.lesson]
    require(args.exporter_commit == EXPORTER_COMMIT and args.exporter_sha256 == EXPORTER_SHA256,
            "Invalid private exporter revision")
    files = {
        "unpack_audio.py": gh.fetch_pinned(args.exporter_commit, PRIVATE_HELPER, args.exporter_sha256),
        "corrected-source.txt": gh.fetch_pinned(SOURCE_COMMITS[args.lesson], source_path, source_hash),
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
            index["source_sha256"] == source_hash and index["private_source_commit"] == SOURCE_COMMITS[lesson]
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
        additions = [{"path": dest + "/" + name, "contents": base64.b64encode(contents[name]).decode("ascii")}
                     for name in sorted(contents.keys() - rows.keys())]
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
    get.add_argument("--exporter-commit", default=EXPORTER_COMMIT)
    get.add_argument("--exporter-sha256", default=EXPORTER_SHA256)
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
