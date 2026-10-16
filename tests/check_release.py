"""Ship gate: prove that no internal file is in the repository, its git history, or the built package.

    python -m tests.check_release [dist_dir]

Checks every path in the working tree, in every commit on every branch, and inside every sdist/wheel
in dist_dir, against the list of things that must never ship. It first checks a planted path, so an
empty result cannot mean "looked at nothing". It then runs the secret / personal-data / control-character
scan over the contents of the built packages. Exit 1 on any finding.
"""
import re
import subprocess
import sys
import tarfile
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Paths that must never be published (internal notes, research, the old project, environments, data).
FORBIDDEN = re.compile(r"""(?ix)
    (^|/)(LESSONS|HANDOFF|STATUS|REVIEW|VERIFY_AGENT_LOOP|ARCHITECTURE|ENGINE|PROTECTION|AUTHORSHIP_PRIVATE)(\.md|\.txt)?$
  | (^|/)RESEARCH[^/]*$ | (^|/)docs/research(/|$) | (^|/)research_notes(/|$) | rank2\.py$ | ghsearch\d*\.py$
  | (^|/)(outreach|ops|chatbot|venv|\.venv|core|agent|agents|voice_agents|tui|config|asset_maps|killtest[^/]*)(/|$)
  | (^|/)(server|voice_assistant[^/]*|llm_fallback|websocket_server[^/]*|wadina[^/]*)\.py$
  | (^|/)(paper|mainGoal|theProPrompt|steps|skill_thinking|jsonWorkflow|test_results)[^/]*$
  | \.env$ | memory\.db | \.sqlite(-wal|-shm)?$ | (^|/)\.c4a(/|$) | (^|/)secret$
  | pico\.min\.css$ | (^|/)site/template\.html$
""")


def git(*a):
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True).stdout


def paths_in_tree():
    return {p for p in git("ls-files").splitlines() if p}


def paths_in_history():
    out = set()
    for line in git("log", "--all", "--name-only", "--format=").splitlines():
        if line.strip():
            out.add(line.strip())
    return out


def package_members(dist: Path):
    found = {}
    for f in sorted(dist.glob("*")):
        if f.suffix == ".whl":
            with zipfile.ZipFile(f) as z:
                found[f.name] = {n: z.read(n) for n in z.namelist()}
        elif f.name.endswith(".tar.gz"):
            with tarfile.open(f) as t:
                found[f.name] = {m.name.split("/", 1)[-1]: t.extractfile(m).read() for m in t.getmembers() if m.isfile()}
    return found


def main():
    if not FORBIDDEN.search("docs/research/notes.md") or FORBIDDEN.search("call4agents/ledger.py"):
        print("check_release BROKEN: the forbidden-path pattern does not behave on the planted examples")
        return 2
    bad = defaultdict(list)
    tree, history = paths_in_tree(), paths_in_history()
    if not tree:
        print("check_release BROKEN: no files in the working tree (not a git checkout?)")
        return 2
    for p in sorted(tree):
        if FORBIDDEN.search(p):
            bad["working tree"].append(p)
    for p in sorted(history):
        if FORBIDDEN.search(p):
            bad["git history"].append(p)
    authors = {a for a in git("log", "--all", "--format=%ae%n%ce").splitlines() if a}
    for a in authors:
        if not a.endswith("@users.noreply.github.com") and a not in ("noreply@github.com",):
            bad["commit identities"].append(a)
    scanned = 0
    if len(sys.argv) > 1:
        sys.path.insert(0, str(ROOT))
        from tests.secret_scan import canary_ok, is_binary, scan_text
        if not canary_ok():
            print("check_release BROKEN: secret-scan canary not found")
            return 2
        members = package_members(Path(sys.argv[1]))
        if not members:
            print(f"check_release BROKEN: no .whl or .tar.gz in {sys.argv[1]}")
            return 2
        for pkg, files in members.items():
            hits = defaultdict(set)
            for name, data in files.items():
                if FORBIDDEN.search(name):
                    bad[pkg].append(name)
                if not is_binary(name):
                    scanned += 1
                    scan_text(data.decode("utf-8", errors="replace"), name, hits)
            for kind, rows in hits.items():
                bad[pkg] += [f"{kind}: {r[0]}:{r[1]} {r[2]}" for r in sorted(rows)]
    for where, items in bad.items():
        for i in items[:40]:
            print(f"  {where}: {i}")
    total = sum(len(v) for v in bad.values())
    print(f"check_release: {len(tree)} files in the tree, {len(history)} paths in history, "
          f"{scanned} packaged files scanned, {len(authors)} commit identities; {total} problem(s)")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
