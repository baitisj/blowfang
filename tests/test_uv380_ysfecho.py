"""The FTM echo's keeper (c/common/ysf_echo.c, firmware/uv380-openrtx/mod/c6ysftx_echo.c): a DN
call heard on simulated UV-380 tap audio is kept frame by frame as the bytes it was sent as, so
the parrot can send it back as it came (AMBE voice, DCH and FICH) with no vocoder: hard decisions,
so a symbol now and then comes back wrong (about 1 bit in 10^4 on clean audio), which the FTM's
own FEC covers.
SPDX-License-Identifier: MIT"""
import os
import subprocess

import numpy as np
import pytest

pytest.importorskip("scipy")
from modem import ysf  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..", "c", "common")
MOD = os.path.join(os.path.dirname(__file__), "..", "firmware", "uv380-openrtx", "mod")


def ftm_call(ncomms, seed):
    """an FTM-style DN call (header, comms FN 0-7..., terminator) whose comms frames carry random
    bits after the FICH, standing in for real AMBE voice: frames as bytes, preamble dropped"""
    tx = bytearray(ysf.tx_dn(ncomms))[24:]
    rng = np.random.default_rng(seed)
    for k in range(1, ncomms + 1):
        at = 120 * k + 30                        # sync 5 bytes + FICH 25 bytes, then the payload
        tx[at:120 * (k + 1)] = bytes(rng.integers(0, 256, 90, dtype=np.uint8))
    return bytes(tx)


def tap_audio(frames, rng, noise=300.0, invert=True):
    from scipy import signal
    x = ysf.modulate(bytes([ysf.PREAMBLE]) * 24 + frames, fs=48000, alpha=0.2, outer_hz=2700.0) / 2400.0 * 8000.0
    b, a = signal.butter(6, 3400.0, fs=48000)
    x = signal.lfilter(b, a, x)[::6] + rng.normal(0, noise, len(x[::6]))
    quiet = lambda: rng.normal(0, 12000, 8000)  # noqa: E731
    y = np.concatenate([quiet(), x, quiet()]) * (-1 if invert else 1)
    return np.clip(np.round(y), -32768, 32767).astype("<i2")


def bit_errors(a, b):
    assert len(a) == len(b)
    return int(np.count_nonzero(np.unpackbits(np.frombuffer(a, np.uint8) ^ np.frombuffer(b, np.uint8))))


def keep(tmp_path, audio, maxf=200):
    subprocess.run(["make", "-s", "-C", ROOT, "ysf_echo_host"], check=True)
    raw = tmp_path / "tap.raw"
    raw.write_bytes(audio.tobytes())
    out = subprocess.run([os.path.join(ROOT, "ysf_echo_host"), str(raw), "invert", str(maxf)], check=True,
                         capture_output=True, text=True).stdout.splitlines()
    calls = []
    for ln in out:
        if ln.startswith("call "):
            calls.append({"info": dict(kv.split("=", 1) for kv in ln.split()[1:]), "frames": []})
        elif calls:
            calls[-1]["frames"].append(bytes.fromhex(ln))
    return calls


@pytest.mark.parametrize("ncomms", [8, 24])
def test_a_dn_call_is_kept_as_sent(tmp_path, ncomms):
    tx = ftm_call(ncomms, seed=ncomms)
    calls = keep(tmp_path, tap_audio(tx, np.random.default_rng(ncomms)))
    assert len(calls) == 1
    c = calls[0]
    i = c["info"]
    assert (i["n"], i["hdr"], i["term"], i["voice"], i["gaps"], i["ended"], i["src"]) == \
        (str(ncomms + 2), "1", "1", str(ncomms), "0", "1", "AG7EW")
    # every bit, voice included, but for the odd symbol the 24 kHz sampling instant puts on the
    # wrong side of a threshold (about 1 in 10^4 here; the FTM's FEC is there for those)
    assert bit_errors(b"".join(c["frames"]), tx) <= len(tx) * 8 // 1000


def test_a_call_longer_than_the_room_keeps_its_start_and_its_terminator(tmp_path):
    tx = ftm_call(30, seed=5)
    calls = keep(tmp_path, tap_audio(tx, np.random.default_rng(6)), maxf=12)
    i = calls[0]["info"]
    assert (i["n"], i["dropped"], i["term"], i["ended"]) == ("12", "20", "1", "1")
    assert bit_errors(b"".join(calls[0]["frames"]), tx[:11 * 120] + tx[-120:]) <= 12


def test_noise_keeps_nothing(tmp_path):
    rng = np.random.default_rng(7)
    noise = np.clip(np.round(rng.normal(0, 12000, 8000 * 30)), -32768, 32767).astype("<i2")
    assert keep(tmp_path, noise) == []


def test_echo_module_builds_and_holds_two_seconds_of_a_call(tmp_path):
    """c6ysftx_echo.c (the runner's uv380.ysf echo): the image (64-byte header, the code, 16-byte
    tag) plus its state and at least 20 frames (2 s, header and terminator included) within the
    16 KB arena, checked by the compiler on sizeof(echo_state); it keys only through tx_tone (the
    ident first) and never says a frame's bytes"""
    import re
    from modem import uv380mod
    src = os.path.join(MOD, "c6ysftx_echo.c")
    code, entry = uv380mod.build(src)
    assert entry == 0
    img = (64 + (len(code) + 7) // 8 * 8 + 16 + 7) // 8 * 8
    probe = tmp_path / "probe.c"
    probe.write_text(f'#include "{os.path.abspath(src)}"\n'
                     f"_Static_assert({img} + sizeof(echo_state) + 20 * YE_FRAME <= 16384, \"room\");\n")
    r = subprocess.run(["clang", *uv380mod.CFLAGS, "-I" + MOD, "-fsyntax-only", str(probe)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    body = open(src).read()
    assert "c6k_key(api" in body and "tx_mic" not in body
    says = re.findall(r'api->(?:say|fmt)\([^;]*;', body, re.S)
    assert says and not [s for s in says if "frames[" in s or "c6_hex" in s]
