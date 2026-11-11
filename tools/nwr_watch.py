"""NOAA Weather Radio timing: when is a SAME burst (the alert tones) likely on the air, so a K6
weather-radio listen can be timed to it (docs/k6-pocsag.md, NOAA SAME).

  python3 tools/nwr_watch.py                      # once: the alerts now, and the verdict
  python3 tools/nwr_watch.py --every 300          # every 5 min, a line only when it changes
  python3 tools/nwr_watch.py --file alerts.json   # an alerts document saved earlier

It reads api.weather.gov/alerts/active?area=WA (CAP alerts as GeoJSON). An alert can make the
transmitter send SAME tones only when it is an EAS product: parameters.EAS-ORG is WXR (warnings,
watches, and statements such as FLS and SPS). Advisories (wind WIY, coastal flood CFY) carry SAME
event code NWS, no EAS-ORG, and BLOCKCHANNEL EAS NWEM CMAS: no tones. The tones go out when a
product is issued or updated, so an active WXR alert for a county the transmitter serves makes
more bursts likely (updates, extensions, the cancellation) for as long as it lasts. The other
sure burst is the Required Weekly Test, on Wednesdays late in the morning, Pacific; --rwt sets
the window (the default is a guess until the transmitter's own schedule is checked).

Each alert that counts gives a line: its event, the SAME codes it names among --same, when it
was sent and when it ends. The verdict line: LISTEN (an alert that counts is active, or the RWT
window is open) or QUIET. --once exits 0 for LISTEN and 1 for QUIET. Receive only: nothing here
touches a radio. api.weather.gov wants a User-Agent; this one names the tool, nothing else.
SPDX-License-Identifier: MIT"""
import argparse
import datetime as dt
import json
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo

URL = "https://api.weather.gov/alerts/active?area={area}"
UA = "blowfang-nwr-watch/1 (receive-only weather radio timing)"
PACIFIC = ZoneInfo("America/Los_Angeles")
# hamlet's county and its neighbour (SAME 0SSCCC: Whatcom, Skagit); the counties the K6's
# transmitter serves belong here once that is checked
SAME_CODES = (
    "053073",
    "053057",
)
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def fetch(area="WA", timeout=20):
    req = urllib.request.Request(URL.format(area=area),
                                 headers={"User-Agent": UA, "Accept": "application/geo+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def counts(feature, same):
    """the alert's SAME codes among same if it can make tones (EAS-ORG WXR), else ()"""
    p = feature.get("properties", {})
    par = p.get("parameters", {}) or {}
    if "WXR" not in (par.get("EAS-ORG") or []):
        return ()
    codes = (p.get("geocode", {}) or {}).get("SAME") or []
    return tuple(c for c in codes if c in same)


def parse_rwt(s):
    """'wed 10:00-12:30' -> (weekday 0-6, start minutes, end minutes), Pacific"""
    day, span = s.lower().split()
    a, b = span.split("-")
    mins = [int(x.split(":")[0]) * 60 + int(x.split(":")[1]) for x in (a, b)]
    return DAYS.index(day[:3]), mins[0], mins[1]


def rwt_open(now_utc, rwt):
    t = now_utc.astimezone(PACIFIC)
    m = t.hour * 60 + t.minute
    return t.weekday() == rwt[0] and rwt[1] <= m < rwt[2]


def verdict(doc, now_utc, same=SAME_CODES, rwt=parse_rwt("wed 10:00-12:30")):
    """(LISTEN or QUIET, [lines]) for an alerts document at now_utc"""
    lines = []
    for f in doc.get("features", []):
        hit = counts(f, same)
        if not hit:
            continue
        p = f["properties"]
        lines.append(f"{p.get('event', '?')}: {' '.join(hit)} sent {p.get('sent', '?')} "
                     f"ends {p.get('ends') or p.get('expires') or '?'}")
    if rwt_open(now_utc, rwt):
        lines.append("Required Weekly Test window open")
    return ("LISTEN" if lines else "QUIET"), lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--area", default="WA")
    ap.add_argument("--same", default=",".join(SAME_CODES), help="SAME county codes, comma separated")
    ap.add_argument("--rwt", default="wed 10:00-12:30", help="RWT window, Pacific: 'wed 10:00-12:30'")
    ap.add_argument("--file", help="an alerts document saved earlier, instead of fetching")
    ap.add_argument("--every", type=float, help="seconds between looks (a line when it changes)")
    a = ap.parse_args(argv)
    same, rwt = tuple(a.same.split(",")), parse_rwt(a.rwt)
    last = None
    while True:
        now = dt.datetime.now(dt.timezone.utc)
        try:
            doc = json.load(open(a.file)) if a.file else fetch(a.area)
            v, lines = verdict(doc, now, same, rwt)
        except (OSError, ValueError) as e:
            v, lines = "ERROR", [str(e)]
        if (v, lines) != last:
            stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            print(f"{stamp} {v}", flush=True)
            for ln in lines:
                print(f"  {ln}", flush=True)
            last = (v, lines)
        if not a.every:
            return 0 if v == "LISTEN" else 1
        time.sleep(a.every)


if __name__ == "__main__":
    sys.exit(main())
