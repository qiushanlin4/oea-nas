#!/bin/bash
export PATH="/c/cygwin64/usr/bin:/c/cygwin64/usr/sbin:$PATH"
cd "C:/Users/cheng2007/Documents/deepseek-harness/default-workspace/oea"
SRC=rootfs_out
# 1. make room: remove things that are either useless on this board or recoverable
rm -rf $SRC/var/lib/apt/lists/* 2>/dev/null
rm -rf $SRC/usr/src/linux-headers-* 2>/dev/null
rm -rf $SRC/usr/lib/modules/*/build $SRC/usr/lib/modules/*/source 2>/dev/null
rm -rf $SRC/usr/include 2>/dev/null
rm -rf $SRC/usr/share/doc/* 2>/dev/null
rm -rf $SRC/usr/share/man/* 2>/dev/null
rm -rf $SRC/usr/share/locale/* 2>/dev/null
rm -rf $SRC/usr/share/i18n/* 2>/dev/null
rm -rf $SRC/usr/share/unicode/* 2>/dev/null
rm -rf $SRC/usr/share/zsh/* 2>/dev/null
rm -rf $SRC/usr/share/perl/* 2>/dev/null
# firmware: keep only what RK3318 needs (wifi/bt on this box are optional)
find $SRC/usr/lib/firmware -mindepth 1 -maxdepth 1 -type d ! -name "brcm" ! -name "rtl*" ! -name "mediatek" -exec rm -rf {} + 2>/dev/null
echo "after trim: $(du -sb $SRC | awk '{printf "%.0f MB", $1/1048576}')"
