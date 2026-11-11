"""DMR's sync words through the K6 chip's plain FSK receiver (c/k6/mod/dfskcap_dmr.c).

DMR is 4FSK at 4800 symbols a second, dibits 01, 00, 10, 11 as +3, +1, -1, -3. The chip's plain
FSK receiver at 4800 baud gives the sign of the discriminator: one bit a symbol, the dibit's
first bit (which way up depends on the discriminator's sense, not known). A sync word has outer
symbols only, so its signs are a fixed 24 bits: the BS data sync DFF57D75DF5D is 439B4D with +
as 1, and the voice sync is its inverse. dfskcap_dmr gives the chip the last sixteen of those
as its sync word; a capture is then the signs of the symbols that follow, and a burst being 144
symbols, the next bursts' sync fields are its bits 120 to 143, 264 to 287 and so on.

    python3 -m modem.dmrsign LOG        the serial log -> what each setting found

SPDX-License-Identifier: MIT
"""
import argparse

import numpy as np

BS_DATA = 0xDFF57D75DF5D
BURST = 144                  # symbols: 12 of CACH, 54, the 24 of the sync field, 54
FIRST = 120                  # where the next burst's sync field starts in a capture


def signs(sync48):
    """a 48-bit sync word -> its 24 symbols' signs, + as 1 (dibit 01 is +3, 11 is -3)"""
    d = [(sync48 >> (46 - 2 * i)) & 3 for i in range(24)]
    assert all(x in (1, 3) for x in d), "outer symbols only"
    return np.array([1 if x == 1 else 0 for x in d], np.int8)


SIGNS = signs(BS_DATA)       # 439B4D


def air_signs(bursts, rng, voice_every=0):
    """a repeater's output as the symbols' signs: bursts of 144, each with the data sync (or,
    every voice_every-th, the voice sync), the rest chance"""
    out = []
    for i in range(bursts):
        s = 1 - SIGNS if voice_every and i % voice_every == voice_every - 1 else SIGNS
        out += [rng.integers(0, 2, 66).astype(np.int8), s, rng.integers(0, 2, 54).astype(np.int8)]
    return np.concatenate(out)


def next_syncs(bits):
    """a capture's bits -> for each later burst in it, how many of its sync field's 24 signs
    agree with the data sync's (24: a sync the same way up; 0: one the other way up; about 12:
    not a sync, or not DMR)"""
    return [int((bits[i:i + 24] == SIGNS).sum()) for i in range(FIRST, len(bits) - 23, BURST)]


def report(lines, need=21):
    """dfskcap_dmr's say lines (as in the serial log) -> a line for every capture that has a
    later sync (at least need of its 24 signs right, either way up), and a line a setting:
    slots, captures, those with a later sync, the syncs found of those looked for in them, the
    signs right in the found ones, and the RSSI of the confirmed captures"""
    said = [ln.split("say ", 1)[-1].strip() for ln in lines]
    said = said[max([i for i, ln in enumerate(said) if ln.startswith("dfskcap start")] or [0]):]
    sets, caps, cur, slots, peak = {}, [], None, {}, {}
    for ln in said:
        f = ln.split()
        if ln[:2] == "s " and len(f) > 2 and f[1].isdigit():
            sets[int(f[1])] = dict(x.split("=", 1) for x in f[2:] if "=" in x)
        elif ln[:1] == "v" and f and f[0][1:].isdigit():
            fe, kv = int(f[0][1:]), dict(x.split("=", 1) for x in f[1:] if "=" in x)
            slots[fe] = slots.get(fe, 0) + 1
            if "p" in kv:
                peak.setdefault(fe, []).append(int(kv["p"]))
        elif ln[:1] == "c" and len(f) > 3 and f[0][1:].isdigit() and f[1].isdigit():
            kv = dict(x.split("=", 1) for x in f[2:] if "=" in x)
            cur = {"fe": int(f[0][1:]), "n": int(f[1]), "t": int(kv.get("t", 0)), "r": int(kv.get("r", 0)), "w": []}
            caps.append(cur)
        elif ln[:1] == "d" and len(f) == 4 and f[0][1:].isdigit() and cur is not None:
            if int(f[0][1:]) == cur["fe"] and int(f[1]) == cur["n"] and int(f[2], 16) == len(cur["w"]):
                cur["w"].extend(int(f[3][i:i + 4], 16) for i in range(0, len(f[3]) - 3, 4))
    out, tally = [], {}
    for c in caps:
        t = tally.setdefault(c["fe"], {"caps": 0, "ok": 0, "found": 0, "looked": 0, "right": 0, "r": []})
        t["caps"] += 1
        if not c["w"]:
            continue
        bits = np.array([int(ch) for w in c["w"] for ch in f"{w:016b}"], np.int8)
        a = next_syncs(bits)
        hit = [x for x in a if x >= need or x <= 24 - need]
        if hit:
            t["ok"] += 1
            t["found"] += len(hit)
            t["looked"] += len(a)
            t["right"] += sum(max(x, 24 - x) for x in hit)
            t["r"].append(c["r"])
            out.append(f"c{c['fe']} t={c['t']} r={c['r']}: later syncs, signs like the data sync's of 24: "
                       + " ".join(str(x) for x in a))
    for fe in sorted(sets):
        t = tally.get(fe, {"caps": 0, "ok": 0, "found": 0, "looked": 0, "right": 0, "r": []})
        s = sets[fe]
        ln = (f"dmr{fe} sy={s.get('sy', '?')} cfg={s.get('cfg', '?')}: {slots.get(fe, 0)} slots, {t['caps']} captures, "
              f"{t['ok']} with a later sync; syncs {t['found']} of {t['looked']}")
        if t["found"]:
            ln += f", signs right {t['right']} of {24 * t['found']}; RSSI {min(t['r'])} to {max(t['r'])}"
        if peak.get(fe):
            ln += f"; slot peaks {min(peak[fe])} to {max(peak[fe])}"
        out.append(ln)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("log")
    ap.add_argument("--need", type=int, default=21, help="signs of 24 for a later sync (either way up)")
    a = ap.parse_args(argv)
    with open(a.log, errors="replace") as f:
        print("\n".join(report(f.read().splitlines(), a.need)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
