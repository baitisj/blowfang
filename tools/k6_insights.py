#!/usr/bin/env python3
"""The K6 insights, one file each in docs/k6-insights/ (README.md there says why).

  python3 tools/k6_insights.py                 every insight: number, title, file
  python3 tools/k6_insights.py N               insight N in full
  python3 tools/k6_insights.py -g WORD         the ones that mention WORD (any case)
  python3 tools/k6_insights.py new "Title"     start the next one; prints its path

Standard library only. SPDX-License-Identifier: MIT
"""
import argparse
import datetime
import os
import re
import sys

DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "k6-insights")
NAME = re.compile(r"^(\d{2,})-([a-z0-9]+(?:-[a-z0-9]+)*)\.md$")
STOP = {
    "a",
    "an",
    "and",
    "at",
    "by",
    "for",
    "from",
    "in",
    "no",
    "not",
    "of",
    "on",
    "or",
    "than",
    "the",
    "to",
    "when",
    "with",
}


def slug(title, limit=48):
    """a file name's words: lower case, a-z 0-9 joined by '-', at most `limit` characters,
    no trailing small words"""
    t = re.sub(r"[^a-z0-9]+", "-", title.lower().replace("'", "")).strip("-")
    out = []
    for w in t.split("-"):
        if len("-".join(out + [w])) > limit:
            break
        out.append(w)
    while len(out) > 2 and out[-1] in STOP:
        out.pop()
    return "-".join(out)


def entries(d=DIR):
    """[(number, title, path)] in number order; the title from the file's first line"""
    out = []
    for f in sorted(os.listdir(d)):
        m = NAME.match(f)
        if not m:
            continue
        p = os.path.join(d, f)
        first = open(p, encoding="utf-8").readline().strip()
        t = re.match(r"^# (\d+)\. (.+)$", first)
        out.append((int(m.group(1)), t.group(2) if t else first, p))
    return sorted(out)


def new(title, d=DIR, today=None):
    nums = [n for n, _, _ in entries(d)]
    n = max(nums, default=0) + 1
    title = title.strip().rstrip(".")
    p = os.path.join(d, f"{n:02d}-{slug(title)}.md")
    day = (today or datetime.date.today()).isoformat()
    with open(p, "x", encoding="utf-8") as f:
        f.write(f"# {n}. {title}\n\n"
                f"- From: (thread or person), {day} ([doc](../k6-modules.md)). Not built.\n"
                f"- (What it is.)\n"
                f"- It would buy: \n"
                f"- Settled by: \n")
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", nargs="*", help="N, or new TITLE")
    ap.add_argument("-g", "--grep", help="only the insights that mention this (any case)")
    a = ap.parse_args(argv)
    if a.what[:1] == ["new"]:
        if len(a.what) < 2:
            raise SystemExit('new: give the title, e.g. new "Read the squelch tail"')
        print(new(" ".join(a.what[1:])))
        return 0
    es = entries()
    if a.what:
        n = int(a.what[0])
        for k, _, p in es:
            if k == n:
                sys.stdout.write(open(p, encoding="utf-8").read())
                return 0
        raise SystemExit(f"no insight {n}")
    for k, t, p in es:
        if a.grep and a.grep.lower() not in open(p, encoding="utf-8").read().lower():
            continue
        print(f"{k:3d}  {t}  ({os.path.relpath(p)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
