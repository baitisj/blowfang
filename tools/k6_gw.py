"""GROUNDWAVE (c/k6/mod/gw.c) on the bench: decode a recording of a K6's overs, or play the K6
from the PC over the IC-7100.

  python3 tools/k6_gw.py decode RUN.raw            raw s16le mono 48 kHz (k6_upload.py --keep)
  python3 tools/k6_gw.py decode RUN.wav
  python3 tools/k6_gw.py play [--skill fair] [--call]
  python3 tools/k6_gw.py sim [--skill fair]        the PC against itself, no radio

decode prints every GROUNDWAVE packet (magic 0xA6) whose CRC checks, with its time, type, seq,
game id and body, and any K6 upload report (0xAF) in the same audio. Each over carries its
packet twice, so a good over shows two lines a few hundred ms apart. It keys nothing.

play is the PC opponent (modem/k6gw.py: Peer with an Aimer that tries every shot on a copy of
the game, and buys from the shop). Start gw.c on the K6 (key 2, radio), then run play and call
from the K6 (MENU in the lobby); or play --call and answer on the K6. It records the IC-7100's
receive audio (parec), decodes the K6's overs as they come, and answers with overs of its own:
the packet twice on PHY F/1800, then AG7EW in MCW at 25 wpm, keyed through tools/k6_bench.py
(PTT lock, bench owner, 70 cm), only when the channel is clear (S-meter) and 3.5 s after the
K6's last packet. A reliable packet goes again after 14 s without its ACK, at most 8 times.
It refuses unless the IC-7100 is on 440.850. The run's audio, overs and log go to
$BLOWFANG_WORK/runs/gw-play-<UTC>/. Ctrl-C ends it (the K6 then times out by itself).
SPDX-License-Identifier: MIT
"""
import argparse
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
from modem import fsk, k6gw, k6mod, pager  # noqa: E402

FS = 48000
BAUD = 1800
ID_WPM = 25           # gw.c's MCW ID
WINDOW_S = 8.0        # play decodes the recording's last 8 s each pass (an over is about 4 s)


def load(path):
    if path.endswith(".wav"):
        import soundfile
        x, fs = soundfile.read(path, dtype="float64")
        return (x if x.ndim == 1 else x[:, 0]), fs
    return np.fromfile(path, dtype="<i2").astype(float) / 32768, 48000


def decode(x, fs, baud=1800):
    """audio -> [(time s, packet dict)], GROUNDWAVE packets only, either word order"""
    d = fsk.Demodulator("F", baud, fsk.FS, 64)
    y = fsk.resample(np.asarray(x, dtype=float), fs, fsk.FS)
    out = []
    if len(y) < fsk.FS // 2:
        return out
    for start, p, _, _ in d.demodulate(y):
        for q in (p, pager.swap_words(p)):
            pk = k6gw.parse(bytes(q))
            if pk is not None:
                pk["swapped"] = q is not p
                pk["raw"] = bytes(q)
                out.append((start / fsk.FS, pk))
                break
    return out


def over_audio(pkt, fs=FS, level=0.3):
    """one over as gw.c sends it: lead, the packet twice on PHY F/1800, then AG7EW in MCW"""
    from modem import k6
    bits = pager.Air(lead_ms=300.0).burst_bits([pkt, pkt], BAUD)
    x = fsk.modulate(bits, "F", BAUD, fs, level)
    return np.concatenate([x, np.zeros(int(0.2 * fs)), k6.cw("AG7EW", fs, ID_WPM, 800.0, min(level, 0.4))])


def show(game, me):
    s = game.state()
    return (f"round {s.round}/{s.rounds} turn {s.turn} wind {s.wind:+d}  "
            f"K6 hp {s.p[1 - me].hp} wins {s.p[1 - me].wins}  PC hp {s.p[me].hp} wins {s.p[me].wins}"
            f" ${s.p[me].money}")


def say(*a):
    print(time.strftime("%H:%M:%S", time.gmtime()), *a, flush=True)


def make_peer(a):
    aim = k6gw.Aimer(a.skill, seed=a.seed)
    peer = k6gw.Peer(None, aim=aim, shop=None if a.no_shop else k6gw.buy)
    return peer, aim


def cmd_sim(a):
    """the PC's opponent against a second PC peer through two Links and a perfect channel"""
    q, t = [], 0.0
    pc, aim = make_peer(a)
    other = k6gw.Peer(None, aim=k6gw.Aimer("fair", seed=1), shop=k6gw.buy)
    la, lb = k6gw.Link(pc), k6gw.Link(other)
    other.call(a.seed or 0x2468ACE, rounds=a.rounds)
    while t < 3600 and not (pc.done and other.done and la.idle() and lb.idle()):
        for src, dst in ((la, lb), (lb, la)):
            pk = src.due(t)
            if pk is not None:
                src.sent(t)
                q.append((t + 0.5, dst, pk))
                t += 4.0
        for item in [i for i in q if i[0] <= t]:
            q.remove(item)
            item[1].heard(item[2], item[0])
        t += 0.5
    print(show(pc.game, pc.me), f"desync {pc.desync}", f"{t / 60:.1f} min of air")
    return 0


def cmd_play(a):
    import k6_bench as bench
    import k6_upload
    peer, aim = make_peer(a)
    link = k6gw.Link(peer)
    rig = bench.rigctl(a.rigctld)
    bench.check_owner()
    hz = bench.rig_hz(rig)
    if hz != a.freq or not bench.in_band(hz):
        raise SystemExit(f"refusing: IC-7100 on {hz} Hz, not {a.freq} (gw.c plays on 440.850 only)")
    d = os.path.join(bench.work(), "runs", time.strftime("gw-play-%Y%m%dT%H%MZ", time.gmtime()))
    os.makedirs(d, exist_ok=True)
    ns = argparse.Namespace(dry_run=False, rec_device=a.rec_device)
    rec = k6_upload.Recorder(ns, d)
    done_raw, n_over = set(), 0
    t_start = time.time()
    if a.call:
        seed = a.seed or int(time.time()) & 0x7FFFFFFF
        peer.call(seed, rounds=a.rounds)
        say(f"calling: seed {seed:08x}, {a.rounds} rounds")
    else:
        say("listening for the K6's call on 440.850 (MENU in gw.c's radio lobby)")
    try:
        while not (peer.done and link.idle()):
            if time.time() - t_start > a.max_min * 60:
                say("time up")
                break
            time.sleep(0.4)
            n = rec.samples()
            w = max(0, n - int(WINDOW_S * FS))
            for t, pk in decode(rec.audio(w), FS, BAUD):
                at = (w + t * FS) / FS
                key = (pk["raw"], round(at, 1))
                if key in done_raw:
                    continue
                done_raw.add(key)
                before = peer.game.state().turn if peer.game else None
                got = link.heard(pk["raw"], at)
                if got is not None:
                    say(f"rx {k6gw.NAMES.get(got['type'], got['type'])} seq={got['seq']}"
                        + (f" angle {got['body'][0]} power {got['body'][1]} item {got['body'][2]}"
                           if got["type"] == k6gw.MOVE else ""))
                    if peer.game and peer.game.state().turn != before:
                        say(show(peer.game, peer.me))
            now = rec.samples() / FS
            try:
                busy = int(float(rig("l", "STRENGTH"))) > a.busy_db
            except (ValueError, OSError, bench.subprocess.CalledProcessError):
                busy = False
            pk = link.due(now, busy)
            if pk is None:
                continue
            p = k6gw.parse(pk)
            name = k6gw.NAMES.get(p["type"], p["type"])
            n_over += 1
            wav = os.path.join(d, f"over-{n_over:03d}-{name.lower()}.wav")
            bench.write_wav(wav, over_audio(pk, FS, a.level), FS)
            if p["type"] == k6gw.MOVE:
                say(f"tx MOVE angle {p['body'][0]} power {p['body'][1]} item {p['body'][2]}"
                    f" (aimed for {aim.last[3]:.0f} points)")
            else:
                say(f"tx {name} seq={p['seq']}")
            bench.key_and_play(rig, wav, a.device, what=f"GROUNDWAVE {name}")
            link.sent(now)
            if p["type"] == k6gw.MOVE and peer.game:
                say(show(peer.game, peer.me))
    except KeyboardInterrupt:
        say("stopped by hand")
    finally:
        rec.stop()
    if peer.game:
        say("game over:" if peer.done else "left:", show(peer.game, peer.me), f"desync {peer.desync}")
        with open(os.path.join(d, "result.txt"), "w") as f:
            f.write(show(peer.game, peer.me) + f" done={peer.done} desync={peer.desync}\n")
            for t, way, pkd in link.log:
                f.write(f"{t:8.2f} {way} {k6gw.NAMES.get(pkd['type'], pkd['type'])} seq={pkd['seq']}"
                        f" body={pkd['body'][:4].hex()}\n")
        return 0 if peer.done else 1
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    dp = sub.add_parser("decode")
    dp.add_argument("file")
    dp.add_argument("--baud", type=float, default=1800)
    for name in ("play", "sim"):
        pp = sub.add_parser(name)
        pp.add_argument("--skill", choices=sorted(k6gw.SKILL_ERR), default="fair",
                        help="how far the PC misses on purpose (easy, fair, hard)")
        pp.add_argument("--no-shop", action="store_true", help="the PC buys nothing")
        pp.add_argument("--seed", type=int, help="the game's seed when the PC calls; its aim's errors")
        pp.add_argument("--rounds", type=int, default=3)
    pp = sub.choices["play"]
    pp.add_argument("--call", action="store_true", help="the PC calls (else it answers the K6's call)")
    pp.add_argument("--freq", type=int, default=440_850_000)
    pp.add_argument("--level", type=float, default=0.3, help="the overs' audio peak (0.3)")
    pp.add_argument("--busy-db", type=int, default=-40,
                    help="rigctl's STRENGTH (dB over S9) above which the channel is busy (-40)")
    pp.add_argument("--max-min", type=float, default=60.0, help="give up after this long (60)")
    pp.add_argument("--rigctld", help="host:port (default rigctl's own)")
    pp.add_argument("--device", default=None, help="the IC-7100's USB audio sink (k6_bench's)")
    pp.add_argument("--rec-device", default=None, help="its USB audio source (k6_upload's)")
    a = ap.parse_args()
    if a.cmd == "sim":
        return cmd_sim(a)
    if a.cmd == "play":
        import k6_bench
        import k6_upload
        a.device = a.device or k6_bench.SINK
        a.rec_device = a.rec_device or k6_upload.REC
        return cmd_play(a)
    x, fs = load(a.file)
    pkts = decode(x, fs, a.baud)
    for t, p in pkts:
        print(f"{t:8.2f} s  {k6gw.NAMES.get(p['type'], p['type'])} seq={p['seq']} gid={p['gid']:04x} "
              f"from={p['from']} round={p['round']} turn={p['turn']} call={p['call']} "
              f"body={p['body'][:8].hex()}{' (words swapped)' if p['swapped'] else ''}")
    for t, r in k6mod.reports(x, fs):
        print(f"{t:8.2f} s  K6 report {r}")
    print(f"{len(pkts)} GROUNDWAVE packets")
    return 0 if pkts else 1


if __name__ == "__main__":
    sys.exit(main())
