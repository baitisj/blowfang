# SoapyFang

Real or recorded I/Q behind an emulated BK48xx-family RF chip (the FD6818 / BK4829 of the UV-K6
and the UV-21 Pro), so emulated radio firmware hears real signals. Today it drives the UV-21 Pro
port image on the board of `sim/uv21/board.py`, in the same browser workbench.

    python3 -m sim.soapyfang                                    # the synthetic band (no SDR)
    python3 -m sim.soapyfang --source sigmf:PATH.sigmf-meta     # a recording
    python3 -m sim.soapyfang --source soapy:driver=rtlsdr --gain 30
    python3 -m sim.soapyfang --source soapy:driver=plutosdr,uri=ip:192.168.2.1 --gain 40
    python3 -m sim.soapyfang --source pluto --gain 50          # the Pluto over libiio (no SoapySDR)

Then open http://127.0.0.1:5058/. The SoapyFang panel (bottom right) shows the source, the window
and what the chip reports; tick Listen for the tuned channel's audio (Monitor ignores the noise
squelch).

## Parts

| File | What it does |
| --- | --- |
| `sources.py` | `IqSource`: `SyntheticSource` (the Station list as FM I/Q over a noise floor), `SigmfSource` (cf32_le, ci16_le, ci8), `SoapySource` (any SoapySDR receiver), `PlutoIioSource` (the Pluto through `iio_readdev`, set up by tools/pluto_bench.py, which `pluto.py`'s `open_pluto` brings in: hamlet's way), `open_source()` |
| `chip.py` | `ChipModel`: REG_67 RSSI from FFT bins in the channel, REG_65 noise, REG_63 glitch, REG_0C squelch bit, 8 kHz speaker audio |
| `board.py` | `SoapyUV21Board`: `UV21Board` with those registers from the chip, and a fast register-read path |
| `workbench.py`, `panel.html` | the UV-21 workbench with the SoapyFang panel and audio |

## Rules it keeps

- Receive only. `SoapySource` never opens a transmit stream; the UV-21 image is receive-only too.
- A SoapySDR receiver's LO sits a quarter of the sample rate above the frequency asked for, so the
  LO leak and the DC spike stay off the channel being listened to. A Pluto's LO is parked at
  2.4 GHz on close (docs/pluto-bench.md).
- On hamlet the Pluto belongs to the bench runner: a live Pluto session is a runner job, not a
  hand-started process. An RTL-SDR on another computer needs no runner.

## On hamlet, with the Pluto

A bench runner job with one `sim.soapyfang` step (docs/bench-runner.md). Hamlet has no thumbv7em
Rust target, so the job carries the image built elsewhere: `radios/uv21/build/port.bin` and the
symbol listing `llvm-nm radios/uv21/build/port.elf > port.syms`.

```json
{"id": "soapyfang-live-1", "who": "soapyfang", "freq_mhz": "bench", "minutes": 35,
 "steps": [{"do": "sim.soapyfang",
            "args": ["--minutes", "30", "--gain", "50", "--image", "job:port.bin", "--syms", "job:port.syms"]}]}
```

The page is on hamlet's 127.0.0.1:5058 only; from another computer, `ssh -L 5058:127.0.0.1:5058
hamlet` and open http://127.0.0.1:5058/. The step ends itself after `--minutes` (55 at most) and
parks the Pluto's LO. It needs `python-unicorn` and libiio's command-line tools on hamlet.
Without `--dbm-offset` the Pluto's levels are set so the window's median floor reads -122 dBm.
- Recordings: generated signals only in Git (`*.sigmf-*` is ignored); off-air captures stay out.

## What is modelled, and what isn't

The chip's own filters, AGC and squelch maths are not published, so the registers are behavioural:
RSSI is the power inside the 25 kHz channel, noise is the discriminator's energy above 5 kHz on a
0-127 scale, glitch counts clicks per 10 ms. RSSI uses the board's scale, raw = (dBm + 174) x 2
(the K6 and SURF convention; the UV-21's own offset is unmeasured). An SDR's levels need
`--dbm-offset` from a known signal. The next step is to fit all of this against the real chip:
the Pluto sends a known level, the K6 reports its registers (or streams its GP0 IF), and the same
I/Q goes through this model.

Speed: with the fast read path, SURF runs at about half of real time on one 2.1 GHz core (the
plain board runs at about a fifth). Register writes still go bit by bit through the image's
receive-only gate.
