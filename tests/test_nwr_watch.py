"""tools/nwr_watch.py on a made-up alerts document (no network)"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import nwr_watch as nw  # noqa: E402


def alert(event, codes, eas=True):
    par = {"BLOCKCHANNEL": ["EAS", "NWEM", "CMAS"]}
    if eas:
        par = {"EAS-ORG": ["WXR"]}
    return {"properties": {"event": event, "geocode": {"SAME": codes}, "parameters": par,
                           "sent": "2026-11-11T14:00:00-08:00", "ends": "2026-11-11T20:00:00-08:00"}}


def test_only_eas_alerts_for_the_counties_count():
    doc = {"features": [
        alert("Wind Advisory", ["053073"], eas=False),        # no tones: blocked from EAS
        alert("Flood Warning", ["053033"]),                   # tones, but another county
        alert("Flood Statement", ["053057", "053061"]),       # tones in Skagit
    ]}
    tue = dt.datetime(2026, 11, 10, 20, 0, tzinfo=dt.timezone.utc)
    v, lines = nw.verdict(doc, tue)
    assert v == "LISTEN" and len(lines) == 1 and lines[0].startswith("Flood Statement: 053057 "), lines
    v, lines = nw.verdict({"features": doc["features"][:2]}, tue)
    assert (v, lines) == ("QUIET", [])


def test_weekly_test_window_is_pacific():
    rwt = nw.parse_rwt("wed 10:00-12:30")
    inside = dt.datetime(2026, 11, 11, 19, 0, tzinfo=dt.timezone.utc)    # 11:00 PST
    after = dt.datetime(2026, 11, 11, 21, 0, tzinfo=dt.timezone.utc)     # 13:00 PST
    assert nw.verdict({"features": []}, inside, rwt=rwt) == ("LISTEN", ["Required Weekly Test window open"])
    assert nw.verdict({"features": []}, after, rwt=rwt)[0] == "QUIET"
