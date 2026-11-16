#!/bin/sh
# Fetch the text-to-speech voices for tools/tts_say.py (docs/uv380-tts.md) into DIR,
# and with --score the listeners tools/tts_compare.py scores with. Everything comes from
# GitHub release assets (no Hugging Face login), and a file already there is kept.
#
#   sh tools/tts_models.sh ~/blowfang-work/tts/models [--score] [--all]
#
# Default: Kokoro v1.0 (int8 model, 88 MB, and its voice pack) and the Piper voice
# en_US-hfc_female-medium. --all adds every Piper voice the comparison tried.
# Licenses: Kokoro Apache-2.0; Piper voices per their MODEL_CARD (hfc_female: CC0).
# SPDX-License-Identifier: MIT
set -eu
[ $# -ge 1 ] || { echo "usage: sh tools/tts_models.sh DIR [--score] [--all]" >&2; exit 2; }
DIR=$1
shift
SCORE=0
ALL=0
for a in "$@"; do
    case $a in
        --score) SCORE=1 ;;
        --all) ALL=1 ;;
        *) echo "unknown option $a" >&2; exit 2 ;;
    esac
done
mkdir -p "$DIR"
cd "$DIR"
KOKORO=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
SHERPA=https://github.com/k2-fsa/sherpa-onnx/releases/download
PIPER_OLD=https://github.com/rhasspy/piper/releases/download/v0.0.2

get() {  # get URL FILE
    [ -s "$2" ] && return 0
    echo "fetching $2"
    curl -fsSL -o "$2.part" "$1"
    mv "$2.part" "$2"
}

sherpa_tar() {  # sherpa_tar RELEASE NAME: unpack NAME.tar.bz2 into NAME/
    [ -d "$2" ] && return 0
    echo "fetching $2"
    curl -fsSL "$SHERPA/$1/$2.tar.bz2" | tar xjf -
}

piper_old() {  # piper_old NAME: en-us-NAME.onnx and .onnx.json from piper v0.0.2
    [ -s "en-us-$1.onnx" ] && return 0
    echo "fetching en-us-$1"
    curl -fsSL "$PIPER_OLD/voice-en-us-$1.tar.gz" | tar xzf - "en-us-$1.onnx" "en-us-$1.onnx.json"
}

get "$KOKORO/kokoro-v1.0.int8.onnx" kokoro-v1.0.int8.onnx
get "$KOKORO/voices-v1.0.bin" voices-v1.0.bin
sherpa_tar tts-models vits-piper-en_US-hfc_female-medium

if [ $ALL = 1 ]; then
    for v in \
        en_US-hfc_male-medium \
        en_US-joe-medium \
        en_US-john-medium \
        en_US-kristin-medium \
        en_US-lessac-high \
        en_US-norman-medium \
        en_GB-alan-medium \
        en_GB-cori-high \
        en_GB-northern_english_male-medium
    do
        sherpa_tar tts-models "vits-piper-$v"
    done
    for v in \
        amy-low \
        lessac-medium \
        ryan-medium \
        ryan-high
    do
        piper_old "$v"
    done
fi

if [ $SCORE = 1 ]; then
    sherpa_tar asr-models sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8
    sherpa_tar asr-models sherpa-onnx-whisper-base.en
    get https://raw.githubusercontent.com/microsoft/DNS-Challenge/master/DNSMOS/DNSMOS/sig_bak_ovr.onnx \
        sig_bak_ovr.onnx
fi
echo "models in $DIR"
