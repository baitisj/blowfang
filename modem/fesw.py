"""The report of the K6's front-end switch probe (c/k6/mod/fesw.c, docs/k6-fesw.md).

    python3 -m modem.fesw LOG

LOG: the serial log of a run (the module's lines, with or without "K6 MOD say " ahead). For
each frequency and each state of the switch (0 the path the firmware picks, 1 the other path,
2 neither) the median over the rounds of the RSSI's mean, and the loss against state 0 of the
same round. A frequency whose state 0 varies by more than SPLIT dB over the rounds (a carrier
that came and went: the bench's test carrier, a repeater's key-up) is reported twice, the
rounds above the middle and the rounds below it apart, so that the loss on a strong signal and
the floor's behaviour do not mix. SPDX-License-Identifier: MIT"""
import gzip
import re
import statistics
import sys

SPLIT = 20
LINE = re.compile(r"fe (\d+) f=(\d+) s=([012]) t=(\d+) r=(-?\d+) (-?\d+) (-?\d+) n=(\d+) g=(\d+)( x)?\s*$")


def parse(lines):
    """-> {kHz: {round: {state: (lo, mean, hi, noise, glitch, moved)}}}"""
    out = {}
    for ln in lines:
        m = LINE.search(ln)
        if m:
            rnd, khz, s = int(m[1]), int(m[2]), int(m[3])
            out.setdefault(khz, {}).setdefault(rnd, {})[s] = (int(m[5]), int(m[6]), int(m[7]), int(m[8]),
                                                              int(m[9]), bool(m[10]))
    return out


def _med(v):
    return statistics.median(v) if v else None


def _row(khz, name, rounds):
    """one line: the three states' median mean RSSI, the losses, the noise meter"""
    full = [r for r in rounds if set(r) == {0, 1, 2}]
    if not full:
        return f"fe {khz} {name}: no round with all three states"
    r = [_med([x[s][1] for x in full]) for s in range(3)]
    loss = [_med([x[0][1] - x[s][1] for x in full]) for s in (1, 2)]
    n = [_med([x[s][3] for x in full]) for s in range(3)]
    moved = sum(any(x[s][5] for s in range(3)) for x in full)
    return (f"fe {khz} {name}: {len(full)} rounds, RSSI {r[0]:g} / {r[1]:g} / {r[2]:g} dBm "
            f"(own, other, neither), loss {loss[0]:g} and {loss[1]:g} dB, noise {n[0]:g} / {n[1]:g} / {n[2]:g}"
            + (f", pins moved in {moved}" if moved else ""))


def report(lines):
    out = []
    for khz, rounds in sorted(parse(lines).items()):
        rs = [r for _, r in sorted(rounds.items()) if 0 in r]
        own = [r[0][1] for r in rs]
        if own and max(own) - min(own) > SPLIT:
            mid = (max(own) + min(own)) / 2
            out.append(_row(khz, "carrier", [r for r in rs if r[0][1] >= mid]))
            out.append(_row(khz, "no carrier", [r for r in rs if r[0][1] < mid]))
        else:
            out.append(_row(khz, "all", rs))
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        sys.exit(__doc__)
    op = gzip.open if argv[0].endswith(".gz") else open
    with op(argv[0], "rt", errors="replace") as f:
        print("\n".join(report(f.read().splitlines()) or ["no fe lines"]))


if __name__ == "__main__":
    main()
