"""SoapyFang (sim/soapyfang): I/Q from a source, through the emulated BK48xx chip, into the real UV-21
port image. Generated signals only (the synthetic band, SigMF files written here, a stand-in for
the SoapySDR module): no SDR and no off-air recording.

What must hold: the chip's RSSI reads a signal's level to within a dB or two and the floor where
there is none; FM audio comes out at the tone that went in; noise, glitch and squelch tell a
signal from an empty channel; a SigMF file reads like the source it was written from; the window
follows the firmware when it tunes outside it; a SoapySDR receiver never gets a transmit stream
and a Pluto's LO is parked on close; and the real SURF, fed this way, hears the CW wake on 440.850
and finds the peaks in a recording."""
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sim.soapyfang.chip import ChipModel  # noqa: E402
from sim.soapyfang.sources import (PlutoIioSource, SigmfSource, SoapySource, SyntheticSource,  # noqa: E402
                                   open_source, write_sigmf)
from sim.uv21.board import Station  # noqa: E402

F0 = 440_000_000


def chip_on(band, rate=1_200_000, center=F0, seconds=0.05):
    c = ChipModel(SyntheticSource(band, rate=rate, center_hz=center))
    c.advance(seconds)
    return c


def test_rssi_reads_a_carrier_and_the_floor():
    c = chip_on([Station("a", F0 + 100_000, -80)])
    assert abs(c.rssi_dbm(F0 + 100_000) - -80) < 1.5
    assert abs(c.rssi_dbm(F0 + 300_000) - -125) < 2.0
    assert c.rssi_raw(F0 + 100_000) == pytest.approx((-80 + 174) * 2, abs=3)


def test_fm_audio_noise_glitch_and_squelch():
    band = [Station("v", F0 + 50_000, -70, "voice", on_s=10, period_s=10)]
    c = chip_on(band)
    c.tune(F0 + 50_000)
    c.advance(0.4)
    seq, pcm = c.pcm_after(0)
    assert seq > 0 and len(pcm) > 0.25 * c.pcm_rate
    x = pcm[-int(0.25 * c.pcm_rate):].astype(float)
    peak = np.argmax(np.abs(np.fft.rfft(x * np.hanning(len(x))))) * c.pcm_rate / len(x)
    assert abs(peak - 1000) < 40, peak
    assert c.noise_raw() < 20 and c.glitch_raw() == 0
    assert c.read_reg(0x0C, {}) & 0x0002                    # squelch open on the signal
    c.tune(F0 - 200_000)                                    # an empty channel
    c.advance(0.8)
    assert c.noise_raw() > 100 and c.glitch_raw() > 5
    assert not c.read_reg(0x0C, {}) & 0x0002


def test_squelch_follows_reg78_thresholds():
    c = chip_on([Station("a", F0, -100)])
    c.tune(F0)
    c.advance(0.2)
    raw = c.rssi_raw()                                      # (-100 + 174) * 2 = 148: raw/2 = 74
    assert c.read_reg(0x0C, {0x78: (70 << 8) | 66}) & 2      # opens at 70
    assert not c.read_reg(0x0C, {0x78: (90 << 8) | 86}) & 2  # a higher threshold: closed
    assert raw >> 1 == pytest.approx(74, abs=2)


def test_sigmf_file_reads_like_its_source(tmp_path):
    band = [Station("a", F0 + 120_000, -85), Station("b", F0 - 240_000, -95)]
    src = SyntheticSource(band, rate=1_200_000, center_hz=F0)
    base = str(tmp_path / "two-carriers")
    write_sigmf(base, src.read(int(0.2 * src.rate)), src.rate, F0)
    c = ChipModel(SigmfSource(base + ".sigmf-meta"))
    c.advance(0.05)
    assert abs(c.rssi_dbm(F0 + 120_000) - -85) < 1.5
    assert abs(c.rssi_dbm(F0 - 240_000) - -95) < 1.5
    assert abs(c.rssi_dbm(F0 + 400_000) - -125) < 2.5
    assert c.rssi_dbm(F0 + 5_000_000) < -115                 # past the recording: its floor


def test_the_window_follows_the_firmware():
    c = chip_on([Station("far", 445_000_000, -75)])
    assert c.rssi_dbm(445_000_000) == pytest.approx(-75, abs=1.5)
    assert c.retunes == 1 and abs(c.src.center_hz - 445_000_000) < 1


class FakeSoapy:
    """the parts of the SoapySDR module SoapySource uses, recording what it is asked"""
    SOAPY_SDR_RX, SOAPY_SDR_TX, SOAPY_SDR_CF32, SOAPY_SDR_OVERFLOW = 1, 0, "CF32", -4

    def __init__(self):
        self.calls = []
        fake = self

        class Device:
            def __init__(self, args):
                fake.calls.append(("open", args))
                self.f = 0.0
                self.rate = 0.0

            def setSampleRate(self, d, ch, r):
                self.rate = r

            def getSampleRate(self, d, ch):
                return self.rate

            def setGainMode(self, d, ch, auto):
                pass

            def setGain(self, d, ch, g):
                fake.calls.append(("gain", d, g))

            def setFrequency(self, d, ch, f):
                fake.calls.append(("freq", d, f))
                self.f = f

            def getFrequency(self, d, ch):
                return self.f

            def setupStream(self, d, fmt, chans):
                fake.calls.append(("stream", d, fmt))
                return "s"

            def activateStream(self, s):
                pass

            def readStream(self, s, bufs, n, timeoutUs=0):
                bufs[0][:n] = 0.01
                return (n, 0, 0)

            def deactivateStream(self, s):
                pass

            def closeStream(self, s):
                fake.calls.append(("close",))
        self.Device = Device


def test_soapy_source_receives_only_and_parks_a_pluto():
    fake = FakeSoapy()
    src = SoapySource("driver=plutosdr,uri=ip:192.168.2.1", rate=2_400_000, center_hz=440_850_000,
                      gain=40, soapy=fake)
    assert src.center_hz == 440_850_000 + 600_000           # LO a quarter of the rate above
    assert len(src.read(4096)) == 4096
    src.close()
    streams = [c for c in fake.calls if c[0] == "stream"]
    assert streams == [("stream", FakeSoapy.SOAPY_SDR_RX, "CF32")]
    assert all(c[1] == FakeSoapy.SOAPY_SDR_RX for c in fake.calls if c[0] in ("freq", "gain"))
    assert fake.calls[-1] == ("freq", FakeSoapy.SOAPY_SDR_RX, 2.4e9)


class FakePlutoBench:
    """the parts of tools/pluto_bench.py PlutoIioSource uses, recording what it is asked"""
    URI, PHY, RX_DEV, WIDE_BW = "ip:192.168.2.1", "ad9361-phy", "cf-ad9361-lpc", 2_000_000

    def __init__(self):
        self.calls = []

    def setup(self, rate, tx_hz=None, rx_hz=None, gain=None, rx_gain=None, uri=None, rx_bw=200000):
        self.calls.append(("setup", rate, tx_hz, rx_hz, gain, rx_gain, rx_bw))

    def put(self, kind, dev, ch, attr, v, uri=None, check=True):
        self.calls.append(("put", kind, ch, attr, v))

    def park_rx(self, uri=None):
        self.calls.append(("park",))


def fake_readdev(tmp_path, monkeypatch, i=1024, q=-512):
    """an iio_readdev on PATH that streams one constant int16 I/Q sample until its reader goes"""
    d = tmp_path / "bin"
    d.mkdir()
    exe = d / "iio_readdev"
    exe.write_text("#!/usr/bin/env python3\nimport struct, sys\n"
                   f"b = struct.pack('<hh', {i}, {q}) * 4096\n"
                   "try:\n    while True:\n        sys.stdout.buffer.write(b)\n"
                   "except (BrokenPipeError, OSError):\n    pass\n")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{d}{os.pathsep}{os.environ['PATH']}")


def test_pluto_source_receives_only_and_parks(tmp_path, monkeypatch):
    fake_readdev(tmp_path, monkeypatch)
    pb = FakePlutoBench()
    src = PlutoIioSource(pb, rate=2_400_000, center_hz=440_850_000, gain=40)
    assert src.center_hz == 440_850_000 + 600_000            # LO a quarter of the rate above
    setup = pb.calls[0]
    assert setup[0] == "setup" and setup[2] is None and setup[4] is None     # no TX LO, no TX gain
    assert setup[3] == 441_450_000 and setup[5] == 40 and setup[6] == pb.WIDE_BW
    x = src.read(1000)
    assert len(x) == 1000 and np.allclose(x, 0.5 - 0.25j)
    assert src.tune(445_000_000) and src.center_hz == 445_600_000
    assert pb.calls[-1] == ("put", "out", "altvoltage0", "frequency", 445_600_000)   # the RX LO only
    assert len(src.read(100)) == 100                         # after the stale buffers are dropped
    src.close()
    assert pb.calls[-1] == ("park",)
    assert all(c[2] == "altvoltage0" for c in pb.calls if c[0] == "put")
    with pytest.raises(ValueError):
        PlutoIioSource(pb, uri="usb:1.2.5")


def test_an_uncalibrated_source_sets_its_level_from_the_floor():
    """with no calibration (the Pluto's default), the median floor of the window reads auto_floor_dbm"""
    src = SyntheticSource([Station("a", F0 + 100_000, -80)], rate=1_200_000, center_hz=F0)
    src.dbm_offset, src.auto_floor_dbm = None, -110.0
    c = ChipModel(src)
    c.advance(0.05)
    assert abs(c.rssi_dbm(F0 + 300_000) - -110) < 2.0
    assert abs(c.rssi_dbm(F0 + 100_000) - -65) < 2.0         # the carrier 45 dB over the floor
    with pytest.raises(ValueError):                         # pluto is sim/soapyfang/pluto.py's, not here
        open_source("pluto")


# ---- the real firmware, fed through the chip ----
unicorn = pytest.importorskip("unicorn")
from sim.soapyfang.board import SoapyUV21Board  # noqa: E402
from sim.uv21.board import build  # noqa: E402
from test_uv21_surf import REG_GPIO, REG_PA, REG_STATE, sel, tap, until  # noqa: E402


@pytest.fixture(scope="module")
def built():
    return build()


def test_surf_hears_the_cw_wake_from_iq(built):
    b = SoapyUV21Board(*built)
    b.run(until="boot done", limit=300_000_000)
    m, out = until(b, r"wake 440\.8500 (-?\d+) (-?\d+)", 20)
    assert m, out[-400:]
    level, floor = int(m.group(1)), int(m.group(2))
    assert abs(level - -78) <= 3 and abs(floor - -125) <= 4, m.group(0)
    assert b.fast_bus and b.fast_reads > 100
    for reg, v in b.tx_watch:                               # writes still go through the image's gate
        if reg == REG_STATE:
            assert not (v & 0x00F0 and v & 0x000E), hex(v)
        elif reg == REG_PA:
            assert v == 0x0022, hex(v)
        elif reg == REG_GPIO:
            assert not v & 0x001C, hex(v)


def test_surf_finds_the_peaks_in_a_recording(built, tmp_path):
    band = [Station("a", 439_700_000, -80), Station("b", 440_100_000, -85),
            Station("c", 439_200_000, -90)]
    src = SyntheticSource(band, rate=2_400_000, center_hz=439_650_000)
    base = str(tmp_path / "three-carriers")
    write_sigmf(base, src.read(int(0.5 * src.rate)), src.rate, 439_650_000)
    b = SoapyUV21Board(*built, source=SigmfSource(base + ".sigmf-meta"))
    b.run(until="boot done", limit=300_000_000)
    until(b, r"$^", 6)
    assert sel(b) == 439_500_000
    tap(b, "SIDE1", 1.0)
    assert sel(b) == 439_700_000
    tap(b, "SIDE1", 1.0)
    assert sel(b) == 440_100_000
    tap(b, "BAND", 1.0)
    assert sel(b) == 439_700_000
    tap(b, "BAND", 1.0)
    assert sel(b) == 439_200_000
