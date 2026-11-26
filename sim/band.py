"""The model band the emulated radios hear when no SDR feeds them: Stations (a carrier, voice
overs, a CW ident on a period) and the made-up 70 cm activity of default_band(). Shared by the
UV-21 board (sim/uv21/board.py), the RT-950 board (sim/rt950/board.py) and SoapyFang's synthetic
source (sim/soapyfang/sources.py).
SPDX-License-Identifier: MIT"""
from __future__ import annotations

from dataclasses import dataclass, field

MORSE = {"A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.", "G": "--.",
         "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..", "M": "--", "N": "-.",
         "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.", "S": "...", "T": "-", "U": "..-",
         "V": "...-", "W": ".--", "X": "-..-", "Y": "-.--", "Z": "--..", "0": "-----",
         "1": ".----", "2": "..---", "3": "...--", "4": "....-", "5": ".....", "6": "-....",
         "7": "--...", "8": "---..", "9": "----.", "/": "-..-."}


def morse_keying(text, wpm=20):
    """[(on, seconds), ...] for text in CW at wpm (a dot is 1.2 / wpm s)"""
    dot = 1.2 / wpm
    out = []
    for w, word in enumerate(text.upper().split()):
        if w:
            out.append((0, 7 * dot))
        for c, ch in enumerate(word):
            if c:
                out.append((0, 3 * dot))
            for e, el in enumerate(MORSE.get(ch, "")):
                if e:
                    out.append((0, dot))
                out.append((1, dot if el == "." else 3 * dot))
    return out


@dataclass
class Station:
    """one signal of the model band"""
    name: str
    hz: int
    dbm: float
    kind: str = "carrier"          # carrier | voice (overs) | cw (an ident every period_s) | off
    width_hz: int = 6000           # half the occupied width: full level inside it
    on_s: float = 6.0              # voice: an over this long ...
    period_s: float = 20.0         # ... every period (voice), or an ident every period (cw)
    phase_s: float = 0.0
    text: str = "AG7EW"            # cw: what it sends
    enabled: bool = True
    _cw: list = field(default_factory=list, repr=False)

    def keyed(self, t):
        if not self.enabled or self.kind == "off":
            return False
        if self.kind == "carrier":
            return True
        u = (t + self.phase_s) % self.period_s
        if self.kind == "voice":
            return u < self.on_s
        if not self._cw:
            self._cw = morse_keying(self.text)
        for on, d in self._cw:
            if u < d:
                return bool(on)
            u -= d
        return False


def default_band():
    """made-up 70 cm activity round 440 MHz (no real station's data), and the CW wake beacon on
    440.850, the frequency the port listens on for it"""
    return [
        Station("repeater A", 441_200_000, -71, "voice", on_s=8, period_s=25, phase_s=3),
        Station("repeater B", 442_900_000, -88, "voice", on_s=5, period_s=17, phase_s=9),
        Station("simplex", 446_000_000, -95, "voice", on_s=4, period_s=30, phase_s=14),
        Station("beacon", 432_300_000, -102, "carrier", width_hz=1500),
        Station("data burst", 439_975_000, -84, "voice", on_s=0.4, period_s=1.1),
        Station("wake (CW)", 440_850_000, -78, "cw", period_s=15, text="AG7EW AG7EW"),
    ]


def two_metre_band():
    """made-up 2 m activity within SURF's default ±250 kHz of 146.52 MHz (the RT-950 base
    firmware's VFO A with blank settings; no real station's data)"""
    return [
        Station("calling", 146_520_000, -82, "voice", on_s=4, period_s=20, phase_s=6),
        Station("repeater A", 146_760_000, -70, "voice", on_s=8, period_s=25, phase_s=3),
        Station("repeater B", 146_340_000, -92, "voice", on_s=5, period_s=17, phase_s=9),
        Station("beacon", 146_600_000, -104, "carrier", width_hz=1500),
        Station("CW", 146_430_000, -86, "cw", period_s=15, text="AG7EW AG7EW"),
    ]
