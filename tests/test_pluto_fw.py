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


if __name__ == "__main__":
    unittest.main()
