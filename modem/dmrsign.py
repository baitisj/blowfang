"""DMR's sync words through the K6 chip's plain FSK receiver (c/k6/mod/dfskcap_dmr.c).

DMR is 4FSK at 4800 symbols a second, dibits 01, 00, 10, 11 as +3, +1, -1, -3. The chip's plain
FSK receiver at 4800 baud gives the sign of the discriminator: one bit a symbol, the dibit's
first bit (which way up depends on the discriminator's sense, not known). A sync word has outer
symbols only, so its signs are a fixed 24 bits: the BS data sync DFF57D75DF5D is 439B4D with +
as 1, and the voice sync is its inverse. dfskcap_dmr gives the chip the last sixteen of those
as its sync word; a capture is then the signs of the symbols that follow, with the next
bursts' sync fields in it: 144 bits apart if the clock kept time, which the chip's does not
(it drops some ten signs a burst), so they are searched for wherever they are.

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
    not a sync, or not DMR). For a clock that keeps time (the model's): the real chip's drops
    signs, so its captures are searched with find_syncs"""
    return [int((bits[i:i + 24] == SIGNS).sum()) for i in range(FIRST, len(bits) - 23, BURST)]


# What the real chip gives (run J, 2026-11-11): its clock follows the slicer's turns and drops
# signs, some ten a burst, so the syncs are not 144 bits apart, and the sync field itself
# comes whole or, more often, with the second of its signs 15 and 16 (a pair of +3) dropped.
SHORT = np.delete(SIGNS, 15)


def find_syncs(bits):
    """a capture's bits -> [(bit, way, kind)] of the sync fields in it, wherever they are: way
    is "+" (the data sync's way up) or "-", kind "whole" (all 24 signs) or "short" (the 23
    that are left when the chip drops sign 16). Chance gives one in millions of bits"""
    bits = np.asarray(bits, np.int8)
    out = []
    for way, flip in (("+", 0), ("-", 1)):
        w = (np.lib.stride_tricks.sliding_window_view(bits ^ flip, 24) == SIGNS).all(1) if len(bits) >= 24 else []
        whole = [int(i) for i in np.flatnonzero(w)]
        out += [(i, way, "whole") for i in whole]
        if len(bits) >= 23:
            sh = (np.lib.stride_tricks.sliding_window_view(bits ^ flip, 23) == SHORT).all(1)
            out += [(int(i), way, "short") for i in np.flatnonzero(sh)]
    return sorted(out)


CARRIER = 10                 # dB over a setting's floor for a slot to count as having a carrier


def report(lines):
    """dfskcap_dmr's say lines (as in the serial log) -> a line for every capture with a sync
    field in it (where they are, s marking the short ones), and a line a setting: slots,
    captures, those with a sync inside, the syncs whole and short, the RSSI of those captures,
    the slots' peaks, the floor (the median of the slots' lowest readings), and of the slots
    with a carrier (a peak CARRIER dB over the floor) how many had a sync, with their peaks:
    what the chip found of what there was. A carrier need not be DMR (the repeater's analog
    ID, the bench's ID keeper next door in the wide filter)"""
    said = [ln.split("say ", 1)[-1].strip() for ln in lines]
    said = said[max([i for i, ln in enumerate(said) if ln.startswith("dfskcap start")] or [0]):]
    sets, caps, cur, slots, pending = {}, [], None, [], []
    for ln in said:
        f = ln.split()
        if ln[:2] == "s " and len(f) > 2 and f[1].isdigit():
            sets[int(f[1])] = dict(x.split("=", 1) for x in f[2:] if "=" in x)
        elif ln[:1] == "v" and f and f[0][1:].isdigit():
            kv = dict(x.split("=", 1) for x in f[1:] if "=" in x)
            slots.append({"fe": int(f[0][1:]), "p": int(kv["p"]) if "p" in kv else None,
                          "f": int(kv["f"]) if "f" in kv else None, "caps": pending})
            pending = []
        elif ln[:1] == "c" and len(f) > 3 and f[0][1:].isdigit() and f[1].isdigit():
            kv = dict(x.split("=", 1) for x in f[2:] if "=" in x)
            cur = {"fe": int(f[0][1:]), "n": int(f[1]), "t": int(kv.get("t", 0)), "r": int(kv.get("r", 0)), "w": [],
                   "sy": []}
            caps.append(cur)
            pending.append(cur)
        elif ln[:1] == "d" and len(f) == 4 and f[0][1:].isdigit() and cur is not None:
            if int(f[0][1:]) == cur["fe"] and int(f[1]) == cur["n"] and int(f[2], 16) == len(cur["w"]):
                cur["w"].extend(int(f[3][i:i + 4], 16) for i in range(0, len(f[3]) - 3, 4))
    out, tally = [], {}
    for c in caps:
        t = tally.setdefault(c["fe"], {"caps": 0, "ok": 0, "whole": 0, "short": 0, "bits": 0, "r": []})
        t["caps"] += 1
        if not c["w"]:
            continue
        bits = np.array([int(ch) for w in c["w"] for ch in f"{w:016b}"], np.int8)
        t["bits"] += len(bits)
        a = c["sy"] = find_syncs(bits)
        if a:
            t["ok"] += 1
            t["whole"] += sum(k == "whole" for _, _, k in a)
            t["short"] += sum(k == "short" for _, _, k in a)
            t["r"].append(c["r"])
            out.append(f"c{c['fe']} t={c['t']} r={c['r']}: {len(a)} syncs in {len(bits)} signs, at "
                       + " ".join(f"{i}{'' if w == '+' else '-'}{'s' if k == 'short' else ''}" for i, w, k in a))
    for fe in sorted(sets):
        t = tally.get(fe, {"caps": 0, "ok": 0, "whole": 0, "short": 0, "bits": 0, "r": []})
        s, mine = sets[fe], [x for x in slots if x["fe"] == fe]
        ln = f"dmr{fe} sy={s.get('sy', '?')} cfg={s.get('cfg', '?')}"
        if "g" in s:
            ln += f" g={s['g']}"
        ln += (f": {len(mine)} slots, {t['caps']} captures ({t['bits']} signs), {t['ok']} with a sync inside; "
               f"syncs {t['whole']} whole, {t['short']} short")
        if t["ok"]:
            ln += f"; RSSI {min(t['r'])} to {max(t['r'])}"
        peak = [x["p"] for x in mine if x["p"] is not None]
        if peak:
            ln += f"; slot peaks {min(peak)} to {max(peak)}"
        low = [x["f"] for x in mine if x["f"] is not None]
        if low:
            floor = int(np.median(low))
            car = [x for x in mine if x["p"] is not None and x["p"] >= floor + CARRIER]
            hit = [x for x in car if any(c["sy"] for c in x["caps"])]
            miss = [x for x in car if not any(c["sy"] for c in x["caps"])]
            ln += f"; floor {floor}; carrier in {len(car)} slots, a sync in {len(hit)}"
            if hit:
                ln += f" (peaks {min(x['p'] for x in hit)} to {max(x['p'] for x in hit)})"
            if miss:
                ln += f", none in {len(miss)} (peaks {min(x['p'] for x in miss)} to {max(x['p'] for x in miss)})"
        out.append(ln)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("log")
    a = ap.parse_args(argv)
    with open(a.log, errors="replace") as f:
        print("\n".join(report(f.read().splitlines())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
