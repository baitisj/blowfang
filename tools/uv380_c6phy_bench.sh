#!/bin/sh
# The UV-380's flat FSK (docs/uv380-c6phy.md) on the hamlet bench: the UV-380 sends fsk24's test
# packets through the HR_C6000's physical layer mode, the K6 (fsk24rx_uv) and the Pluto receive.
# Bench keys: /mnt/project-files/uv380-c6phy/bench-keys.md. MIT licence.
#
#   sh tools/uv380_c6phy_bench.sh stage RUN       build, sign and check everything; no RF
#   sh tools/uv380_c6phy_bench.sh dry RUN         c6phytx dry on the UV-380 (no RF): slot clock,
#                                                 TX RAM read back
#   sh tools/uv380_c6phy_bench.sh live RUN K6LOG  the UV-380 at 0 W, PA on (power 0, zero 2), its
#                                                 own receiver off (listen 0), on
#                                                 440.850 (freq 440850 on newer images), after the K6's module upload from
#                                                 the Pluto: 3 key-ups at 2400 baud while the K6 is
#                                                 in its 2400 settings, 2 at 1200 in its last,
#                                                 the Pluto capturing each (50 kHz up); then the
#                                                 radio's power and zero back as found, reports
#                                                 (the modules' defaults since run 3: the idle
#                                                 half, TX_HALF 0; trim lowered by FSK_OFF)
#
#   sh tools/uv380_c6phy_bench.sh live2 RUN K6LOG  as live, with run 2's fixes (11-16): the next
#                                                 slot into the idle TX RAM half only (TX_WRITE 1),
#                                                 4 key-ups at 2400 alternating the half phase
#                                                 (TX_HALF 0, 1, 0, 1), 2 at 1200 with the phase
#                                                 that read more; the transmit trim lowered by the
#                                                 FSK's measured carrier offset (FSK_OFF, Hz,
#                                                 default 6680; re-trimmed after the first key-up
#                                                 if the Pluto still reads over 300 Hz), restored
#
# RUN is the run folder (~/blowfang-work/runs/uv380-c6phy-...). live sends the K6 its module
# (fsk24rx_uv) from the Pluto, AG7EW in MCW first and last (up to 3 sends), waits for the K6
# logger's "fsk24 start" line in K6LOG, then keys the UV-380. No IC-7100. Each UV-380 key-up:
# AG7EW in Morse first, then 4.6 s of FSK. Nothing here writes to /dev/ttyUSB2.
set -eu
CMD=${1:?stage, dry, live or live2}
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
# live2's modules: c6phytx on air, one TX RAM half a slot edge, both phases, both rates
wrappers() {
    for b in 2400 1200; do
        for h in 0 1; do
            seq=0; [ $b = 1200 ] && seq=100
            printf '%s\n' "#include \"ortx_mod.h\"" "#define UV_MODULE_LEVEL 4" "#define TX_DRY 0" \
                "#define TX_WRITE 1u" "#define TX_HALF ${h}u" "#define TX_BAUD ${b}u" "#define TX_SEQ ${seq}u" \
                "#include \"$MOD/c6phytx.c\"" > "$RUN/c6phytx_${b}h$h.c"
        done
    done
}
uvmod() { P tools/uv380_i2sprobe.py mod "$UV" "$1" --key-file "$KEY" --log "$RUN/uv380.log" --run-timeout 30; }

case "$CMD" in
stage)
    P -m pytest -q tests/test_uv380_c6phy.py tests/test_k6_fsk24.py
    P -m modem.c6phy plan | tee "$RUN/plan.txt"
    for m in c6phytx c6phytx_live c6phytx_live1200; do
        P -m modem.uv380mod "$MOD/$m.c" "$RUN/$m.u3r" --key-file "$KEY"
    done
    wrappers
    for m in c6phytx_2400h0 c6phytx_2400h1 c6phytx_1200h0 c6phytx_1200h1; do
        P -m modem.uv380mod "$RUN/$m.c" "$RUN/$m.u3r" --key-file "$KEY"
    done
    # level 0.5: the fsk24 ladder's test packets' level, sent from the Pluto as it sent them
    P -m modem.k6 mod "$RUN/up5.wav" c/k6/mod/fsk24rx_uv.c --key-file "$KEY" --serial "$(date +%s)" \
        --phy F --baud 1800 --payload XL --level 0.5 --api-level 2
    [ -n "$UV" ] && echo "UV-380 on $UV" || echo "no UV-380 on /dev/serial/by-id (*050C*): set UV="
    ls -l "$RUN"
    ;;
dry)
    [ -n "$UV" ] || { echo "no UV-380 port"; exit 2; }
    echo "$(stamp) dry run"
    uvmod "$MOD/c6phytx.c" | tee "$RUN/dry.txt"
    ;;
live|live2)
    K6LOG=${3:?the K6 logger log}
    [ -n "$UV" ] || { echo "no UV-380 port"; exit 2; }
    uvcmd tune | tee "$RUN/tune-before.txt"
    PWR=$(sed -n 's/.*pwr=\([0-9]*\).*/\1/p' "$RUN/tune-before.txt" | tail -1)
    ZERO=$(sed -n 's/.*zero=\([0-9]*\).*/\1/p' "$RUN/tune-before.txt" | tail -1)
    LISTEN=$(uvcmd listen | sed -n 's/.*listen on=\([01]\).*/\1/p' | tail -1)
    # the radio's own receiver off: it heard the IC-7100 and refused sends (11-16 21:5xZ)
    # images since the 11-17 channel plan boot on 441.850 (the UV-380's channel) and read freq=
    # back: these sends are for the K6, so they go on its 440.850, and the radio goes back to
    # 441.850 afterwards. An older image has no freq= and only sends on 440.850.
    FREQ=
    grep -q "freq=" "$RUN/tune-before.txt" && FREQ="freq 440850"
    uvcmd "listen 0" "power 0" "zero 2" $FREQ | tee "$RUN/tune-set.txt"
    grep -q "pwr=0 .*" "$RUN/tune-set.txt" && grep -q "zero=2" "$RUN/tune-set.txt" && grep -q "listen on=0" "$RUN/tune-set.txt" \
        || { echo "power 0 / zero 2 / listen 0 not read back: nothing sent"; exit 3; }
    [ -z "$FREQ" ] || grep -q "freq=440850" "$RUN/tune-set.txt" \
        || { echo "freq 440850 not read back: nothing sent"; exit 3; }
    TRIM=$(sed -n 's/.*trim=\(-*[0-9]*\).*/\1/p' "$RUN/tune-before.txt" | tail -1)
    restore() {
        BACK=
        [ -z "$FREQ" ] || BACK="freq 441850"
        uvcmd "power ${PWR:-1000}" "zero ${ZERO:-1}" "listen ${LISTEN:-1}" "trim ${TRIM:-5745}" $BACK \
            | tee "$RUN/tune-after.txt"
    }
    trap restore EXIT
    # the raw mode's FSK sits ~6.7 kHz above the FM (run 2); run 3 trimmed it to +39 Hz
    [ -n "$TRIM" ] || { echo "no trim in the tune line: nothing sent"; exit 3; }
    T2=$((TRIM - ${FSK_OFF:-6680}))
    uvcmd "trim $T2" | tee "$RUN/tune-trim.txt"
    grep -q "trim=$T2 " "$RUN/tune-trim.txt" || { echo "trim $T2 not read back: nothing sent"; exit 3; }
    [ "$CMD" = live2 ] && wrappers
    # the K6's module from the Pluto (Jeff 11-16: the Pluto, not the IC-7100): F/1800 XL, 750 us
    # pre-emphasis, 3.7 kHz full scale, gain -52 (run 2: -40 overloads the K6 at the bench's
    # spacing and it hears nothing); up to UPLOADS sends (6): the K6 keeps a partial upload, and
    # at -52 about 1 chunk in 4 gets through a send (run 3: 0 -> 10/18 in 3); re-run without
    # re-staging to resume, since stage makes a new transfer
    try=0
    while [ $try -lt "${UPLOADS:-6}" ]; do
        try=$((try + 1))
        grep -q "fsk24 start" "$K6LOG" 2>/dev/null && break
        echo "$(stamp) K6 upload $try from the Pluto"
        P tools/pluto_bench.py send "$RUN/up5.wav" --preemph --dev 3700 --gain -52 | tee -a "$RUN/k6up.txt"
        n=0
        until grep -q "fsk24 start" "$K6LOG" 2>/dev/null || [ $n -ge 8 ]; do n=$((n + 1)); sleep 1; done
    done
    grep -q "fsk24 start" "$K6LOG" || { echo "the K6 took no module after $try Pluto sends: nothing keyed"; exit 4; }
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
    key2() {  # as key, $1 a wrapper's path without .c; the errors report too
        P tools/pluto_bench.py rx "$RUN/$2.cf32" --hz 440900000 --seconds 16 > "$RUN/$2.rx.txt" 2>&1 &
        sleep 2
        echo "$(stamp) $2"
        uvmod "$1.c" | tee "$RUN/$2.txt"
        wait
        P -m modem.c6phy decode "$RUN/$2.cf32" --baud "$3" | tee "$RUN/$2.decode.txt"
        P -m modem.c6phy errors "$RUN/$2.cf32" --baud "$3" > "$RUN/$2.errors.txt" 2>&1 || true
        head -3 "$RUN/$2.errors.txt"
    }
    if [ "$CMD" = live ]; then
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
    else
        crc() { n=$(sed -n 's/.* \([0-9]*\) CRC right$/\1/p' "$RUN/$1.decode.txt" | head -1); echo "${n:-0}"; }
        key2 "$RUN/c6phytx_2400h0" k2400h0a 2400
        OFF=$(sed -n 's/^fsk_carrier_hz=\([-+0-9]*\).*/\1/p' "$RUN/k2400h0a.decode.txt" | head -1)
        if [ -n "$OFF" ] && { [ "$OFF" -gt 300 ] || [ "$OFF" -lt -300 ]; }; then
            T2=$((T2 - OFF))
            echo "$(stamp) still $OFF Hz off: trim $T2"
            uvcmd "trim $T2" | tee -a "$RUN/tune-trim.txt"
        fi
        sleep 6
        key2 "$RUN/c6phytx_2400h1" k2400h1a 2400
        sleep 6
        key2 "$RUN/c6phytx_2400h0" k2400h0b 2400
        sleep 6
        key2 "$RUN/c6phytx_2400h1" k2400h1b 2400
        H0=$(( $(crc k2400h0a) + $(crc k2400h0b) )); H1=$(( $(crc k2400h1a) + $(crc k2400h1b) ))
        H=0; [ "$H1" -gt "$H0" ] && H=1
        echo "$(stamp) Pluto CRC right: half 0 $H0, half 1 $H1; 1200 with half $H" | tee "$RUN/halves.txt"
        echo "$(stamp) waiting for the K6's setting 3 (D/1200)"
        until grep -q "say s 3 " "$K6LOG"; do sleep 1; done
        sleep 3
        key2 "$RUN/c6phytx_1200h$H" k1200a 1200
        sleep 6
        key2 "$RUN/c6phytx_1200h$H" k1200b 1200
    fi
    until grep -q "fsk24 end" "$K6LOG"; do sleep 2; done
    P -m modem.fsk24 report "$K6LOG" | tee "$RUN/k6-report.txt"
    ;;
*)
    echo "stage, dry, live or live2"; exit 2 ;;
esac
