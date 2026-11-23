#!/usr/bin/env python3
"""Which radios' CI a change needs. Each radio's workflow (k6-firmware.yml, uv380.yml,
uv380-openrtx.yml, rt950.yml, uv21.yml) starts with a scope job that runs this; the radio's build and test
jobs run only when it says yes, and show as skipped otherwise (a skipped job counts as passed).
The [bench] section (the Pluto and hamlet bench runner tools, bench-tools.yml) works the same
way: this tool treats it as one more radio, whose workflow is a light Python test job.

The map is .github/ci-radios.txt: one section per radio, listing the paths it builds from.
A pull request runs a radio's jobs when it changes a file in that radio's section. Everything
runs on a push to main, a pushed tag (a release), the weekly run, "Run workflow" (any branch), and a
pull request labelled ci-full (add the label, then re-run the jobs or push). A changed file the map doesn't place
runs every radio, so a gap in the map costs time, never a missed test.

  python3 tools/ci_scope.py                    # what this branch's PR would run (against origin/main)
  python3 tools/ci_scope.py --base REF         # the same against another base
  python3 tools/ci_scope.py --files a.c b.py   # which radios these paths run
  python3 tools/ci_scope.py --check            # the map is complete and true (Forbidden files job)
  python3 tools/ci_scope.py --github k6        # in CI: run=true|false for one radio

--check fails on a line that matches no file (a rename left it behind), a tracked file no section
places, or a dependency the map misses: a radio's test files (as its workflow lists them) and the
Python modules they import, and the C files a radio's sources #include, must all be in its section.

SPDX-License-Identifier: MIT
"""

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP = os.path.join(ROOT, ".github", "ci-radios.txt")
FULL_LABEL = "ci-full"
# events that always run every radio
FULL_EVENTS = (
    "push",
    "schedule",
    "workflow_dispatch",
)
# sections that aren't radios
IGNORE = "ignore"
ALL = "all"
C_EXT = (
    ".c",
    ".h",
    ".inc",
    ".S",
)
INCLUDE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.M)
WORKFLOW_TEST = re.compile(r"\btests/test_\w+\.py\b")


def glob_regex(pat):
    """A map line as a regex over a path: * and ? stay inside one folder, ** crosses folders."""
    out, i = "", 0
    while i < len(pat):
        if pat.startswith("**", i):
            out += ".*"
            i += 2
            if pat.startswith("/", i):
                out = out[:-2] + "(?:.*/)?"
                i += 1
        elif pat[i] == "*":
            out += "[^/]*"
            i += 1
        elif pat[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pat[i])
            i += 1
    return re.compile(out + r"\Z")


class Rule:
    def __init__(self, line, lineno):
        self.raw = line
        self.lineno = lineno
        self.neg = line.startswith("!")
        self.pat = line[1:] if self.neg else line
        self.rx = glob_regex(self.pat.rstrip("/"))
        # a folder line (ending /) or a plain name takes everything inside it; a line with a
        # wildcard matches paths themselves only, so c/* is the files in c/, not c/k6/...
        self.folder = self.pat.endswith("/") or not any(ch in self.pat for ch in "*?")

    def hits(self, path):
        if self.rx.match(path):
            return True
        if not self.folder:
            return False
        parts = path.split("/")
        return any(self.rx.match("/".join(parts[:n])) for n in range(len(parts) - 1, 0, -1))


class Map:
    def __init__(self, path=MAP):
        self.path = path
        self.sections = {}
        cur = None
        with open(path, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                m = re.fullmatch(r"\[([a-z0-9-]+)\]", line)
                if m:
                    cur = m.group(1)
                    if cur in self.sections:
                        raise SystemExit(f"{path}:{n}: section [{cur}] twice")
                    self.sections[cur] = []
                elif cur is None:
                    raise SystemExit(f"{path}:{n}: a path before the first [section]")
                else:
                    self.sections[cur].append(Rule(line, n))
        self.radios = [s for s in self.sections if s not in (IGNORE, ALL)]

    def in_section(self, name, path):
        """gitignore style: the last line that matches decides, and a ! line takes the path out"""
        took = False
        for r in self.sections.get(name, ()):
            if r.hits(path):
                took = not r.neg
        return took

    def place(self, path):
        """(radios, how): how is 'ignored', 'all', 'mapped' or 'unmapped' (unmapped runs every radio)"""
        if self.in_section(IGNORE, path):
            return [], "ignored"
        if self.in_section(ALL, path):
            return list(self.radios), "all"
        got = [r for r in self.radios if self.in_section(r, path)]
        if got:
            return got, "mapped"
        return list(self.radios), "unmapped"

    def scope(self, changed):
        """{radio: [the changed paths that run it]}, and the paths no section places"""
        why = {r: [] for r in self.radios}
        unmapped = []
        for p in changed:
            radios, how = self.place(p)
            if how == "unmapped":
                unmapped.append(p)
            for r in radios:
                why[r].append(p)
        return why, unmapped

    def workflow(self, radio):
        """the radio's own workflow file, the one .github/workflows/ line in its section"""
        wf = [r.pat for r in self.sections[radio] if not r.neg and r.pat.startswith(".github/workflows/")]
        return wf[0] if len(wf) == 1 else None


# The radio workflows' own path filters (on: push / pull_request: paths:). A change that touches
# only [ignore] paths (docs) starts no radio workflow at all, not even its scope job, and a push of
# such a change to main builds nothing (Jeff, 2026-11-19: "Docs only change is invoking CI"). The
# filter is [ignore] turned inside out: everything, less each [ignore] line, plus its ! lines, in
# the map's order (GitHub's filters also let the last match decide). `--paths` prints it; `--check`
# checks every radio workflow carries exactly this list on both events.
FILTER_EVENTS = (
    "push",
    "pull_request",
)


def gh_paths(m):
    """the GitHub `paths:` list that leaves out what [ignore] takes"""
    out = ["**"]
    for r in m.sections.get(IGNORE, ()):
        p = r.pat.rstrip("/")
        if r.pat.endswith("/"):
            pats = [p + "/**"]
        elif r.folder and os.path.isdir(os.path.join(ROOT, p)):
            pats = [p, p + "/**"]  # a plain name that is a folder here
        else:
            pats = [p]
        if p.startswith("**/"):
            pats.append(p[3:])  # ** with a slash after it also means no folder at all
        out += [q if r.neg else "!" + q for q in pats]
    return out


def workflow_paths(src):
    """{event: [paths]} from a workflow's on: block (a plain reader for the layout used here)"""
    lines = src.splitlines()
    try:
        i = lines.index("on:")
    except ValueError:
        return {}
    got, ev, inpaths = {}, None, False
    for line in lines[i + 1:]:
        if line and not line.startswith(" ") and not line.startswith("#"):
            break  # the next top-level key
        body = line.split(" #", 1)[0].rstrip()
        if not body.strip() or body.strip().startswith("#"):
            continue
        ind = len(body) - len(body.lstrip())
        if ind == 2:
            ev = body.strip().rstrip(":")
            inpaths = False
        elif ind == 4:
            inpaths = body.strip() == "paths:"
            if inpaths:
                got[ev] = []
        elif inpaths and body.strip().startswith("- "):
            got[ev].append(body.strip()[2:].strip().strip("'\""))
    return got


def git(*args):
    return subprocess.run(["git", "-C", ROOT, *args], check=True, capture_output=True, text=True).stdout


def tracked():
    """every tracked path, the submodule (sotto) included as one path"""
    return [p for p in git("ls-files", "-z").split("\0") if p]


def submodules():
    return [line.split("\t", 1)[1] for line in git("ls-files", "-s").splitlines() if line.startswith("160000 ")]


# --check: the map against the tree


def py_imports(path):
    """module names a Python file imports (absolute imports; the tests put ROOT, tools/ and
    tests/ on sys.path themselves)"""
    try:
        tree = ast.parse(open(os.path.join(ROOT, path), encoding="utf-8").read())
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []
    names = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            names += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
            names.append(n.module)
            names += [n.module + "." + a.name for a in n.names]
    return names


def py_resolve(name, importer, files):
    parts = name.split(".")
    for base in ("", os.path.dirname(importer), "tools", "tests"):
        stem = "/".join(p for p in (base, *parts) if p)
        for cand in (stem + ".py", stem + "/__init__.py"):
            if cand in files:
                return cand
    return None


def py_closure(starts, files):
    """{module file: the file that imports it}, from the start files down"""
    seen = {s: None for s in starts if s in files}
    todo = list(seen)
    while todo:
        f = todo.pop()
        for name in py_imports(f):
            dep = py_resolve(name, f, files)
            if dep and dep not in seen:
                seen[dep] = f
                todo.append(dep)
    return seen


def c_includes(path, files, subs):
    """the repository files a C source #includes "like/this", resolved from its own folder"""
    full = os.path.join(ROOT, path)
    if os.path.islink(full):
        return []
    try:
        src = open(full, encoding="utf-8", errors="replace").read()
    except OSError:
        return []
    out = []
    for inc in INCLUDE.findall(src):
        p = os.path.normpath(os.path.join(os.path.dirname(path), inc)).replace(os.sep, "/")
        if p.startswith("../"):
            continue
        if p in files or any(p == s or p.startswith(s + "/") for s in subs):
            out.append(p)
    return out


def check(m):
    errors = []
    files = tracked()
    fileset = set(files)
    subs = submodules()

    # every line still matches something (a renamed folder leaves its line behind)
    for sec, rules in m.sections.items():
        for r in rules:
            if not any(r.hits(p) for p in files):
                errors.append(f"{m.path}:{r.lineno}: [{sec}] {r.raw} matches no tracked file")

    # every tracked file has a place
    for p in files:
        if m.place(p)[1] == "unmapped":
            errors.append(f"{p}: in no section of .github/ci-radios.txt (add it to the radios that use it, or [ignore])")

    for radio in m.radios:
        wf = m.workflow(radio)
        if not wf or wf not in fileset:
            errors.append(f"[{radio}]: needs exactly one .github/workflows/ line, its own workflow")
            continue

        def missing(p):
            radios, how = m.place(p)
            return how != "ignored" and radio not in radios

        # the tests its workflow runs, and every module they import
        src = open(os.path.join(ROOT, wf), encoding="utf-8").read()
        want, have = gh_paths(m), workflow_paths(src)
        for ev in FILTER_EVENTS:
            if have.get(ev) != want:
                errors.append(f"{wf}: on.{ev}.paths isn't [ignore]'s filter (tools/ci_scope.py --paths prints it)")
        if not re.search(r"(?m)^    tags:", src):
            errors.append(f"{wf}: on.push needs tags: so a release tag runs every job")
        tests = sorted(set(WORKFLOW_TEST.findall(src)))
        for t in tests:
            if t not in fileset:
                errors.append(f"{wf}: runs {t}, which isn't in the repository")
        for f, via in sorted(py_closure(tests, fileset).items()):
            if missing(f):
                how = f"{via} imports it" if via else f"{wf} runs it"
                errors.append(f"[{radio}] misses {f} ({how})")

        # the C its sources include
        csrc = [p for p in files if p.endswith(C_EXT) and m.in_section(radio, p)]
        seen = set(csrc)
        todo = list(csrc)
        while todo:
            f = todo.pop()
            for inc in c_includes(f, fileset, subs):
                if missing(inc):
                    errors.append(f"[{radio}] misses {inc} ({f} includes it)")
                if inc not in seen and inc in fileset:
                    seen.add(inc)
                    todo.append(inc)
    return errors


# CI: the decision for one radio


def pr_labels(event):
    """the PR's labels now (so adding ci-full then re-running works), else as at the event"""
    names = {lab["name"] for lab in event.get("pull_request", {}).get("labels", [])}
    url = event.get("pull_request", {}).get("issue_url")
    token = os.environ.get("GITHUB_TOKEN")
    if url and token:
        try:
            req = urllib.request.Request(url + "/labels", headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            })
            with urllib.request.urlopen(req, timeout=20) as r:
                names |= {lab["name"] for lab in json.load(r)}
        except Exception as e:  # labels are a convenience: fall back to the event's own
            print(f"::notice::couldn't read the PR's labels ({e}); using the event's")
    return names


def pr_changed():
    """files the PR changes: the merge commit actions/checkout gives a pull_request run against its
    first parent (the base branch), so it needs fetch-depth: 2"""
    parents = git("rev-list", "--parents", "-n", "1", "HEAD").split()
    if len(parents) != 3:
        raise RuntimeError(f"HEAD isn't a merge commit ({len(parents) - 1} parents)")
    return [p for p in git("diff", "--name-only", "-z", "HEAD^1", "HEAD").split("\0") if p]


def github(radio):
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    try:
        event = json.load(open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8"))
    except (KeyError, OSError, ValueError):
        event = {}
    why, unmapped, full = None, [], None
    try:
        m = Map()
        if radio not in m.radios:
            raise RuntimeError(f"no [{radio}] section in .github/ci-radios.txt")
        if event_name in FULL_EVENTS:
            full = f"a {event_name} run builds every radio"
        elif event_name.startswith("pull_request") and FULL_LABEL in pr_labels(event):
            full = f"the PR has the {FULL_LABEL} label"
        elif event_name.startswith("pull_request"):
            why, unmapped = m.scope(pr_changed())
        else:
            full = f"unknown event {event_name!r}"
    except (Exception, SystemExit) as e:  # never skip on a failure here (a broken map too): build it
        full = f"couldn't work out the change ({e})"
        print(f"::warning::ci_scope: {full}; building {radio}")

    run = full is not None or bool(why[radio])
    lines = [f"### CI scope: {radio} {'runs' if run else 'skipped'}", ""]
    if full:
        lines.append(f"Everything runs: {full}.")
    elif run:
        lines.append(f"These changed files are {radio}'s (.github/ci-radios.txt):")
        lines.append("")
        lines += [f"- `{p}`" for p in why[radio][:50]]
        if len(why[radio]) > 50:
            lines.append(f"- and {len(why[radio]) - 50} more")
    else:
        others = sorted(r for r in why if why[r])
        lines.append("No changed file is this radio's. " + ("Radios this PR builds:" if others else "This PR builds no radio."))
        lines += [f"- {r}" for r in others]
        lines.append("")
        lines.append(f"To build everything anyway, add the {FULL_LABEL} label and re-run the jobs.")
    for p in unmapped:
        print(f"::warning file={p}::{p} is in no section of .github/ci-radios.txt, so every radio builds; add it there")
    text = "\n".join(lines) + "\n"
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"run={'true' if run else 'false'}\n")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="check the map against the tree")
    ap.add_argument("--github", metavar="RADIO", help="in CI: write run=true|false for this radio")
    ap.add_argument("--files", nargs="+", metavar="PATH", help="which radios these paths run")
    ap.add_argument("--base", default="origin/main", help="compare HEAD with this (default origin/main)")
    ap.add_argument("--paths", action="store_true", help="print the radio workflows' paths: filter")
    a = ap.parse_args(argv)

    if a.github:
        return github(a.github)
    m = Map()
    if a.paths:
        for p in gh_paths(m):
            print(f"      - '{p}'")
        return 0
    if a.check:
        errors = check(m)
        for e in errors:
            print(e)
        print(f"ci-radios: {len(errors)} problem(s)" if errors else "ci-radios: the map is complete")
        return 1 if errors else 0
    if a.files:
        changed = a.files
    else:
        changed = [p for p in git("diff", "--name-only", "-z", f"{a.base}...HEAD").split("\0") if p]
    why, unmapped = m.scope(changed)
    for r in m.radios:
        print(f"{r:14s} {'runs' if why[r] else 'skipped'}" + (f"  ({len(why[r])} file(s), e.g. {why[r][0]})" if why[r] else ""))
    for p in unmapped:
        print(f"unmapped (runs every radio): {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
