"""tools/pluto_fw.py: the DFU suffix matches ADI's, and only pinned images pass. SPDX-License-Identifier: MIT"""
import os
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import pluto_fw  # noqa: E402


class PlutoFw(unittest.TestCase):
    def test_dfu_suffix(self):
        with tempfile.TemporaryDirectory() as td:
            itb = os.path.join(td, "a.itb")
            open(itb, "wb").write(b"\xd0\x0d\xfe\xed" + bytes(60))
            d = open(pluto_fw.dfu_file(itb, os.path.join(td, "a.dfu")), "rb").read()
            self.assertEqual(len(d), 64 + 16)
            bcd, pid, vid, dfu, sig, n = struct.unpack("<HHHH3sB", d[64:76])
            self.assertEqual((bcd, pid, vid, dfu, sig, n), (0xFFFF, 0xB673, 0x0456, 0x0100, b"UFD", 16))
            self.assertEqual(struct.unpack("<I", d[76:])[0], zlib.crc32(d[:76]) ^ 0xFFFFFFFF)

    def test_unpinned_refused(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "x.itb")
            open(p, "wb").write(b"not a pinned image")
            with self.assertRaises(SystemExit):
                pluto_fw.pinned_fit(p, ("stock", "reserved"))
            with self.assertRaises(SystemExit):
                pluto_fw.pinned(p, "bit")

    def test_families(self):
        for h, (name, use) in pluto_fw.FITS.items():
            self.assertIn(name, pluto_fw.FAMILY)
            self.assertIn(use, ("stock", "reserved"))
            self.assertEqual(len(h), 64)

    def test_psd_db(self):
        self.assertEqual(pluto_fw.psd_db(6251.4 + 256, 6251.4), 3.0)
        self.assertEqual(pluto_fw.psd_db(6251.4, 6251.4), 0.0)

    def test_riq_parse_and_second(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy")
        import socket
        import threading
        import time
        rate, f0 = 100_000, 1234.5
        n = np.arange(rate)
        x = 0.25 * np.exp(2j * np.pi * f0 * n / rate)
        iq = np.empty(2 * rate, dtype="<i2")
        iq[0::2], iq[1::2] = np.round(x.real * 32767), np.round(x.imag * 32767)
        pay = iq.tobytes()
        msgs = []
        for i, off in enumerate(range(0, len(pay), 10_000 * 4)):
            chunk = pay[off:off + 40_000]
            msgs.append(pluto_fw.RIQ_HDR.pack(b"RIQ1", 1, 64, 1, 0, 1, 50, i, off // 4, rate, 162_550_000,
                                              len(chunk), 1, 16, bytes(4)) + chunk)
        a, b = socket.socketpair()
        th = threading.Thread(target=lambda: (a.sendall(b"".join(msgs)), a.close()))
        th.start()
        got = list(pluto_fw.riq_messages(b, time.time() + 5))
        th.join()
        b.close()
        self.assertEqual(len(got), len(msgs))
        self.assertEqual(got[3][0][6], 50)
        data = np.concatenate([np.frombuffer(m[64:], dtype="<i2") for _, m, _ in got]).astype(np.float64)
        r = pluto_fw.riq_second(data, rate)
        self.assertAlmostEqual(r["peak_hz"], f0, delta=0.2)
        self.assertAlmostEqual(r["centroid_hz"], f0, delta=0.2)
        self.assertAlmostEqual(r["dbfs"], 20 * np.log10(0.25), delta=0.1)

    def test_producer_pinned(self):
        self.assertEqual(len(pluto_fw.EARSHOT["producer"]), 64)


if __name__ == "__main__":
    unittest.main()
