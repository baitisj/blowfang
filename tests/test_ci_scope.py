"""tools/ci_scope.py and .github/ci-radios.txt: which radios' CI a change runs. MIT"""

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import ci_scope  # noqa: E402

TOY = """\
[ignore]
**/*.md
docs/
!docs/cards/

[all]
.gitmodules

[k6]
.github/workflows/k6.yml
c/k6/
docs/cards/
modem/
!modem/dmr*.py
c/common/surf_core.inc

[uv380]
.github/workflows/uv380.yml
radios/uv380/
c/common/
modem/k6.py
modem/dmrsotto.py
"""


@pytest.fixture
def toy(tmp_path):
    p = tmp_path / "map.txt"
    p.write_text(TOY)
    return ci_scope.Map(str(p))


@pytest.mark.parametrize("path, radios, how", [
    ("c/k6/mod/surf.c", ["k6"], "mapped"),
    ("radios/uv380/src/main.c", ["uv380"], "mapped"),
    ("c/common/surf_core.inc", ["k6", "uv380"], "mapped"),
    ("c/common/ssb_polar.c", ["uv380"], "mapped"),
    ("modem/k6.py", ["k6", "uv380"], "mapped"),
    ("modem/dmrsotto.py", ["uv380"], "mapped"),          # the ! line takes it out of [k6]
    ("radios/uv380/README.md", [], "ignored"),            # docs never build
    ("docs/k6-dmr.md", [], "ignored"),
    ("docs/cards/core.txt", ["k6"], "mapped"),            # unless a ! line takes them back
    (".gitmodules", ["k6", "uv380"], "all"),
    ("radios/rt950/new.c", ["k6", "uv380"], "unmapped"),  # unknown paths build everything
])
def test_place(toy, path, radios, how):
    assert toy.place(path) == (radios, how)


def test_globs():
    assert ci_scope.glob_regex("tests/test_k6*").match("tests/test_k6_surf.py")
    assert not ci_scope.glob_regex("tests/test_k6*").match("tests/sub/test_k6.py")
    assert ci_scope.glob_regex("**/*.md").match("README.md")
    assert ci_scope.glob_regex("**/*.md").match("a/b/c.md")
    assert ci_scope.Rule("c/k6/", 1).hits("c/k6/mod/x.c")
    assert ci_scope.Rule("sotto", 1).hits("sotto")
    assert not ci_scope.Rule("c/k6", 1).hits("c/k6x/y.c")
    assert ci_scope.Rule("c/*", 1).hits("c/sotto.h")


def test_scope(toy):
    why, unmapped = toy.scope(["radios/uv380/src/main.c", "docs/x.md", "c/common/surf_core.inc"])
    assert why == {"k6": ["c/common/surf_core.inc"], "uv380": ["radios/uv380/src/main.c", "c/common/surf_core.inc"]}
    assert unmapped == []
    assert toy.workflow("k6") == ".github/workflows/k6.yml"


def test_the_map_is_complete():
    """every tracked file placed, no stale line, each radio's test imports and C includes in its
    section (the Forbidden files job runs the same)"""
    errors = ci_scope.check(ci_scope.Map())
    assert errors == [], "\n".join(errors)


@pytest.mark.parametrize("path, run", [
    ("radios/uv380/src/main.c", {"k6": False, "uv380": True, "uv380-openrtx": False, "rt950": False}),
    ("radios/rt950/src/surf2.c", {"k6": False, "uv380": False, "uv380-openrtx": False, "rt950": True}),
    ("firmware/uv380-openrtx/mod/c6dmrrx.c", {"k6": False, "uv380": True, "uv380-openrtx": True, "rt950": False}),
    ("modem/dmrvoice.py", {"k6": False, "uv380": True, "uv380-openrtx": False, "rt950": False}),
    ("modem/dmrsotto.py", {"k6": True, "uv380": True, "uv380-openrtx": False, "rt950": False}),   # c4dmr
    ("c/k6/mod/ft8.c", {"k6": True, "uv380": False, "uv380-openrtx": False, "rt950": False}),
    ("c/common/surf_core.inc", {"k6": True, "uv380": True, "uv380-openrtx": True, "rt950": True}),
    ("sotto", {"k6": True, "uv380": True, "uv380-openrtx": True, "rt950": False}),
    ("docs/uv380-port.md", {"k6": False, "uv380": False, "uv380-openrtx": False, "rt950": False}),
])
def test_the_real_map(path, run):
    """the cases this exists for: work on one radio doesn't rebuild the K6"""
    why, _ = ci_scope.Map().scope([path])
    assert {r: bool(v) for r, v in why.items()} == run


def run_github(tmp_path, radio, event_name, event):
    ev = tmp_path / "event.json"
    ev.write_text(json.dumps(event))
    out = tmp_path / "out.txt"
    env = dict(os.environ, GITHUB_EVENT_NAME=event_name, GITHUB_EVENT_PATH=str(ev), GITHUB_OUTPUT=str(out),
               GITHUB_STEP_SUMMARY=str(tmp_path / "summary.md"))
    env.pop("GITHUB_TOKEN", None)
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "ci_scope.py"), "--github", radio],
                   check=True, env=env, capture_output=True)
    return out.read_text().strip()


@pytest.mark.parametrize("event_name", ["push", "schedule", "workflow_dispatch"])
def test_full_runs(tmp_path, event_name):
    assert run_github(tmp_path, "k6", event_name, {}) == "run=true"


def test_the_full_label(tmp_path):
    ev = {"pull_request": {"labels": [{"name": "ci-full"}]}}
    assert run_github(tmp_path, "rt950", "pull_request", ev) == "run=true"


def test_a_failure_builds(tmp_path, monkeypatch):
    """a PR run that can't tell what changed builds rather than skips"""
    def broken():
        raise RuntimeError("HEAD isn't a merge commit")
    ev = tmp_path / "event.json"
    ev.write_text(json.dumps({"pull_request": {"labels": []}}))
    out = tmp_path / "out.txt"
    monkeypatch.setattr(ci_scope, "pr_changed", broken)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(ev))
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    ci_scope.github("k6")
    assert out.read_text().strip() == "run=true"


def test_a_pr_skips(tmp_path, monkeypatch):
    """a PR that only touches the RT-950 skips the K6"""
    ev = tmp_path / "event.json"
    ev.write_text(json.dumps({"pull_request": {"labels": []}}))
    for radio, want in (("k6", "run=false"), ("rt950", "run=true")):
        out = tmp_path / f"out-{radio}.txt"
        monkeypatch.setattr(ci_scope, "pr_changed", lambda: ["radios/rt950/src/surf2.c"])
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(ev))
        monkeypatch.setenv("GITHUB_OUTPUT", str(out))
        ci_scope.github(radio)
        assert out.read_text().strip() == want


def test_the_workflow_filter(toy):
    # [ignore] turned inside out, in the map's order: the last match decides, as on GitHub
    assert ci_scope.gh_paths(toy) == [
        "**",
        "!**/*.md",
        "!*.md",
        "!docs/**",
        "docs/cards/**",
    ]


def test_workflow_paths_reads_both_events():
    src = """name: x
on:
  push:
    branches: [main]
    # a comment
    paths:
      - '**'
      - '!docs/**'
  pull_request:
    paths:
      - "**"
  schedule:
    - cron: "17 9 * * 1"

jobs:
  a:
    paths:
      - nope
"""
    assert ci_scope.workflow_paths(src) == {"push": ["**", "!docs/**"], "pull_request": ["**"]}


def test_the_radio_workflows_skip_docs_only():
    # every radio workflow carries the real map's filter on push and pull_request (--check too)
    m = ci_scope.Map()
    want = ci_scope.gh_paths(m)
    assert "!docs/**" in want and "docs/k6-modules.md" in want
    for radio in m.radios:
        src = open(os.path.join(ROOT, m.workflow(radio)), encoding="utf-8").read()
        got = ci_scope.workflow_paths(src)
        for ev in ci_scope.FILTER_EVENTS:
            assert got.get(ev) == want, (radio, ev)
