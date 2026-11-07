#!/bin/bash
# ddroot.sh -- extract ONLY the rootfs partition out of an Armbian image.
#
# Armbian's .img is a whole-disk image; the rootfs is not at offset 0.
# Rather than trust a partition table parser, we use the known byte offset
# for the RK3318 builds we worked with.  If your image differs, run
#   fdisk -l armbian-rk3318.img
# on Linux (or `parted -s <img> unit B print` in Cygwin) and use its numbers.
#
# Usage:
#   WORK=~/oea ./ddroot.sh
#
# Environment:
#   WORK  working directory (default: current directory)
#   IMG   source image (default: $WORK/armbian-rk3318.img)
#   OUT   destination    (default: $WORK/armroot.img)
#   OFF   byte offset of the rootfs partition (default: 553648128 = 528 MiB)
#   SIZE  its size in bytes                  (default: 3144679424)

set -e
export PATH="/c/cygwin64/usr/bin:/c/cygwin64/usr/sbin:$PATH"

WORK="${WORK:-$PWD}"
IMG="${IMG:-$WORK/armbian-rk3318.img}"
OUT="${OUT:-$WORK/armroot.img}"
OFF="${OFF:-553648128}"
SIZE="${SIZE:-3144679424}"

if [ ! -f "$IMG" ]; then
    echo "missing source image: $IMG" >&2
    echo "set IMG=... or put armbian-rk3318.img in $WORK" >&2
    exit 1
fi

echo "source : $IMG ($(stat -c%s "$IMG") bytes)"
echo "offset : $OFF   size: $SIZE"
echo "extracting rootfs partition..."

dd if="$IMG" of="$OUT" bs=1M skip=$((OFF / 1048576)) count=$((SIZE / 1048576)) \
   status=progress 2>&1 | tail -3

ls -la "$OUT"
# sanity: a valid ext4 has the magic 0xEF53 at offset 0x438
echo -n "ext4 magic at 0x438: "
dd if="$OUT" bs=1 skip=1080 count=2 2>/dev/null | od -An -tx1
