#!/usr/bin/env bash
# Update the ADALM-Pluto's firmware over its USB mass-storage drive (ADI's documented way:
# copy pluto.frm to the PlutoSDR drive, eject it, the Pluto flashes and reboots by itself).
#
#   bash tools/pluto_fw_update.sh PATH/TO/pluto.frm [SHA256]
#
# Run it at hamlet by hand: it asks for sudo to mount and eject the drive when udisksctl can't
# (hamlet has no udisks on 11-16, so expect a password prompt).
# Never unplug the Pluto while LED1 blinks fast (it is writing flash: about 3-4 minutes).
# Afterwards it waits for the Pluto to come back, prints the new firmware version and powers its
# transmitter down (tools/pluto_bench.py quiet). The u-boot environment (ad9364 mode,
# xo_correction) is not touched by a pluto.frm update.
# SPDX-License-Identifier: MIT
set -euo pipefail

FRM=${1:?usage: pluto_fw_update.sh PATH/TO/pluto.frm [SHA256]}
WANT=${2:-}
HERE=$(cd "$(dirname "$0")" && pwd)
[ -f "$FRM" ] || { echo "no such file: $FRM"; exit 1; }
SIZE=$(stat -c %s "$FRM")
[ "$SIZE" -gt 4000000 ] || { echo "$FRM is only $SIZE bytes: not a Pluto image"; exit 1; }
GOT=$(sha256sum "$FRM" | cut -d' ' -f1)
echo "image: $FRM ($SIZE bytes, sha256 $GOT)"
if [ -n "$WANT" ] && [ "$GOT" != "$WANT" ]; then
  echo "sha256 mismatch: expected $WANT"; exit 1
fi

# the Pluto's drive: the partition labelled PlutoSDR
PART=$(lsblk -rno NAME,LABEL | awk '$2=="PlutoSDR"{print $1; exit}')
[ -n "$PART" ] || { echo "no PlutoSDR drive found (is the Pluto plugged in? lsblk -o NAME,LABEL)"; exit 1; }
DEV=/dev/$PART
DISK=/dev/$(lsblk -rno PKNAME "$DEV" | head -1)
echo "Pluto drive: $DEV on $DISK"
fwver() { iio_attr -u usb: -C fw_version 2>/dev/null | tail -1 | sed 's/^fw_version: *//' || true; }
present() { grep -qs PlutoSDR /sys/bus/usb/devices/*/product; }
BEFORE=$(fwver)
echo "before: ${BEFORE:-unknown}"

MNT=$(lsblk -rno MOUNTPOINT "$DEV" | head -1)
if [ -z "$MNT" ]; then
  if udisksctl mount -b "$DEV" >/dev/null 2>&1; then
    MNT=$(lsblk -rno MOUNTPOINT "$DEV" | head -1)
  else
    MNT=$(mktemp -d /tmp/plutosdr.XXXX)
    sudo mount -o "uid=$(id -u),gid=$(id -g)" "$DEV" "$MNT"
  fi
fi
echo "mounted at $MNT: $(ls "$MNT" | tr '\n' ' ')"
grep -o 'v0\.[0-9][0-9]*[^<" ]*' "$MNT/info.html" 2>/dev/null | head -3 | sed 's/^/info.html: /' || true

echo "copying pluto.frm ..."
cp "$FRM" "$MNT/pluto.frm"
sync
echo "copied; ejecting (the Pluto starts flashing on eject)"
if ! udisksctl unmount -b "$DEV" >/dev/null 2>&1; then
  sudo umount "$DEV"
fi
if ! eject "$DISK" 2>/dev/null; then
  sudo eject "$DISK"
fi
echo "ejected at $(date -u +%H:%M:%SZ). DO NOT UNPLUG: LED1 blinks fast while it writes flash."

# wait for the Pluto to finish writing and drop off USB (it stays enumerated while it flashes;
# iio is not asked anything meanwhile), then to come back reporting a different version
for i in $(seq 1 120); do
  sleep 5
  present || break
done
if present; then
  echo "the Pluto is still on USB after 10 minutes: check LED1 (blinking fast = still writing; leave it)"
  exit 1
fi
echo "Pluto rebooting at $(date -u +%H:%M:%SZ) ..."
for i in $(seq 1 96); do
  sleep 5
  present || continue
  NOW=$(fwver)
  if [ -n "$NOW" ] && [ "$NOW" != "$BEFORE" ]; then
    echo "back at $(date -u +%H:%M:%SZ): $NOW (was ${BEFORE:-unknown})"
    python3 "$HERE/pluto_bench.py" quiet || true
    exit 0
  fi
done
echo "the Pluto hasn't come back with a new version after 8 minutes (now: $(fwver)): check its LEDs"
exit 1
