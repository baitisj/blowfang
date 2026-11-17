"""A UV-380 DMR call as the Pluto captures it at 0 W (11-17 V1b/V1c): the FM ident near +0.8 kHz,
then the C6000's DMR near +7.1 kHz. tools/uv380_dmrvoice.dmr_disc follows the carrier to 0 Hz before the
channel filter, and the call decodes; the plain discriminator (an +-8 kHz channel on 0 Hz) cut the
DMR's top half off and nothing decoded. SPDX-License-Identifier: MIT"""
import os
import sys

import numpy as np
from scipy import signal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
from modem import c6phy, dmrvoice  # noqa: E402

FS = 240_000


def _capture():
    import uv380_dmrvoice as tool
    pay = [dmrvoice.pn(k) for k in range(30)]
    dmr_hz = tool.symbols_audio(tool.voice_stream_symbols(pay), 48000, 1944.0 / 5000.0) * 5000.0
    t = np.arange(48000) / 48000.0
    ident_hz = 2500.0 * np.sin(2 * np.pi * 800 * t) + 900.0
    f48 = np.concatenate([np.zeros(4800), ident_hz, dmr_hz + 7100.0, np.zeros(4800)])
    on = np.concatenate([np.zeros(4800), np.ones(len(ident_hz) + len(dmr_hz)), np.zeros(4800)])
    f = signal.resample_poly(f48, 5, 1)
    a = np.repeat(on, 5)
    iq = a * np.exp(2j * np.pi * np.cumsum(f) / FS)
    rng = np.random.default_rng(7)
    iq = iq + 0.02 * (rng.standard_normal(len(iq)) + 1j * rng.standard_normal(len(iq)))
    return iq.astype(np.complex64), pay


def test_followed_carrier_decodes_and_the_plain_discriminator_does_not():
    import uv380_dmrvoice as tool
    iq, pay = _capture()
    x = tool.dmr_disc(iq, FS) / tool.CF32_FULL_DEV
    _, vb, _ = tool.decode_voice(x, 48000)
    assert len(vb) == len(pay)
    errs = sum(int(np.sum(np.sign(s) != dmrvoice.hard(pay[k]))) for k, s in vb)
    assert errs == 0
    plain = c6phy.fm_demod(iq, FS) / tool.CF32_FULL_DEV
    _, vb2, _ = tool.decode_voice(plain, 48000)
    assert len(vb2) < len(pay) // 2


def test_a_false_sync_before_the_call_does_not_take_the_time_zero():
    """11-17 V2: one false voice sync in the noise before the call made voice_bursts' 60 ms grid
    and every real burst fell off it; the grid is now the one most syncs agree with"""
    import uv380_dmrvoice as tool
    iq, pay = _capture()
    x = tool.dmr_disc(iq, FS) / tool.CF32_FULL_DEV
    y = tool.dd.audio_symbols(x, 48000)
    pol, ev = tool.dd.decode_stream(y, thr=0.85)
    first = min(e["t"] for e in ev if e.get("kind") == "voice")
    fake = [{"kind": "voice", "t": first - 0.7311}]
    vb = tool.voice_bursts(y, pol, fake + ev)
    assert [k for k, _ in vb] == list(range(len(pay)))
