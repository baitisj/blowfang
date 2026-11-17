#!/usr/bin/env python3
"""DMR voice calls carrying sotto (docs/uv380-c6000-modem.md, "Sotto in DMR voice bursts"): the
decoder for what the UV-380 sends with c6dvtx (a Pluto rx wav, or any FM recording), the map
learner for its raw test pattern, and a voice call as audio for the tests and the UV-380's
receiver.

  python3 tools/uv380_dmrvoice.py voice IN.wav [--out MSG.bit] [--map MAP.json] [--preemph]
  python3 tools/uv380_dmrvoice.py voice IN.wav --learn MAP.json
  python3 tools/uv380_dmrvoice.py vwav OUT.wav [--msg MSG.bit] [--pn N] [--map MAP.json]

voice: each burst's 216 payload bits, soft, through modem/dmrvoice.py (soft Viterbi, the block's
CRC) into the message. The call is 3 LC headers, superframes of voice bursts A-F, a terminator;
bursts B-F have no sync, so they are read 60, 120 ... 300 ms after their A, on A's timing and
levels (as sim/k6if/dmrdec.py reads their EMBs).

--learn reads a call that carried the raw test pattern (c6dvtx_pn) instead: it finds how the chip
put page 3's bits on the air and writes that map (json) for voice --map. The bit errors through
the plain map and through the learned one tell whether a map is needed at all.

vwav writes such a call (--pn N: N superframes of the test pattern; --map: through a map, as a
chip with that mapping would send it). Bursts and audio as tools/uv380_dmrburst.py builds them.

MIT licence."""
import argparse
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
from modem import dmrsotto, dmrvoice  # noqa: E402
from sim.k6if import dmr, dmrdec as dd  # noqa: E402
import uv380_dmrburst as burst  # noqa: E402

symbols_audio = burst.symbols_audio


def voice_soft(u144):
    """a voice burst's soft values (dd.burst_symbols) -> its 216 payload bits' (positive = 1): the
    108 before the sync or EMB and the 108 after"""
    bb = dd.soft_bits(u144)[24:]
    return np.concatenate([bb[:108], bb[156:]])


def voice_stream_symbols(payloads, cc=1, dst=9, src=1, lead=6, hdr=3, tail=4, mp=None, seed=0):
    """27-byte voice burst payloads (a multiple of 6) -> 4800/s symbols of a direct-mode voice call
    on slot 1 (slot 2 idle): lead idle frames, hdr voice LC headers, the payloads in bursts A-F
    (voice sync on A, the EMB and embedded LC on B-E), the terminator, tail idle frames. mp
    ([(air bit, sign)] for each page 3 bit, as dmrvoice.learn_map gives it) sends each payload as
    a chip with that mapping would"""
    assert len(payloads) % 6 == 0
    rng = np.random.default_rng(seed)
    ds = dd.bits(-np.array(dmr.PAT["bs"]))
    vs = dd.bits(np.array(dmr.PAT["bs"]))
    s1 = [dd.burst_bits(cc, 9, rng.integers(0, 2, 196), ds, tc=0, rng=rng) for _ in range(lead)]
    s1 += [dd.burst_bits(cc, 1, dd.bptc196_encode(dd.lc_encode(0, dst, src)), ds, tc=0, rng=rng)
           for _ in range(hdr)]
    fr = dd.emb_lc_encode(dd.lc9(0, dst, src))
    for k, pay in enumerate(payloads):
        b = dmrvoice.hard(pay)
        if mp:
            air = np.zeros(dmrvoice.BITS)
            for i, (a, sg) in enumerate(mp):
                air[a] = sg * b[i]
            b = air
        bits = (b > 0).astype(np.uint8)
        j = k % 6
        if j == 0:
            mid = vs
        else:
            e = dd.emb_encode(cc, (1, 3, 3, 2, 0)[j - 1])
            body = fr[32 * (j - 1):32 * j] if j < 5 else np.zeros(32, np.uint8)
            mid = np.concatenate([e[:8], body, e[8:]])
        v = dd.voice_burst_bits(mid, 0, rng)
        v[24:132] = bits[:108]
        v[180:] = bits[108:]
        s1.append(v)
    s1.append(dd.burst_bits(cc, 2, dd.bptc196_encode(dd.lc_encode(0, dst, src, kind="term")), ds,
                            tc=0, rng=rng))
    s1 += [dd.burst_bits(cc, 9, rng.integers(0, 2, 196), ds, tc=0, rng=rng) for _ in range(tail)]
    out = []
    for b in s1:
        out.append(dd.bits_to_symbols(b))
        out.append(dd.bits_to_symbols(dd.burst_bits(cc, 9, rng.integers(0, 2, 196), ds, tc=1, rng=rng)))
    return np.concatenate(out)


def voice_payloads(blocks):
    """dmrsotto blocks -> voice burst payloads, whole superframes (the end block repeated, as
    c6dvtx does)"""
    pay = [dmrvoice.encode(b) for b in blocks]
    while len(pay) % 6:
        pay.append(pay[-1])
    return pay


def voice_bursts(y, pol, ev, sps=dd.SPS):
    """matched-filtered samples and decode_stream's events -> [(burst index from the first burst A
    heard, 216 soft payload values in air order)]: each A and the B-F found after it. A voice sync
    off the 60 ms grid of the first, or inside a superframe already taken, is a false one (a
    burst's payload can look like a sync) and is dropped"""
    out, t0, taken = [], None, set()
    for e in ev:
        if e.get("kind") != "voice":
            continue
        if t0 is None:
            t0 = e["t"]
        f = (e["t"] - t0) / 0.060
        k0 = int(round(f))
        if abs(f - k0) > 0.1 or k0 in taken:
            continue
        s0 = int(round(e["t"] * sps * dmr.BAUD))
        sym, u = dd.burst_symbols(y, s0, sps, pol=pol)
        if sym is None:
            continue
        taken.update(range(k0, k0 + 6))
        out.append((k0, voice_soft(u)))
        ref = dd.burst_ref(y, s0, sps, pol=pol)
        for j in range(1, 6):
            sk, uk = dd.burst_symbols(y, s0 + 288 * sps * j, sps, pol=pol, ref=ref)
            if sk is None:
                break
            out.append((k0 + j, voice_soft(uk)))
    return out


def decode_voice(x, fs, thr=0.85, mp=None):
    """audio -> (events, [(burst index, soft payload)], dmrvoice.unpack's (result, blocks))"""
    y = dd.audio_symbols(x, fs)
    pol, ev = dd.decode_stream(y, thr=thr)
    vb = voice_bursts(y, pol, ev)
    return ev, vb, dmrvoice.unpack([v for _, v in vb], mp)


def learn_pn(vb, offsets=range(0, 31, 6)):
    """a test-pattern call's bursts -> (map, weakest correlation, permutation?, the first burst's
    pattern number, bit errors through the plain map, through the learned one, bits): the first
    burst A heard may not be the call's first, so each superframe offset is tried"""
    soft = [v for _, v in vb]
    best = None
    for off in offsets:
        ks = [off + k for k, _ in vb]
        mp, weak, perm = dmrvoice.learn_map(soft, ks)
        if best is None or weak > best[1]:
            best = (mp, weak, perm, off, ks)
    mp, weak, perm, off, ks = best
    return (mp, weak, perm, off, dmrvoice.pn_errors(soft, ks), dmrvoice.pn_errors(soft, ks, mp),
            len(soft) * dmrvoice.BITS)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("voice")
    v.add_argument("wav")
    v.add_argument("--out")
    v.add_argument("--map", help="page 3 -> air map from --learn (json)")
    v.add_argument("--learn", metavar="MAP", help="the call carried the raw test pattern: write the map here")
    v.add_argument("--thr", type=float, default=0.85)
    v.add_argument("--preemph", action="store_true", help="undo a 750 us de-emphasis first")
    w = sub.add_parser("vwav")
    w.add_argument("out")
    burst.stream_args(w)
    w.add_argument("--pn", type=int, metavar="SUPERFRAMES", help="the raw test pattern instead")
    w.add_argument("--map", help="send through this map (json), as such a chip would")
    w.add_argument("--fs", type=int, default=48000)
    w.add_argument("--amp", type=float, default=0.35)
    a = ap.parse_args()
    if a.cmd == "vwav":
        mp = [tuple(m) for m in json.load(open(a.map))] if a.map else None
        if a.pn:
            pay = [dmrvoice.pn(k) for k in range(6 * a.pn)]
        else:
            _, blocks, _ = burst.stream_for(a)
            pay = voice_payloads(blocks)
        x = burst.symbols_audio(voice_stream_symbols(pay, a.cc, mp=mp), a.fs, a.amp)
        burst.write_wav(a.out, x, a.fs)
        print(f"{a.out}: {len(pay)} voice bursts, {len(x) / a.fs:.2f} s")
        return 0
    x, fs = burst.read_wav(a.wav)
    if a.preemph:
        x = burst.preemphasis(x, fs)
    mp = [tuple(m) for m in json.load(open(a.map))] if a.map else None
    ev, vb, (r, blocks) = decode_voice(x, fs, a.thr, mp)
    kinds = {}
    for e in ev:
        if e.get("kind") == "data":
            kinds[e["dtype"]] = kinds.get(e["dtype"], 0) + 1
    n_a = sum(e.get("kind") == "voice" for e in ev)
    print(f"bursts {len(ev)} data types {dict(sorted(kinds.items()))} (1 LC header, 2 terminator) "
          f"voice syncs {n_a} payloads {len(vb)}")
    if a.learn:
        if not vb:
            print("no voice bursts")
            return 1
        mp, weak, perm, off, plain, learned, n = learn_pn(vb)
        print(f"test pattern from burst {off}: bit errors plain map {plain}/{n}, learned map {learned}/{n}, "
              f"weakest correlation {weak:.2f}, permutation {perm}")
        json.dump(mp, open(a.learn, "w"))
        print(f"{a.learn}: map written")
        return 0 if perm else 1
    good = sum(b is not None for b in blocks)
    print(f"blocks CRC good {good}/{len(blocks)}")
    if r is None:
        print("no dmrsotto head block")
        return 1
    print({k: v for k, v in r.items() if k != "msg"}, f"{len(r['msg']) // dmrsotto.SF_BYTES} superframes")
    if a.out:
        open(a.out, "wb").write(r["msg"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
