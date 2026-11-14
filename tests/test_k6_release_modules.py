"""The release's module set (tools/k6_release_modules.txt): slots, names, sizes"""
import os
import shutil

import pytest

from modem import k6mod

ROOT = os.path.join(os.path.dirname(__file__), "..")
LIST = os.path.join(ROOT, "tools", "k6_release_modules.txt")
MOD = os.path.join(ROOT, "c", "k6", "mod")
BLOCK = 11752                      # the module block less its header and tag (test_k6_gw.py)
LEVEL_MAX = 9                      # the images' API level
PAGE_AND_DECK_SLOTS = range(9, 16) # EXPLORE's pages 9-11, the editor's 12, help decks 13-15

need_clang = pytest.mark.skipif(not shutil.which("clang") or not shutil.which("ld.lld"),
                                reason="needs clang and ld.lld")


def entries():
    out = []
    for ln in open(LIST):
        ln = ln.split("#")[0].strip()
        if ln:
            slot, src = ln.split()
            out.append((int(slot), src))
    return out


def test_slots_are_module_slots_and_used_once():
    e = entries()
    slots = [s for s, _ in e]
    srcs = [m for _, m in e]
    assert len(set(slots)) == len(slots) and len(set(srcs)) == len(srcs)
    for s in slots:
        assert 0 <= s < 32 and s not in PAGE_AND_DECK_SLOTS, s
    for m in srcs:
        assert os.path.exists(os.path.join(MOD, m + ".c")), m


@need_clang
@pytest.mark.parametrize("slot,src", entries())
def test_each_builds_named_and_fits(slot, src):
    path = os.path.join(MOD, src + ".c")
    code, entry = k6mod.build(path)
    name = k6mod.module_name(code)
    assert name and len(name) <= 12, src
    assert all(c.isupper() or c.isdigit() or c in " -/." for c in name), name
    assert entry == 16 and k6mod.bin_entry(code) == 16
    assert len(code) <= BLOCK, (src, len(code))
    assert 1 <= k6mod.source_level(path) <= LEVEL_MAX


@need_clang
def test_names_are_different():
    names = [k6mod.module_name(k6mod.build(os.path.join(MOD, m + ".c"))[0]) for _, m in entries()]
    assert len(set(names)) == len(names), names
