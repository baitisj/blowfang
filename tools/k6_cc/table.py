"""Table of tools/k6_cc/compare.sh's results: flash per image, static RAM, deepest stack, and the
emulated decoder's MHz with a PCM check against the first config.
Usage: python tools/k6_cc/table.py OUT CONFIG [CONFIG ...]   SPDX-License-Identifier: MIT"""
import filecmp
import json
import os
import re
import sys

IMAGES = [
    'aura-pager-only',
    'aura-pager-lr',
    'aura-pager-vlr',
    'aura-pager-vlrh',
    'aura-explore',
    'aura-pager-vlr-rom',
    'aura-pager-vlri',
]
BENCHES = [
    'v37',
    'c1200',
    'c600',
    'c400',
    'h1200',
    'h400',
]


def fit(out, cfg, img):
    p = os.path.join(out, cfg, f'size-{img}.txt')
    if not os.path.exists(p):
        return None
    t = open(p).read()
    m = re.search(r'flash (\d+) of 122880.*static RAM (\d+)', t)
    s = re.search(r'running stack: deepest direct calls (\d+)', t)
    return int(m[1]), int(m[2]), (int(s[1]) if s else None)


def main():
    out, cfgs = sys.argv[1], sys.argv[2:]
    base = cfgs[0]
    w = max(16, max(len(c) for c in cfgs) + 2)
    print(f'flash bytes of 122,880 (change against {base})')
    print('%-14s' % 'image' + ''.join(f'{c:>{w}}' for c in cfgs))
    for img in IMAGES:
        b = fit(out, base, img)
        row = '%-14s' % img.replace('aura-', '')
        for c in cfgs:
            f = fit(out, c, img)
            cell = '-' if not f else (str(f[0]) if c == base or not b else '%d (%+d)' % (f[0], f[0] - b[0]))
            row += f'{cell:>{w}}'
        print(row)
    print('\nstatic RAM / deepest direct-call stack, bytes')
    for img in IMAGES:
        row = '%-14s' % img.replace('aura-', '')
        for c in cfgs:
            f = fit(out, c, img)
            cell = '-' if not f else '%d/%s' % (f[1], f[2] if f[2] is not None else '-')
            row += f'{cell:>{w}}'
        print(row)
    rows = []
    for t in BENCHES:
        row, seen = '%-14s' % t, False
        for c in cfgs:
            p = os.path.join(out, c, f'bench-{t}.json')
            if not os.path.exists(p):
                row += f'{"-":>{w}}'
                continue
            seen = True
            d = json.load(open(p))
            pb, pc = os.path.join(out, base, f'pcm-{t}.raw'), os.path.join(out, c, f'pcm-{t}.raw')
            same = os.path.exists(pb) and os.path.exists(pc) and filecmp.cmp(pb, pc, False)
            cell = '%.1f/%.1f%s' % (d['play_MHz']['mean'], d['play_MHz']['worst'], '' if same else ' PCM!')
            row += f'{cell:>{w}}'
        if seen:
            rows.append(row)
    if rows:
        print(f'\ndecoder MHz per 20 ms frame, mean/worst (96 MHz clock; PCM! = output differs from {base})')
        print('\n'.join(rows))


main()
