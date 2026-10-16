"""Secret and personal-data scan of every tracked file (and, with --history, every committed version).

    python -m tests.secret_scan            # tracked files (the pre-commit hook runs this, plus staged files)
    python -m tests.secret_scan --history  # every blob ever committed

Prints the file, line, kind and a MASKED value only. Exit 1 on any finding.
It first scans a planted canary and refuses to say "clean" if the canary is not found: an empty
result can mean "read nothing", not "nothing there".
"""
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "openai_key": r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}",
    "anthropic_key": r"sk-ant-[A-Za-z0-9_-]{20,}",
    "google_key": r"AIza[0-9A-Za-z_-]{35}",
    "nvidia_key": r"nvapi-[A-Za-z0-9_-]{20,}",
    "github_token": r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}",
    "pypi_token": r"pypi-[A-Za-z0-9_-]{50,}",
    "slack_token": r"xox[abposr]-[A-Za-z0-9-]{10,}",
    "aws_key": r"AKIA[0-9A-Z]{16}",
    "private_key": "-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY" + "-----",
    "jwt": r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    "assigned_secret": r"(?i)(?:api[_-]?key|secret|token|password|passwd)\s*[:=]\s*['\"][A-Za-z0-9+/_.=-]{20,}['\"]",
    "email": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]*[a-z]{2,}",
    "phone": r"(?<![\d.])(?:\+212|0)[5-7]\d{8}(?!\d)",
    "personal_path": r"[A-Za-z]:[\\/]+Users[\\/]+[A-Za-z0-9_.-]+",
}
# Allowed on purpose: placeholder domains, GitHub privacy addresses, Anthropic's commit trailer address.
ALLOWED_EMAIL = re.compile(r"@(?:example\.(?:com|org|net)|test\.com|anthropic\.com|users\.noreply\.github\.com)$"
                           r"|^noreply@github\.com$", re.I)
SKIP_LINE = "secret-scan: allow"            # put on a line that must contain a pattern (none today)


CONTROL = re.compile("[" + "".join(chr(c) for c in range(32) if c not in (9, 10, 13)) + chr(127) + "]")


def scan_text(text, where, out):
    for i, line in enumerate(text.split("\n"), 1):
        m = CONTROL.search(line)
        if m:                                        # a NUL or other control byte in a text file
            out["control_char"].add((where, i, f"byte {ord(m.group(0))}"))
        if SKIP_LINE in line:
            continue
        for kind, pat in PATTERNS.items():
            for m in re.finditer(pat, line):
                v = m.group(0)
                if kind == "email" and ALLOWED_EMAIL.search(v):
                    continue
                out[kind].add((where, i, f"{v[:4]}...(len {len(v)})"))


BINARY = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".whl", ".gz", ".zip", ".pyc", ".woff", ".woff2")


def is_binary(path: str) -> bool:
    return path.split("@")[0].lower().endswith(BINARY)


def git(*a):
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True).stdout


def canary_ok():
    found = defaultdict(set)
    fake_key = "AKIA" + "Q" * 16
    fake_mail = "some" + "one" + "@" + "realmail.com"
    scan_text(f'k = "{fake_key}"\nmail: {fake_mail}\nx' + chr(0) + "y\n", "canary", found)
    return "aws_key" in found and "email" in found and "control_char" in found


def blobs(history):
    if not history:
        names = [n for n in git("ls-files", "-z").decode().split("\0") if n]
        names += [n for n in git("diff", "--cached", "--name-only", "-z").decode().split("\0") if n]
        for n in sorted(set(names)):
            f = ROOT / n
            if f.is_file():
                yield n, f.read_bytes()
        return
    seen = {}
    for line in git("rev-list", "--all", "--objects").decode(errors="replace").splitlines():
        sha, _, path = line.partition(" ")
        if path:
            seen.setdefault(sha, path)
    for sha, path in seen.items():
        if subprocess.run(["git", "cat-file", "-t", sha], cwd=ROOT, capture_output=True, text=True).stdout.strip() == "blob":
            yield f"{path}@{sha[:8]}", git("cat-file", "-p", sha)


def main():
    if not canary_ok():
        print("secret scan BROKEN: the planted canary was not found; refusing to report clean")
        return 2
    out, files = defaultdict(set), 0
    for where, data in blobs("--history" in sys.argv):
        if is_binary(where):                         # by file type: a NUL inside a .py must be REPORTED, not skipped
            continue
        files += 1
        scan_text(data.decode("utf-8", errors="replace"), where, out)
    if files == 0:
        print("secret scan BROKEN: read 0 files; refusing to report clean")
        return 2
    for kind in sorted(out):
        for row in sorted(out[kind])[:50]:
            print(f"  {kind:16} {row[0]}:{row[1]}  {row[2]}")
    total = sum(len(v) for v in out.values())
    print(f"secret scan: {files} files read, canary found, {total} finding(s)")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
