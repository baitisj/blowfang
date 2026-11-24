# UV-21 workbench

The UV-21 Pro port image (`radios/uv21/fw`) running in the emulated AT32F421 board, in a browser
page: the radio's screen, a faceplate you can click, the serial console, and a made-up band of
stations you can switch on and off or turn up and down. It is for test-driving SURF and the
other screens without a radio.

    python3 -m sim.uv21                     # then open http://127.0.0.1:5057/
    python3 -m sim.uv21 --host 0.0.0.0      # reachable from the LAN (there is no login)

It builds the image first (`make -C radios/uv21 port`, so cargo and clang as for the tests) and
needs the `unicorn` Python package, as `tests/test_uv21_port.py` does. Nothing else: the server is
the standard library's.

- Click a key to tap it, shift-click for a long press, hold a button past 0.4 s to hold the key.
  The keyboard works too: arrows, Enter (MENU), Esc (EXIT), PageUp / PageDown (top / bottom side
  key), b (middle side key), v (VFO/MR), a (A/B), digits, `*`, `#`.
- The band (`board.py`, `default_band`) is invented: two repeaters, a simplex voice, a beacon
  carrier, a data burst and a CW beacon on 440.850 MHz that sends a test ident every 15 s, so
  the CW wake fires. None of it is a measurement or a recording.
- RSSI comes from the stations' levels through a 6 dB/kHz skirt over a -125 dBm floor, on the
  K6's scale (raw = (dBm + 174) * 2).
- The SPI flash is blank and synthetic: no radio's calibration or settings are read or written.
- The emulator runs at about a fifth of real time, so SURF sweeps slowly; the page shows the
  speed. Every write to a transmit register is logged, and the image is receive only.

`board.py` is also what `tests/test_uv21_surf.py` drives.
