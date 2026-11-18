"""c6dvtx's encode start and payload ports after V1 (11-17: 0x22 = 0x80 over V_SPI put no voice
bursts on air): 0x22 = 0x86, TX_VIA 0 V_SPI, 1 U_SPI page 3, 2 by turns a superframe each, and
the test pattern call uses 2. SPDX-License-Identifier: MIT"""
import os

MOD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "firmware", "uv380-openrtx", "mod")


def test_dvtx_encode_start_and_ports():
    src = open(os.path.join(MOD, "c6dvtx.c")).read()
    assert "#ifndef TX_ENC\n#define TX_ENC 0x86u" in src
    assert "#ifndef TX_VIA\n#define TX_VIA 0\n" in src
    assert "c6_write(api, C6_SOUND, 0x00, pay, DV_BYTES);" in src
    assert "(TX_VIA == 2 && (k / 6u) % 2u == 1u)" in src
    pn = open(os.path.join(MOD, "c6dvtx_pn.c")).read()
    assert "#define TX_VIA 2" in pn
    for f in ("c6dvtx_live.c", "c6dvtx_msg.c"):
        assert "TX_VIA" not in open(os.path.join(MOD, f)).read()
