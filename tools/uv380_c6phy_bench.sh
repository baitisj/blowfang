#!/bin/sh
# The UV-380's flat FSK (docs/uv380-c6phy.md) on the hamlet bench: the UV-380 sends fsk24's test
# packets through the HR_C6000's physical layer mode, the K6 (fsk24rx_uv) and the Pluto receive.
# Bench keys: /mnt/project-files/uv380-c6phy/bench-keys.md. MIT licence.
#
#   sh tools/uv380_c6phy_bench.sh stage RUN       build, sign and check everything; no RF
#   sh tools/uv380_c6phy_bench.sh dry RUN         c6phytx dry on the UV-380 (no RF): slot clock,
#                                                 TX RAM read back
#   sh tools/uv380_c6phy_bench.sh live RUN K6LOG  the UV-380 at 0 W, PA on (power 0, zero 2), on
#                                                 440.850: 3 key-ups at 2400 baud while the K6 is
#                                                 in its 2400 settings, 2 at 1200 in its last,
#                                                 the Pluto capturing each (50 kHz up); then the
#                                                 radio's power and zero back as found, reports
#
# RUN is the run folder (~/blowfang-work/runs/uv380-c6phy-...). live waits for the K6 logger's
# "fsk24 start" line in K6LOG (the K6 module is uploaded first, over the IC-7100 at RF power 0:
# the K6's only upload path). Each key-up: AG7EW in Morse first, then 4.6 s of FSK. Nothing here
# writes to /dev/ttyUSB2 or keys the Pluto.
set -eu
CMD=${1:?stage, dry or live}
RUN=${2:?the run folder}
HERE=$(cd "$(dirname "$0")/.." && pwd)
KEY=${KEY:-$HOME/.config/sotto/k6-mod.key}
UV=${UV:-$(ls /dev/serial/by-id/*050C* 2>/dev/null | head -1)}
MOD=$HERE/firmware/uv380-openrtx/mod
P() { python3 "$@"; }
mkdir -p "$RUN"
cd "$HERE"
stamp() { date -u +%H:%M:%SZ; }

# "tune" / "power" / "zero" answer with a tune line and no "ok": a 2 s wait each, then read back
uvcmd() { P tools/uv380_i2sprobe.py cmd "$UV" "$@" --timeout 2 --log "$RUN/uv380.log" || true; }
uvmod() { P tools/uv380_i2sprobe.py mod "$UV" "$1" --key-file "$KEY" --log "$RUN/uv380.log" --run-timeout 30; }

case "$CMD" in
stage)
    P -m pytest -q tests/test_uv380_c6phy.py tests/test_k6_fsk24.py
    P -m modem.c6phy plan | tee "$RUN/plan.txt"
    for m in c6phytx c6phytx_live c6phytx_live1200; do
        P -m modem.uv380mod "$MOD/$m.c" "$RUN/$m.u3r" --key-file "$KEY"
    done
    P -m modem.k6 mod "$RUN/up.wav" c/k6/mod/fsk24rx_uv.c --key-file "$KEY" --serial "$(date +%s)" \
        --phy F --baud 1800 --payload XL --level 0.3 --api-level 2
    [ -n "$UV" ] && echo "UV-380 on $UV" || echo "no UV-380 on /dev/serial/by-id (*050C*): set UV="
    ls -l "$RUN"
    ;;
dry)
    [ -n "$UV" ] || { echo "no UV-380 port"; exit 2; }
    echo "$(stamp) dry run"
    uvmod "$MOD/c6phytx.c" | tee "$RUN/dry.txt"
    ;;
live)
    K6LOG=${3:?the K6 logger log}
    [ -n "$UV" ] || { echo "no UV-380 port"; exit 2; }
    uvcmd tune | tee "$RUN/tune-before.txt"
    PWR=$(sed -n 's/.*pwr=\([0-9]*\).*/\1/p' "$RUN/tune-before.txt" | tail -1)
    ZERO=$(sed -n 's/.*zero=\([0-9]*\).*/\1/p' "$RUN/tune-before.txt" | tail -1)
    uvcmd "power 0" "zero 2" | tee "$RUN/tune-set.txt"
    grep -q "pwr=0 .*" "$RUN/tune-set.txt" && grep -q "zero=2" "$RUN/tune-set.txt" \
        || { echo "power 0 / zero 2 not read back: nothing sent"; exit 3; }
    restore() { uvcmd "power ${PWR:-1000}" "zero ${ZERO:-1}" | tee "$RUN/tune-after.txt"; }
    trap restore EXIT
    echo "$(stamp) waiting for the K6's fsk24 start in $K6LOG"
    n=0
    until grep -q "fsk24 start" "$K6LOG" 2>/dev/null; do
        n=$((n + 1)); [ $n -lt 300 ] || { echo "no fsk24 start in 300 s"; exit 4; }
        sleep 1
    done
    until grep -q "say s 0 " "$K6LOG"; do sleep 1; done
    echo "$(stamp) K6 in setting 0 (D/2400)"
    sleep 5
    key() {   # $1 module, $2 name: the Pluto captures 14 s from 2 s before the module
        P tools/pluto_bench.py rx "$RUN/$2.cf32" --hz 440900000 --seconds 16 > "$RUN/$2.rx.txt" 2>&1 &
        sleep 2
        echo "$(stamp) $2"
        uvmod "$MOD/$1.c" | tee "$RUN/$2.txt"
        wait
        P -m modem.c6phy decode "$RUN/$2.cf32" --baud "$3" | tee "$RUN/$2.decode.txt"
    }
    key c6phytx_live k2400a 2400
    sleep 10
    key c6phytx_live k2400b 2400
    sleep 10
    key c6phytx_live k2400c 2400
    echo "$(stamp) waiting for the K6's setting 3 (D/1200)"
    until grep -q "say s 3 " "$K6LOG"; do sleep 1; done
    sleep 3
    key c6phytx_live1200 k1200a 1200
    sleep 10
    key c6phytx_live1200 k1200b 1200
    until grep -q "fsk24 end" "$K6LOG"; do sleep 2; done
    P -m modem.fsk24 report "$K6LOG" | tee "$RUN/k6-report.txt"
    ;;
*)
    echo "stage, dry or live"; exit 2 ;;
esac
