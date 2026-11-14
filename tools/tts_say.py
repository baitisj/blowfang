#!/usr/bin/env python3
"""Speak a phrase into a wav with espeak-ng, no system install (the drive echo's
"Received FSK" / "Received range", Jeff 11-14).

    python -m venv ~/blowfang-work/tts
    ~/blowfang-work/tts/bin/pip install espeakng-loader
    ~/blowfang-work/tts/bin/python tools/tts_say.py "Received F S K." out.wav [wpm]

Writes 16-bit mono at espeak-ng's own rate (22050 Hz). SPDX-License-Identifier: MIT
"""
import ctypes
import sys
import wave

import espeakng_loader


def say(text, path, wpm=140):
    lib = ctypes.CDLL(espeakng_loader.get_library_path())
    rate = lib.espeak_Initialize(2, 0, espeakng_loader.get_data_path().encode(), 0)  # synchronous
    if rate <= 0:
        raise RuntimeError("espeak_Initialize failed")
    pcm = bytearray()
    cbtype = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.c_short), ctypes.c_int,
                              ctypes.c_void_p)

    def cb(wav, n, events):
        if wav and n > 0:
            pcm.extend(ctypes.string_at(wav, n * 2))
        return 0

    keep = cbtype(cb)
    lib.espeak_SetSynthCallback(keep)
    lib.espeak_SetVoiceByName(b"en-us")
    lib.espeak_SetParameter(1, int(wpm), 0)  # espeakRATE
    raw = text.encode()
    lib.espeak_Synth(raw, len(raw) + 1, 0, 0, 0, 1, None, None)  # espeakCHARS_UTF8
    lib.espeak_Synchronize()
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(pcm))
    return rate, len(pcm) // 2


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    r, n = say(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 140)
    print(f"{sys.argv[2]}: {n / r:.2f} s at {r} Hz")
