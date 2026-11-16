#!/usr/bin/env python3
"""Sweeps the polar SSB radio model (c/common/ssb_polar_host) over what a radio delivers: PLL
writes a second, PLL step, PA levels, PA update rate and the delay between the two paths, on a
two-tone (700 + 1900 Hz), synthetic speech and (if it is there) real speech, and prints the
opposite-sideband suppression as markdown tables for docs/rt950-polar-sim.md.

  make -C c/common ssb_polar_host
  python3 tools/ssb_polar_sweep.py [--speech FILE.raw] [--grid] > tables.md

--speech: 8 kHz signed 16-bit speech (default: sotto/codec/codec2/raw/hts1a.raw when the
sotto submodule is checked out). --grid adds the full N x Q x P x D grid (144 rows).
MIT licence."""
import argparse
import itertools
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST = os.path.join(ROOT, "c", "common", "ssb_polar_host")
HTS1A = os.path.join(ROOT, "sotto", "codec", "codec2", "raw", "hts1a.raw")

# the ideal radio: continuous phase and envelope, no quantisation
IDEAL = (
    "--writes", "0",
    "--step", "0",
    "--levels", "0",
    "--env-rate", "0",
)

# the values each table sweeps, one a line
WRITES = (
    1000,
    2000,
    4000,
    8000,
    16000,
    32000,
)
STEPS = (
    1,
    10,
    50,
    100,
    250,
)
ENV_RATES = (
    4000,
    8000,
    16000,
    32000,
)
LEVELS = (
    16,
    32,
    64,
    128,
    256,
    1024,
)
DELAYS = (
    0,
    0.05,
    0.1,
    0.25,
    0.5,
    1,
    2,
)
GRID_WRITES = (
    1000,
    2000,
    4000,
    8000,
)
GRID_STEPS = (
    1,
    10,
    50,
)
GRID_LEVELS = (
    16,
    64,
    256,
)
GRID_DELAYS = (
    0,
    0.5,
    1,
    2,
)


def run(sig, *opts):
    """-> {key: value} from the host's one line"""
    out = subprocess.run([HOST, *sig, *opts], check=True, capture_output=True, text=True).stdout
    return {k: v for k, v in (w.split("=", 1) for w in out.split() if "=" in w)}


def sup(sig, *opts):
    return float(run(sig, *opts)["sup_db"])


def signals(speech):
    s = [("two-tone", ()), ("speech (syn)", ("--speech", "--secs", "4"))]
    if speech and os.path.exists(speech):
        s.append(("speech (%s)" % os.path.basename(speech).split(".")[0], ("--in", speech)))
    return s


def table(title, note, rows, sigs, pool):
    """rows: [(label, opts)] -> a markdown table of sup_db per signal"""
    jobs = {(r, n): pool.submit(sup, sig, *o) for r, o in rows for n, sig in sigs}
    lines = ["### " + title, "", note, ""]
    lines.append("| | " + " | ".join(n for n, _ in sigs) + " |")
    lines.append("|---|" + "---:|" * len(sigs))
    for r, o in rows:
        lines.append("| %s | " % r + " | ".join("%.1f" % jobs[(r, n)].result() for n, _ in sigs)
                     + " |")
    return "\n".join(lines) + "\n"


def ideal_but(**kw):
    """the ideal radio with some of its options replaced"""
    o = dict(zip(IDEAL[0::2], IDEAL[1::2]))
    o.update({"--" + k.replace("_", "-"): str(v) for k, v in kw.items()})
    return tuple(itertools.chain.from_iterable(o.items()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--speech", default=HTS1A)
    ap.add_argument("--grid", action="store_true")
    a = ap.parse_args()
    if not os.path.exists(HOST):
        sys.exit("needs make -C c/common ssb_polar_host")
    sigs = signals(a.speech)
    out = []
    with ThreadPoolExecutor(os.cpu_count() or 4) as pool:
        out.append(table(
            "Polar rate and filter (ideal radio)",
            "Opposite-sideband suppression, dB. The polar split at 8 kHz x up, the radio "
            "continuous and exact. This is the ceiling for everything below.",
            [("up 1 (8 kHz), 127 taps", ("--up", "1") + IDEAL),
             ("up 2 (16 kHz), 127 taps", ("--up", "2") + IDEAL),
             ("up 4 (32 kHz), 127 taps", ("--up", "4") + IDEAL),
             ("up 8 (64 kHz), 127 taps", ("--up", "8") + IDEAL),
             ("up 4 (32 kHz), 63 taps", ("--up", "4", "--taps", "63") + IDEAL)],
            sigs, pool))
        out.append(table(
            "Frequency clamp (ideal radio)",
            "The uSDX-style clamp of the instantaneous frequency. track: the clamped-off phase "
            "is paid back later; plain: it is lost.",
            [("none", IDEAL),
             ("plain 0..3000 Hz", IDEAL + ("--clamp", "plain")),
             ("track 0..3000 Hz", IDEAL + ("--clamp", "track")),
             ("plain -1000..4000 Hz", IDEAL + ("--clamp", "plain", "--fmin", "-1000", "--fmax", "4000")),
             ("track -1000..4000 Hz", IDEAL + ("--clamp", "track", "--fmin", "-1000", "--fmax", "4000")),
             ("track -3000..6000 Hz", IDEAL + ("--clamp", "track", "--fmin", "-3000", "--fmax", "6000")),
             ("track -8000..11000 Hz", IDEAL + ("--clamp", "track", "--fmin", "-8000", "--fmax", "11000"))],
            sigs, pool))
        out.append(table(
            "PLL write rate N (frequency path only)",
            "10 Hz steps with error feedback; the envelope continuous and exact, no delay.",
            [("N = %d /s" % n, ideal_but(writes=n, step=10)) for n in WRITES],
            sigs, pool))
        out.append(table(
            "PLL step Q at N = 8000 /s (frequency path only)",
            "fb: error feedback (the PLL phase tracks the wanted phase); round: each write "
            "rounded on its own.",
            [("Q = %g Hz, %s" % (q, m), ideal_but(writes=8000, step=q) + (("--round",) if m == "round" else ()))
             for m in ("fb", "round") for q in STEPS],
            sigs, pool))
        out.append(table(
            "PA update rate (envelope path only)",
            "Each PA level held for its update interval (zero-order hold), levels exact; the "
            "frequency path continuous.",
            [("%d /s" % r, ideal_but(env_rate=r)) for r in ENV_RATES]
            + [("continuous", IDEAL)],
            sigs, pool))
        out.append(table(
            "PA levels P (envelope path only)",
            "Linear levels from 0 to the envelope's peak; continuous otherwise.",
            [("P = %d" % p, ideal_but(levels=p)) for p in LEVELS],
            sigs, pool))
        out.append(table(
            "Delay mismatch D (8 kHz samples; 1 = 125 us)",
            "The envelope late by D against the frequency; continuous and exact otherwise.",
            [("D = %g (%g us)" % (d, d * 125), ideal_but(delay=d)) for d in DELAYS],
            sigs, pool))
        recipes = [
            ("N 4000, Q 10, PA 4000/s x 64, D 0", ("--writes", "4000", "--env-rate", "4000", "--levels", "64")),
            ("N 8000, Q 10, PA 8000/s x 64, D 0", ("--writes", "8000", "--env-rate", "8000", "--levels", "64")),
            ("N 8000, Q 10, PA 8000/s x 64, D 0.1", ("--writes", "8000", "--env-rate", "8000", "--levels", "64", "--delay", "0.1")),
            ("N 8000, Q 10, PA 8000/s x 256, D 0", ("--writes", "8000", "--env-rate", "8000", "--levels", "256")),
            ("N 8000, Q 10, PA continuous x 64, D 0", ("--writes", "8000", "--env-rate", "0", "--levels", "64")),
            ("N 16000, Q 10, PA 16000/s x 128, D 0", ("--writes", "16000", "--env-rate", "16000", "--levels", "128")),
            ("N 16000, Q 10, PA 16000/s x 256, D 0.05", ("--writes", "16000", "--env-rate", "16000", "--levels", "256", "--delay", "0.05")),
            ("N 16000, Q 10, PA 32000/s x 256, D 0", ("--writes", "16000", "--env-rate", "32000", "--levels", "256")),
            ("N 32000, Q 10, PA 32000/s x 256, D 0", ("--writes", "32000", "--env-rate", "32000", "--levels", "256")),
        ]
        out.append(table(
            "Whole radios",
            "Everything quantised at once (10 Hz PLL steps with error feedback, linear PA "
            "levels, zero-order hold on both paths).",
            recipes, sigs, pool))
        if a.grid:
            rows = [("N %d, Q %g, P %d, D %g" % (n, q, p, d),
                     ("--writes", str(n), "--step", str(q), "--levels", str(p), "--delay", str(d),
                      "--env-rate", "8000"))
                    for n in GRID_WRITES for q in GRID_STEPS for p in GRID_LEVELS
                    for d in GRID_DELAYS]
            out.append(table(
                "The full grid (PA at 8000 updates/s)",
                "N PLL writes/s, Q Hz steps (error feedback), P PA levels, D samples of delay.",
                rows, sigs, pool))
    print("\n".join(out))


if __name__ == "__main__":
    main()
