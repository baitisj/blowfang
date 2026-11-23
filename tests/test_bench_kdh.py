"""the bench runner's UV-21 Pro steps (kdh.probe, kdh.dump, kdh.flash, kdh.console:
tools/uv21_kdh.py) and the inbox's refusal of the flash."""
import importlib.util
import os
import sys

TOOLS = os.path.join(os.path.dirname(__file__), "..", "tools")
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.dirname(__file__))
import test_bench_runner as tb  # noqa: E402
from test_bench_runner import work  # noqa: E402,F401  (the runner's fixture: BLOWFANG_WORK, K6_CABLE)
spec = importlib.util.spec_from_file_location("uv21_kdh", os.path.join(TOOLS, "uv21_kdh.py"))
kdh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kdh)
br = tb.br
APP = b"\x00\x40\x00\x20\x01\x11\x00\x08" + bytes(800)       # sp, reset: an app vector table


def test_runner_lists_match_the_tool():
    assert br.KDH_APP_MAX == kdh.APP_MAX
    assert br.KDH_CONSOLE_SAFE.encode() == kdh.CONSOLE_SAFE
    assert tuple(int(b) for b in br.KDH_BAUDS) == kdh.BAUDS


def test_console_args(work):
    for step in ({"do": "kdh.console"}, {"do": "kdh.console", "args": ["--send", "ir", "--seconds", "30"]},
                 {"do": "kdh.console", "args": ["--send=?p", "--listen-s", "3"]},
                 {"do": "kdh.console", "args": ["--send", "?", "--baud", "57600"]}):
        assert tb.problems(tb.job("k", step)) == "", step
    for step in ({"do": "kdh.console", "args": ["--send", "O"]},       # power off
                 {"do": "kdh.console", "args": ["--send", "iR"]},      # reset
                 {"do": "kdh.console", "args": ["--seconds", "9999"]},
                 {"do": "kdh.console", "args": ["--baud", "9600"]},
                 {"do": "kdh.console", "args": ["--port", "/dev/ttyUSB0"]},
                 {"do": "kdh.console", "background": True}):
        assert tb.problems(tb.job("k", step)), step
    argv, _, bg = br.command(tb.job("c"), {"do": "kdh.console", "args": ["--send", "i"]}, str(work / "rd"))
    assert argv[2:5] == ["console", "--port", "/dev/k6cable"] and argv[-2:] == ["--send", "i"] and not bg


def test_probe_and_dump_args(work):
    for step in ({"do": "kdh.probe"}, {"do": "kdh.probe", "args": ["--listen-s", "2"]},
                 {"do": "kdh.probe", "args": ["--wait-boot-s", "180"]},
                 {"do": "kdh.probe", "args": ["--wait-boot-s", "200", "--boot-baud", "57600"]},
                 {"do": "kdh.dump", "args": ["--passes", "3", "--timeout-s=2400"]}):
        assert tb.problems(tb.job("k", step)) == "", step
    for step in ({"do": "kdh.probe", "args": ["--port", "/dev/ttyUSB0"]},
                 {"do": "kdh.probe", "args": ["--image", "x.bin"]},
                 {"do": "kdh.probe", "args": ["--boot-baud", "230400"]},
                 {"do": "kdh.dump", "args": ["--private", "/tmp"]},
                 {"do": "kdh.dump", "args": ["--passes", "9"]},
                 {"do": "kdh.probe", "args": ["--wait-boot-s", "999"]},
                 {"do": "kdh.dump", "args": ["--timeout-s", "9999"]},
                 {"do": "kdh.dump", "args": ["--passes"]},
                 {"do": "kdh.dump", "background": True}):
        assert tb.problems(tb.job("k", step)), step


def test_flash_needs_a_pinned_staged_image(work):
    p, h = tb.stage(work, "dumper.bin", APP)
    ok = {"do": "kdh.flash", "image": p, "sha256": h, "commit": "8985332"}
    assert tb.problems(tb.job("k", ok)) == ""
    assert tb.problems(tb.job("k", dict(ok, args=["--listen-after-s", "60"]))) == ""
    for bad in (dict(ok, sha256="00" * 32), {k: v for k, v in ok.items() if k != "sha256"},
                dict(ok, args=["--region2"]), dict(ok, image="/etc/passwd"), dict(ok, background=True),
                dict(ok, args=["--listen-after-s", "999"]), dict(ok, args=["--port", "/dev/ttyUSB0"])):
        assert tb.problems(tb.job("k", bad)), bad
    big, hb = tb.stage(work, "big.bin", APP + bytes(0xF000))
    assert "app area" in tb.problems(tb.job("k", dict(ok, image=big, sha256=hb)))


def test_command_lines_keep_the_dump_private(work):
    rd = str(work / "rd")
    j = tb.job("uv21-dump-1")
    argv, _, bg = br.command(j, {"do": "kdh.dump", "args": ["--passes", "2"]}, rd)
    assert argv[1].endswith("uv21_kdh.py") and argv[2:6] == ["dump", "--port", "/dev/k6cable", "--out"]
    assert argv[argv.index("--private") + 1] == os.path.join(str(work), "private", "uv21", "uv21-dump-1")
    assert argv[-2:] == ["--passes", "2"] and not bg
    p, h = tb.stage(work, "dumper.bin", APP)
    argv, _, _ = br.command(j, {"do": "kdh.flash", "image": p, "sha256": h}, rd)
    assert argv[2:4] == ["flash", "--port"] and argv[-4:] == ["--image", os.path.realpath(p), "--sha256", h]
    argv, _, _ = br.command(j, {"do": "kdh.flash", "image": p, "sha256": h, "args": ["--listen-after-s=30"]}, rd)
    assert argv[-1] == "--listen-after-s=30"
    for do in ("kdh.probe", "kdh.dump", "kdh.flash", "kdh.console"):
        assert br.uses_cable({"do": do})


def test_inbox_refuses_the_flash():
    spec = importlib.util.spec_from_file_location("bench_inbox", os.path.join(TOOLS, "bench_inbox.py"))
    bi = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bi)
    assert "kdh.flash" in bi.REMOTE_NO_STEPS
    assert "kdh.dump" not in bi.REMOTE_NO_STEPS and "kdh.probe" not in bi.REMOTE_NO_STEPS
