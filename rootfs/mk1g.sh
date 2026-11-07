#!/bin/bash
# mk1g.sh -- trim an extracted Armbian rootfs down so it fits in 1 GiB.
#
# The RK3318 Armbian rootfs is ~3 GB. p7 (/dev/system) is exactly 1 GiB,
# so we have to get under that.  Everything removed here is either
# recoverable with apt or useless on a headless NAS board.
#
# Usage:
#   WORK=~/oea ./mk1g.sh
#
# Environment:
#   WORK  working directory (default: current directory)
#   SRC   extracted rootfs tree (default: $WORK/rootfs_out)

set -e
export PATH="/c/cygwin64/usr/bin:/c/cygwin64/usr/sbin:$PATH"

WORK="${WORK:-$PWD}"
SRC="${SRC:-$WORK/rootfs_out}"

cd "$WORK"
if [ ! -d "$SRC" ]; then
    echo "missing rootfs tree: $SRC" >&2
    echo "extract it first:  debugfs -R 'rdump / rootfs_out' armroot.img" >&2
    exit 1
fi

before=$(du -sb "$SRC" | awk '{printf "%.0f", $1/1048576}')
echo "before: ${before} MB"

# 1. apt index cache -- always re-fetched, and it is 300+ MB
rm -rf "$SRC"/var/lib/apt/lists/* 2>/dev/null || true

# 2. kernel headers and build leftovers -- we never compile on the board
rm -rf "$SRC"/usr/src/linux-headers-* 2>/dev/null || true
rm -rf "$SRC"/usr/lib/modules/*/build 2>/dev/null || true
rm -rf "$SRC"/usr/lib/modules/*/source 2>/dev/null || true
rm -rf "$SRC"/usr/include 2>/dev/null || true

# 3. docs / man / locales / i18n -- headless box, English only
rm -rf "$SRC"/usr/share/doc/* 2>/dev/null || true
rm -rf "$SRC"/usr/share/man/* 2>/dev/null || true
rm -rf "$SRC"/usr/share/locale/* 2>/dev/null || true
rm -rf "$SRC"/usr/share/i18n/* 2>/dev/null || true
rm -rf "$SRC"/usr/share/unicode/* 2>/dev/null || true
rm -rf "$SRC"/usr/share/zsh/* 2>/dev/null || true
rm -rf "$SRC"/usr/share/perl/* 2>/dev/null || true

# 4. firmware for radios this board does not have.
#    OEA has no WiFi and no Bluetooth.  The wired NIC (rk_gmac) needs no
#    firmware blob.  Keep brcm/rtl/mediatek in case you add a USB dongle.
find "$SRC"/usr/lib/firmware -mindepth 1 -maxdepth 1 -type d \
     ! -name "brcm" ! -name "rtl*" ! -name "mediatek" \
     -exec rm -rf {} + 2>/dev/null || true

after=$(du -sb "$SRC" | awk '{printf "%.0f", $1/1048576}')
echo "after : ${after} MB   (need < 1024)"

if [ "$after" -ge 1024 ]; then
    echo "STILL TOO BIG -- trim more before continuing:" >&2
    du -sh "$SRC"/* 2>/dev/null | sort -hr | head -15 >&2
    exit 1
fi

echo "OK -- $((1024 - after)) MB of headroom left in the 1 GiB partition"
