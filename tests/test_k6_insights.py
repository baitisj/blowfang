"""docs/k6-insights/: one insight a file, numbered without gaps or repeats, each file's title
line matching its name, and tools/k6_insights.py listing and starting them.
SPDX-License-Identifier: MIT"""
import datetime
import importlib.util
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("k6_insights", os.path.join(ROOT, "tools", "k6_insights.py"))
ki = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ki)


def test_every_file_is_one_numbered_insight():
    names = [f for f in os.listdir(ki.DIR) if f != "README.md"]
    assert names and all(ki.NAME.match(f) for f in names), [f for f in names if not ki.NAME.match(f)]
    es = ki.entries()
    nums = [n for n, _, _ in es]
    assert len(set(nums)) == len(nums), "two insights share a number: renumber the newer one"
    assert nums == list(range(1, len(nums) + 1)), "a gap in the numbers"
    for n, title, p in es:
        first = open(p, encoding="utf-8").readline()
        assert re.match(r"^# %d\. \S" % n, first), (p, first)
        assert os.path.basename(p).startswith("%02d-" % n)


def test_links_from_an_insight_resolve():
    for _, _, p in ki.entries():
        for link in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", open(p, encoding="utf-8").read()):
            if link.startswith(("http:", "https:")):
                continue
            assert os.path.exists(os.path.join(ki.DIR, link)), (os.path.basename(p), link)


def test_the_old_list_is_gone_from_k6_modules():
    s = open(os.path.join(ROOT, "docs", "k6-modules.md"), encoding="utf-8").read()
    assert "k6-insights/" in s
    assert not re.search(r"^\d+\. \*\*", s.split("## Insights")[1], re.M)


def test_new_takes_the_next_number(tmp_path):
    (tmp_path / "01-one.md").write_text("# 1. One\n")
    (tmp_path / "02-two.md").write_text("# 2. Two\n")
    p = ki.new("Read the squelch tail as a carrier ID.", d=str(tmp_path), today=datetime.date(2026, 11, 9))
    assert os.path.basename(p) == "03-read-the-squelch-tail-as-a-carrier-id.md"
    s = open(p).read()
    assert s.startswith("# 3. Read the squelch tail as a carrier ID\n") and "2026-11-09" in s
    assert [n for n, _, _ in ki.entries(str(tmp_path))] == [1, 2, 3]


def test_slug():
    assert ki.slug("The K6's clock from APRS traffic") == "the-k6s-clock-from-aprs-traffic"
    assert ki.slug("Never move a symbol clock backwards in a streaming DMA receiver") == \
        "never-move-a-symbol-clock-backwards"
