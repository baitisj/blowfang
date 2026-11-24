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
    ("radios/uv380/src/main.c", {"k6": False, "uv380": True, "uv380-openrtx": False, "rt950": False, "uv21": False, "bench": False}),
    ("radios/rt950/src/surf2.c", {"k6": False, "uv380": False, "uv380-openrtx": False, "rt950": True, "uv21": False, "bench": False}),
    ("firmware/uv380-openrtx/mod/c6dmrrx.c", {"k6": False, "uv380": True, "uv380-openrtx": True, "rt950": False, "uv21": False, "bench": False}),
    ("modem/dmrvoice.py", {"k6": False, "uv380": True, "uv380-openrtx": False, "rt950": False, "uv21": False, "bench": False}),
    ("modem/dmrsotto.py", {"k6": True, "uv380": True, "uv380-openrtx": False, "rt950": False, "uv21": False, "bench": False}),   # c4dmr
    ("c/k6/mod/ft8.c", {"k6": True, "uv380": False, "uv380-openrtx": False, "rt950": False, "uv21": False, "bench": False}),
    ("c/common/surf_core.inc", {"k6": True, "uv380": True, "uv380-openrtx": True, "rt950": True, "uv21": True, "bench": False}),
    ("sotto", {"k6": True, "uv380": True, "uv380-openrtx": True, "rt950": False, "uv21": False, "bench": False}),
    ("docs/uv380-port.md", {"k6": False, "uv380": False, "uv380-openrtx": False, "rt950": False, "uv21": False, "bench": False}),
    # the Pluto and the bench runner run only the bench tools' Python tests, no radio's firmware
    ("tools/pluto_bench.py", {"k6": False, "uv380": False, "uv380-openrtx": False, "rt950": False, "uv21": False, "bench": True}),
    ("tools/pluto_chan/pluto_chan.c", {"k6": False, "uv380": False, "uv380-openrtx": False, "rt950": False, "uv21": False, "bench": True}),
    ("tools/pluto_chan/flacout.cpp", {"k6": False, "uv380": False, "uv380-openrtx": False, "rt950": False, "uv21": False, "bench": True}),
    ("tools/pluto_fw.py", {"k6": False, "uv380": False, "uv380-openrtx": False, "rt950": False, "uv21": False, "bench": True}),
    ("tools/bench_runner.py", {"k6": False, "uv380": False, "uv380-openrtx": False, "rt950": False, "uv21": False, "bench": True}),
    ("tests/test_pluto_bench.py", {"k6": False, "uv380": False, "uv380-openrtx": False, "rt950": False, "uv21": False, "bench": True}),
    ("tools/pluto_new_tool.py", {"k6": False, "uv380": False, "uv380-openrtx": False, "rt950": False, "uv21": False, "bench": True}),   # a new Pluto tool needs no new map line
    ("tests/test_pluto_new.py", {"k6": False, "uv380": False, "uv380-openrtx": False, "rt950": False, "uv21": False, "bench": True}),
    # the UV-21's dumper runs only its own job; its bootloader tool runs the bench tools' too
    ("radios/uv21/dumper/dumper.c", {"k6": False, "uv380": False, "uv380-openrtx": False, "rt950": False, "uv21": True, "bench": False}),
    ("tools/uv21_kdh.py", {"k6": False, "uv380": False, "uv380-openrtx": False, "rt950": False, "uv21": True, "bench": True}),
    # the K6 Python the bench tests import runs both
    ("modem/k6.py", {"k6": True, "uv380": True, "uv380-openrtx": True, "rt950": True, "uv21": False, "bench": True}),
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
    # a push whose ref and before the event doesn't give builds everything
    assert run_github(tmp_path, "k6", event_name, {}) == "run=true"


@pytest.mark.parametrize("event", [
    {"ref": "refs/tags/k6-v1.0", "before": "0" * 40, "after": "a" * 40},
    {"ref": "refs/heads/main", "before": "b" * 40, "after": "a" * 40, "forced": True},
    {"ref": "refs/heads/main", "before": "0" * 40, "after": "a" * 40},
])
def test_pushes_that_build_everything(tmp_path, monkeypatch, event):
    """a tag (a release), a forced push, and a main push with no before all build every radio"""
    ev = tmp_path / "event.json"
    ev.write_text(json.dumps(event))
    out = tmp_path / "out.txt"
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(ev))
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    ci_scope.github("k6")
    assert out.read_text().strip() == "run=true"


def test_a_main_push_is_scoped(tmp_path, monkeypatch):
    """a merge to main that only touches the RT-950 builds the RT-950 and skips the K6"""
    ev = tmp_path / "event.json"
    ev.write_text(json.dumps({"ref": "refs/heads/main", "before": "b" * 40, "after": "a" * 40}))
    seen = []
    monkeypatch.setattr(ci_scope, "push_changed", lambda b, a: seen.append((b, a)) or ["radios/rt950/src/surf2.c"])
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(ev))
    for radio, want in (("k6", "run=false"), ("rt950", "run=true")):
        out = tmp_path / f"out-{radio}.txt"
        monkeypatch.setenv("GITHUB_OUTPUT", str(out))
        ci_scope.github(radio)
        assert out.read_text().strip() == want
    assert seen[0] == ("b" * 40, "a" * 40)


def test_a_main_push_that_cant_diff_builds(tmp_path, monkeypatch):
    def broken(before, after):
        raise RuntimeError("before isn't fetchable")
    ev = tmp_path / "event.json"
    ev.write_text(json.dumps({"ref": "refs/heads/main", "before": "b" * 40, "after": "a" * 40}))
    out = tmp_path / "out.txt"
    monkeypatch.setattr(ci_scope, "push_changed", broken)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(ev))
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    ci_scope.github("k6")
    assert out.read_text().strip() == "run=true"


def test_push_changed_diffs_the_push():
    head = ci_scope.git("rev-parse", "HEAD").strip()
    try:
        parent = ci_scope.git("rev-parse", "HEAD^").strip()
    except subprocess.CalledProcessError:
        pytest.skip("a one-commit checkout")
    want = [p for p in ci_scope.git("diff", "--name-only", "-z", parent, head).split("\0") if p]
    assert ci_scope.push_changed(parent, head) == want
    with pytest.raises(RuntimeError):
        ci_scope.push_changed("0" * 40, head)


def test_the_last_build_skips_runs_that_built_nothing():
    """tools/ci_artifact.py: a main run whose build was skipped has no artifacts; the newest one
    that has them (and isn't a PR run, a failure or expired) is the radio's last build"""
    import ci_artifact
    runs = [
        {"id": 6, "head_branch": "main", "event": "push", "conclusion": "success"},          # skipped: none
        {"id": 5, "head_branch": "x", "event": "pull_request", "conclusion": "success"},
        {"id": 4, "head_branch": "main", "event": "push", "conclusion": "failure"},
        {"id": 3, "head_branch": "main", "event": "push", "conclusion": "success"},          # expired
        {"id": 2, "head_branch": "main", "event": "schedule", "conclusion": "success"},
        {"id": 1, "head_branch": "main", "event": "push", "conclusion": "success"},
    ]
    arts = {
        3: [{"name": "uv21-port", "expired": True}],
        2: [{"name": "uv21-port", "expired": False}, {"name": "uv21-dumper", "expired": False}],
        4: [{"name": "uv21-port", "expired": False}],
        5: [{"name": "uv21-port", "expired": False}],
        1: [{"name": "uv21-port", "expired": False}],
    }
    run, got = ci_artifact.pick(runs, lambda r: arts.get(r["id"], []))
    assert run["id"] == 2 and [a["name"] for a in got] == ["uv21-port", "uv21-dumper"]
    run, got = ci_artifact.pick(runs, lambda r: arts.get(r["id"], []), "dumper")
    assert run["id"] == 2 and [a["name"] for a in got] == ["uv21-dumper"]
    assert ci_artifact.pick(runs[:2], lambda r: []) == (None, [])


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
