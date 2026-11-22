#!/bin/sh
# pluto_chan for the host (test) and the Pluto (armhf, static). HAMFLAC: a HamFLAC checkout
# (github.com/baitisj/hamflac; Artistic-2.0, linked, not copied into this MIT repository).
set -e
cd "$(dirname "$0")"
H=${HAMFLAC:?set HAMFLAC to a github.com/baitisj/hamflac checkout}
O=${OUT:-build}
mkdir -p "$O"
build() {  # CC CXX FLAGS NAME
  $1 -O3 -ffast-math -Wall -pthread $3 -c pluto_chan.c -o "$O/pc_$4.o"
  $2 -O2 -std=c++20 -Wall $3 -I"$H/include" -I"$H/src" -I"$H/third_party/dr_libs" -c flacout.cpp -o "$O/fo_$4.o"
  $2 -O2 -std=c++20 $3 -I"$H/include" -I"$H/src" -I"$H/third_party/dr_libs" -c "$H/src/hamflac.cpp" -o "$O/hf_$4.o"
  $2 $3 -o "$O/$4" "$O/pc_$4.o" "$O/fo_$4.o" "$O/hf_$4.o" -lm -pthread $5
}
build cc c++ "" pluto_chan
command -v arm-linux-gnueabihf-g++ >/dev/null && \
  build arm-linux-gnueabihf-gcc arm-linux-gnueabihf-g++ "-mcpu=cortex-a9 -mfpu=neon -mfloat-abi=hard -Wno-psabi" pluto_chan.armhf "-static"
ls -l "$O"/pluto_chan*
