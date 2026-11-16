"""Two-chip SURF on the RT-950 Pro (radios/rt950/src/surf2.c) against the two modelled BK4829s of
radios/rt950/sim/rt950_sim.c: chip 1 sweeps all the time and never comes inside the guard round
chip 0, chip 0 listens with its audio open while the sweep goes on, carets come for stations
(a pulsing one too) while chip 0 plays another, the side keys jump and refine with chip 1 reading
the lens first, PTT stops the sweep and turns chip 1 off, monitor, hold, the demodulator on chip
0 only, the per-chip offset, and EXIT leaving both chips as the firmware had them. The guard is
shown to be what keeps a sweeping chip's desense out of the listener (a build with none opens
the squelch on an empty band). Needs make -C radios/rt950. SPDX-License-Identifier: GPL-3.0-or-later"""
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RT = os.path.join(ROOT, "radios", "rt950")
SIM = os.path.join(RT, "build", "rt950_sim")
NOGUARD = os.path.join(RT, "build", "rt950_sim_noguard")
pytestmark = pytest.mark.skipif(not os.path.exists(SIM), reason="needs make -C radios/rt950")

START = 440_850_000
GUARD = 25_000


def sim(*args, exe=SIM):
    """run the sim; returns (lines, summary dict, frames). A frame is (header dict, bins), each
    bin (hz, lvl, caret, flags)"""
    out = subprocess.run([exe, *map(str, args)], check=True, capture_output=True, text=True).stdout
    lines = out.splitlines()
    summary, frames = {}, []
    for ln in lines:
        w = ln.split()
        if w[0] == "view":
            head, msg = ln.split(" msg=", 1)
            hdr = dict(kv.split("=", 1) for kv in head.split()[1:])
            hdr["msg"] = msg
            frames.append((hdr, []))
        elif w[0] == "bin":
            frames[-1][1].append(tuple(int(x) for x in w[2:]))
        elif len(w) == 2 and not ln.startswith(("t=", "key ", "audio ", "ptt t=")):
            summary[w[0]] = w[1]
    return lines, summary, frames


def audio(lines):
    """[(ms, on, hz)] for each change of the speaker"""
    out = []
    for ln in lines:
        m = re.match(r"audio t=(\d+) (on|off) f=(\d+)", ln)
        if m:
            out.append((int(m.group(1)), m.group(2) == "on", int(m.group(3))))
    return out


def said(lines):
    return [ln.split(" say ", 1)[1] for ln in lines if " say " in ln]


def bin_near(bins, f):
    return min(range(len(bins)), key=lambda i: abs(bins[i][0] - f) if bins[i][0] else 1 << 40)


def test_sweeper_stays_out_of_the_guard_and_only_the_listener_plays():
    lines, s, _ = sim("--end", 10000, "--sig", f"{START}:-80", "--sig", "441200000:-70")
    assert int(s["guard_min"]) >= GUARD
    assert int(s["tunes1"]) > 1000               # chip 1 sweeps all the time
    assert int(s["tunes0"]) < 20                 # chip 0 tunes only for the start, the floor and EXIT
    assert s["c1_audio"] == "0"
    assert said(lines)[1] == "surf2 70cm 440.850 25k" and said(lines)[-1].startswith("end sw=")


def test_listener_plays_without_a_break_while_the_sweep_goes_on():
    # the K6 mutes for every sweep; here chip 0's audio opens once and stays open to EXIT
    lines, s, fr = sim("--end", 12000, "--sig", f"{START}:-80", "--dump", 2000, "--dump", 11000)
    a = audio(lines)
    assert a[0][1] and a[0][0] < 400 and a[0][2] == START
    assert a[1:] == [(a[1][0], False, START)] and a[1][0] >= 12000
    assert int(fr[1][0]["sweeps"]) - int(fr[0][0]["sweeps"]) >= 15
    assert fr[1][0]["audio"] == "1"


def test_carets_come_while_listening_even_for_a_pulser():
    # 441.203 steady and a 0.3 s in 1 s pulser at 440.500 (the 464.495 kind), while 440.850 plays
    lines, s, fr = sim("--end", 9000, "--sig", f"{START}:-80", "--sig", "441203000:-70",
                       "--sig", "440500000:-85:300:1000", "--dump", 8800)
    hdr, bins = fr[0]
    assert hdr["audio"] == "1" and len(audio(lines)) == 2
    for f in (441_203_000, 440_500_000):
        i = bin_near(bins, f)
        assert bins[i][2] > 0, f"no caret near {f}: {bins[i]}"
    c = bin_near(bins, START)
    assert bins[c][2] >= 64                      # chip 0's own station has its caret
    assert bins[c][3] == 1                       # and its bin is chip 0's level
    quiet = [b for b in bins if b[0] and b[3] == 0 and min(abs(b[0] - f) for f in (441_203_000, 440_500_000)) > 30000]
    assert all(b[2] == 0 for b in quiet)


def test_guard_bins_are_the_listeners_or_held():
    _, _, fr = sim("--end", 4000, "--sig", f"{START}:-80", "--dump", 3800)
    for hz, lvl, caret, fl in fr[0][1]:
        d = abs(hz - START)
        if d < GUARD:
            assert fl == (1 if d <= 12_500 else 2)
            assert lvl > 0
        else:
            assert fl == 0


def test_side_key_jumps_to_a_coarse_caret_and_refines_before_the_listener_goes():
    lines, s, fr = sim("--end", 12000, "--sig", "441203000:-70", "--sig", "440200000:-75",
                       "--key", "6000:A", "--key", "8000:M", "--key", "9000:A", "--dump", 9100)
    a = audio(lines)
    assert a[0] == (a[0][0], True, 441_200_000) and 6000 < a[0][0] < 6100
    assert any(x.startswith("sel 441.2000 -70 ") for x in said(lines))
    assert fr[0][0]["msg"] == "no peak"
    assert int(s["guard_min"]) >= GUARD


def test_side_key_down_and_no_peak():
    lines, _, fr = sim("--end", 9000, "--sig", "440200000:-75", "--key", "5000:B", "--key", "7000:B",
                       "--dump", 7050)
    a = audio(lines)
    assert a[0][2] == 440_200_000
    assert fr[0][0]["cen"] == "440200000" and fr[0][0]["msg"] == "no peak"


def test_the_guard_keeps_a_sweeping_chips_desense_out():
    # the model: chip 0 hears -90 dBm more while chip 1 is tuned within 15 kHz of it. An empty band
    lines, s, _ = sim("--end", 10000)
    assert audio(lines) == [] and int(s["guard_min"]) >= GUARD
    if os.path.exists(NOGUARD):
        lines, s, _ = sim("--end", 10000, exe=NOGUARD)
        assert audio(lines) and audio(lines)[0][1]        # the squelch opens on nothing
        assert int(s["guard_min"]) < 15000


def test_ptt_stops_the_sweep_and_turns_chip_1_off():
    lines, s, fr = sim("--end", 9000, "--sig", f"{START}:-80", "--key", "3000:P", "--key", "5000:p",
                       "--dump", 4000)
    assert fr[0][0]["ptt"] == "1" and fr[0][0]["audio"] == "0"
    assert s["ptt_traffic"] == "0"
    assert "ptt t=5000 c1hz=0 spk=0" in lines
    a = audio(lines)
    assert [x[1] for x in a] == [True, False, True, False]
    assert a[1][0] == 3000 and 5000 < a[2][0] < 5050


def test_monitor_opens_the_listener_and_the_sweep_goes_on():
    lines, s, fr = sim("--end", 8000, "--key", "2000:*", "--dump", 3000, "--dump", 7000)
    a = audio(lines)
    assert a[0][1] and 2000 <= a[0][0] < 2050
    assert fr[0][0]["mon"] == "1"
    assert int(fr[1][0]["sweeps"]) > int(fr[0][0]["sweeps"])
    assert s["c0r4f"] == "3f3b"                  # chip 0's squelch put back at EXIT


def test_hold_stops_the_sweep():
    _, _, fr = sim("--end", 9000, "--key", "3000:0", "--dump", 4000, "--dump", 8000)
    assert fr[0][0]["hold"] == "1"
    assert fr[0][0]["sweeps"] == fr[1][0]["sweeps"]


def test_dsb_is_the_listeners_only():
    lines, s, fr = sim("--end", 6000, "--key", "2000:4", "--dump", 2100)
    assert fr[0][0]["mode"] == "DSB"
    assert s["c1_demod"] == "0"
    assert s["c0r3d"] == "0000" or s["c0r3d"] == "0"   # (put back as the sim started it: 0)


def test_per_chip_offset_is_measured():
    # chip 1's floor 6 dB hotter than chip 0's: chip 0's floor is chip 1's minus 6
    lines, _, fr = sim("--end", 4000, "--offset1", 6, "--dump", 3000)
    loff = int(said(lines)[0].split()[1])
    assert -6 <= loff <= -5                      # (REG_67 counts half dB; dBm rounds down)
    assert -128 <= int(fr[0][0]["floor"]) <= -127   # chip 0 really is at -127


def test_band_key_moves_both_chips_and_measures_again():
    lines, s, fr = sim("--end", 8000, "--key", "2000:N", "--dump", 6000)
    assert fr[0][0]["band"] == "GMRS" and fr[0][0]["cen"] == "465137500"
    assert said(lines).count("loff 0") == 2
    assert int(s["guard_min"]) >= GUARD


def test_exit_leaves_the_chips_as_the_firmware_had_them():
    _, s, _ = sim("--end", 5000, "--sig", f"{START}:-80", "--key", "1000:9", "--key", "2000:4")
    assert s["c0hz"] == str(START) and s["c1hz"] == "0"
    assert s["c0r47"] == "6142" and s["spk"] == "0"


@pytest.mark.skipif(not shutil.which("clang") or not shutil.which("make"), reason="needs clang")
def test_builds_for_the_at32f403a():
    out = subprocess.run(["make", "-s", "-C", RT, "target"], check=True, capture_output=True, text=True).stdout
    text = int(out.strip().splitlines()[-1].split()[0])   # size's text column
    assert text < 16384, out
