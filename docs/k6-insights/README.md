# K6 insights

Ideas from the K6 work that might change the plan, one file each. None has been tested unless
it says so. Until 2026-11-09 they were one numbered list at the end of `docs/k6-modules.md`,
and nearly every thread that added one hit a merge conflict there; now each idea is its own
file and two threads only collide if they pick the same number.

## Adding one

    python3 tools/k6_insights.py new "Read the squelch tail as a carrier ID"

writes `docs/k6-insights/NN-read-the-squelch-tail-as-a-carrier-id.md` with the next free number
and the usual parts. Fill them in:

- **From:** the thread or person and the date, with a link to the doc it came out of.
- What it is, in a few lines.
- **It would buy:** what changes if it works.
- **Settled by:** the test or run that would answer it. When one has, say so and when, and keep
  the entry: a settled idea is still worth reading.

If CI says your number is taken (another PR got there first), rename the file to the next free
number and change the title line to match.

## Reading them

    python3 tools/k6_insights.py            every insight: number, title, file
    python3 tools/k6_insights.py 31         one in full
    python3 tools/k6_insights.py -g RSSI    the ones that mention a word

The files sort by number in any file browser. "Insight 31" anywhere in the docs, code or
commit messages means `31-*.md` here.
