#!/usr/bin/env python3
"""The last main run that built a radio, and how to download its images.

A push to main builds only the radios its change touches (tools/ci_scope.py), so the newest main
run of a radio's workflow may have skipped the build. This walks that workflow's successful main
runs, newest first, and stops at the first one that still has artifacts (a run that skipped the
build uploads none). The weekly run and a pushed tag build everything, so a radio's newest build
is never more than a week old.

  python3 tools/ci_artifact.py uv21               # run, commit, artifacts, download commands
  python3 tools/ci_artifact.py k6 --name aura     # only artifacts whose name has this in it
  python3 tools/ci_artifact.py rt950 --json       # the same for a script
  python3 tools/ci_artifact.py uv21 --download DIR    # gh run download into DIR (needs gh)

Listing works without a token on the public repository (GITHUB_TOKEN, or `gh auth token`, raises
the rate limit); downloading needs gh signed in.

SPDX-License-Identifier: MIT
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import ci_scope  # noqa: E402

REPO = "baitisj/blowfang"
API = "https://api.github.com"
# the events a main run builds from; a pull_request run never counts
MAIN_EVENTS = (
    "push",
    "schedule",
    "workflow_dispatch",
)


def token():
    if os.environ.get("GITHUB_TOKEN"):
        return os.environ["GITHUB_TOKEN"]
    try:
        return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True,
                              timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def get(path, tok=None):
    headers = {"Accept": "application/vnd.github+json"}
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(API + path, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def pick(runs, artifacts_of, name=""):
    """the first run (newest first) from main that built: a main event, successful, with live
    artifacts (those whose name contains name, when given). Returns (run, artifacts) or (None, [])"""
    for run in runs:
        if run.get("head_branch") != "main" or run.get("event") not in MAIN_EVENTS:
            continue
        if run.get("conclusion") != "success":
            continue
        arts = [a for a in artifacts_of(run) if not a.get("expired") and name in a["name"]]
        if arts:
            return run, arts
    return None, []


def find(radio, name="", repo=REPO, tok=None, pages=3):
    workflow = os.path.basename(ci_scope.Map().workflow(radio))
    runs = []
    for page in range(1, pages + 1):
        got = get(f"/repos/{repo}/actions/workflows/{workflow}/runs"
                  f"?branch=main&status=success&per_page=50&page={page}", tok)["workflow_runs"]
        runs += got
        run, arts = pick(got, lambda r: get(f"/repos/{repo}/actions/runs/{r['id']}/artifacts?per_page=100",
                                            tok)["artifacts"], name)
        if run or len(got) < 50:
            return workflow, run, arts
    return workflow, None, []


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("radio", help="a section of .github/ci-radios.txt: k6, uv380, uv380-openrtx, rt950, uv21, bench")
    ap.add_argument("--name", default="", help="only artifacts whose name contains this")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--json", action="store_true", help="print the run and its artifacts as JSON")
    ap.add_argument("--download", metavar="DIR", help="download the artifacts into DIR with gh")
    a = ap.parse_args(argv)
    if a.radio not in ci_scope.Map().radios:
        ap.error(f"no [{a.radio}] section in .github/ci-radios.txt")
    workflow, run, arts = find(a.radio, a.name, a.repo, token())
    if not run:
        print(f"no successful main run of {workflow} has live artifacts{' named *' + a.name + '*' if a.name else ''}",
              file=sys.stderr)
        return 1
    cmds = [["gh", "run", "download", str(run["id"]), "-R", a.repo, "-n", art["name"]]
            + (["-D", os.path.join(a.download, art["name"])] if a.download else []) for art in arts]
    if a.json:
        print(json.dumps({
            "workflow": workflow,
            "run": run["id"],
            "sha": run["head_sha"],
            "event": run["event"],
            "created_at": run["created_at"],
            "url": run["html_url"],
            "artifacts": [art["name"] for art in arts],
        }, indent=1))
    else:
        print(f"{workflow}: run {run['id']} ({run['event']}, {run['created_at']}) built main {run['head_sha'][:10]}")
        print(f"  {run['html_url']}")
        for art in arts:
            print(f"  {art['name']}  ({art['size_in_bytes']} bytes, expires {art.get('expires_at', '?')})")
        if not a.download:
            print("download:")
            for c in cmds:
                print("  " + " ".join(c))
    if a.download:
        for c in cmds:
            subprocess.run(c, check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
