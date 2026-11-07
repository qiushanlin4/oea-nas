#!/bin/bash
export PATH="/c/cygwin64/usr/bin:/c/cygwin64/usr/sbin:$PATH"
IMG="C:/Users/cheng2007/Documents/deepseek-harness/default-workspace/oea/armbian-rk3318.img"
OUT="C:/Users/cheng2007/Documents/deepseek-harness/default-workspace/oea/armroot.img"
OFF=553648128
SIZE=3144679424
echo "extracting rootfs partition ($SIZE bytes)..."
dd if="$IMG" of="$OUT" bs=1M skip=$((OFF/1048576)) count=$((SIZE/1048576)) status=progress 2>&1 | tail -3
ls -la "$OUT"
