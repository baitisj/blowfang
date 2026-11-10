"""Turn a sotto 1200 .sot (sotto_enc -1200: 6-byte superframes, 40 ms each) into
blowfang_message.h, the message the UV-380's PAGER mode sends.

    python firmware/uv380-openrtx/make_message.py MSG.sot firmware/uv380-openrtx/blowfang_message.h "what it says"

SPDX-License-Identifier: MIT"""
import sys


def main(src, dst, note):
    data = open(src, "rb").read()
    if len(data) % 6:
        sys.exit("a .sot file is a whole number of 6-byte frames")
    n = len(data) // 6
    rows = [", ".join(f"0x{b:02X}" for b in data[i:i + 12]) for i in range(0, len(data), 12)]
    body = ",\n    ".join(rows)
    with open(dst, "w") as f:
        f.write(f"""/* The message the UV-380's blowfang PAGER mode sends: sotto 1200, {n} superframes
 * ({n * 0.04:.1f} s of speech). {note}
 * Made by firmware/uv380-openrtx/make_message.py. SPDX-License-Identifier: MIT */
#ifndef BLOWFANG_MESSAGE_H
#define BLOWFANG_MESSAGE_H

#include <stdint.h>

#define BLOWFANG_MESSAGE_FRAMES {n}u

static const uint8_t blowfang_message[{len(data)}] = {{
    {body},
}};

#endif
""")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
