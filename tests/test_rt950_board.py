"""The RT-950 Pro SURF image (radios/rt950 `make surf-image`) on the emulated board (sim/rt950):
it boots into dual-chip SURF, both chips work as SURF means them to (chip 0 parked on the
listening frequency, chip 1 sweeping round it), a station shows as the tallest bar where it is,
the keys tune it, nothing it does is a transmit setting, the fast paths agree with the bus, and
SoapyFang's chip model feeds both chips. MIT

Needs a checkout of the base firmware (jaymart1983/Radtel-RT950-Pro-Firmware at the commit
.github/workflows/rt950.yml pins) at ./jm or $RT950_JM, and arm-none-eabi-gcc; skipped without."""
import os
import time
import shutil
import sys

import pytest

pytest.importorskip("unicorn")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from sim.rt950 import board as bd  # noqa: E402
from sim.band import Station  # noqa: E402

JM = os.environ.get("RT950_JM") or os.path.join(ROOT, "jm")
X0, BAR_W, BAR_Y, BAR_H, NV = (240 - 3 * 63) // 2, 3, 80, 150, 63     # jm_glue.c's bars
VFO_HZ = 146_520_000                    # the base firmware's VFO A with blank settings
RX_STATES = {0x0000, 0x0200, 0xBFF1}    # REG_30: off, the tune strobe, receive (bk4829_set_frequency)


@pytest.fixture(scope="module")
def built():
    if not os.path.isdir(os.path.join(JM, "src")) or not shutil.which("arm-none-eabi-gcc"):
        pytest.skip("no base firmware checkout ($RT950_JM or ./jm) or no arm-none-eabi-gcc")
    return bd.build(JM)


def band():
    return [Station("simplex", VFO_HZ + 200_000, -60, "carrier", width_hz=3000),
            Station("weak", VFO_HZ - 300_000, -112, "carrier", width_hz=3000)]


@pytest.fixture(scope="module")
def surfing(built):
    b = bd.RT950Board(*built, band=band())
    b.run_for(5.0)
    return b


def bars(b):
    """each bin's bar height in pixels, read off the screen"""
    out = []
    for i in range(NV):
        x = X0 + BAR_W * i
        out.append(sum(1 for y in range(BAR_Y, BAR_Y + BAR_H) if b.pixel(x, y)))
    return out


def test_the_image_fits_and_starts_where_the_bootloader_jumps(built):
    image, syms = built
    assert len(image) < 1012 * 1024
    sp, pc = int.from_bytes(image[0:4], "little"), int.from_bytes(image[4:8], "little")
    assert sp & 0x2FFE0000 == 0x20000000                  # the bootloader's whole app check
    assert bd.APP < pc < bd.APP + len(image) and pc & 1
    assert "rt950_surf2" in syms and "surf2_run" in syms
    assert bd.RT950Board(*built).fast_bus                 # the driver functions the fast path hooks


def test_boots_into_surf_with_both_chips(surfing):
    b = surfing
    assert b.fast_bus and b.fast_lcd
    assert b.chips[0].hz == VFO_HZ                        # chip 0 listens on the VFO's frequency
    swept = {v for c, a, v, rw in b.rf_log if c == 1 and a == 0x38 and rw == "w"}
    assert len(swept) > 30                                # chip 1 moves
    reads = {c for c, a, v, rw in b.rf_log if a == 0x67 and rw == "r"}
    assert reads == {0, 1}                                # both chips' RSSI read
    assert b.lcd["madctl"] is not None and any(bars(b))


def top(b):
    h = bars(b)
    i = max(range(NV), key=lambda j: h[j])
    assert h[i] > 3 * (sorted(h)[NV // 2] + 1), h       # well above the floor's bars
    return i


def test_a_station_is_the_tallest_bar_on_its_side(surfing, built):
    """+200 kHz: right of the middle; mirrored to -200 kHz: left of it, as far out (the bins are
    a lens, not a linear scale: surf_core.inc's off[])"""
    right = top(surfing)
    b = bd.RT950Board(*built, band=[Station("simplex", VFO_HZ - 200_000, -60, "carrier", width_hz=3000)])
    b.run_for(5.0)
    left = top(b)
    assert right > 31 > left
    assert abs((right - 31) - (31 - left)) <= 1


def test_keys_tune_the_listener(built):
    b = bd.RT950Board(*built, band=band())
    b.run_for(3.0)
    hz = b.chips[0].hz
    b.tap("UP")
    b.run_for(0.6)
    assert b.chips[0].hz > hz
    up = b.chips[0].hz
    b.tap("DOWN")
    b.run_for(0.6)
    assert b.chips[0].hz < up


def test_nothing_is_a_transmit_setting(built):
    b = bd.RT950Board(*built, band=band())
    b.run_for(3.0)
    pins = (b.odr[bd.B] >> 1 & 1, b.odr[bd.E] >> 14 & 1, b.odr[bd.B] >> 8 & 1)   # V3T, SW3T, the amp
    b.press("PTT")                                         # held through keys and a minute of SURF
    for k in ("UP", "#", "DOWN", "OK", "ABC"):
        b.tap(k)
        b.run_for(0.4)
    b.run_for(2.0)
    b.press("PTT", False)
    for c in b.chips:
        assert set(c.states) <= RX_STATES, [hex(v) for v in c.states]
    assert (b.odr[bd.B] >> 1 & 1, b.odr[bd.E] >> 14 & 1, b.odr[bd.B] >> 8 & 1) == pins
    assert not any(op[0] in (0x02, 0x20, 0xD8, 0xC7) for op in b.flash_ops)   # no SPI flash write
    assert not b.flash_ctrl_writes                          # nor the MCU's own flash


def test_the_fast_bus_writes_what_the_bit_level_bus_writes(built):
    """the first 400 register writes, chip by chip, from the driver's bit-banged bus decoded
    edge by edge, and from the fast path at its register functions"""
    def writes(fast):
        b = bd.RT950Board(*built, band=band(), fast_bus=fast)
        while sum(1 for e in b.rf_log if e[3] == "w") < 400:
            b.step(1_000_000)
        return [e[:3] for e in b.rf_log if e[3] == "w"][:400]
    slow, fast = writes(False), writes(True)
    assert slow == fast


def test_soapyfang_feeds_both_chips(built, surfing):
    from sim.rt950.soapy import SoapyRT950Board
    from sim.soapyfang.sources import SyntheticSource
    src = SyntheticSource(band(), rate=1_200_000, center_hz=VFO_HZ)
    b = SoapyRT950Board(*built, source=src)
    try:
        b.run_for(5.0)
    finally:
        b.close()
    assert b.window_misses == 0                             # the sweep stays in chip 0's window
    assert abs(top(b) - top(surfing)) <= 1                  # where the RSSI arithmetic puts it


def test_the_workbench_page_serves_surf(built):
    """python3 -m sim.rt950's page on the synthetic 2 m band: the screen, the state with both chips,
    SoapyFang's panel, a key that tunes, and no PTT key to press"""
    import json
    import threading
    import urllib.error
    from http.server import ThreadingHTTPServer
    import urllib.request
    from sim.rt950 import workbench as wbm
    from sim.soapyfang.sources import SyntheticSource
    src = SyntheticSource(rate=1_200_000, center_hz=VFO_HZ)
    wb = wbm.RT950Workbench(src, built=built)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), wbm.handler(wb))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    get = lambda p: urllib.request.urlopen(url + p, timeout=30).read()
    post = lambda p, body: urllib.request.urlopen(urllib.request.Request(
        url + p, json.dumps(body).encode(), {"Content-Type": "application/json"}), timeout=30)
    try:
        page = get("/").decode()
        assert "RT-950" in page and 'id="sf"' in page and 'data-key="PTT"' not in page
        assert get("/api/screen.png")[:8] == b"\x89PNG\r\n\x1a\n"
        s = json.loads(get("/api/state"))
        assert s["tuned_hz"] == VFO_HZ and s["sweep_hz"] and not s["error"]
        assert {st["name"] for st in s["stations"]} >= {"calling", "repeater A"}   # the 2 m band
        assert json.loads(get("/api/soapyfang"))["tuned_hz"] == VFO_HZ
        post("/api/key", {"key": "UP", "action": "tap"})
        t = time.time()
        while json.loads(get("/api/state"))["tuned_hz"] == VFO_HZ and time.time() - t < 30:
            time.sleep(0.2)
        assert json.loads(get("/api/state"))["tuned_hz"] > VFO_HZ
        with pytest.raises(urllib.error.HTTPError):
            post("/api/key", {"key": "PTT", "action": "tap"})
    finally:
        srv.shutdown()
        srv.server_close()
        wb.stop()
