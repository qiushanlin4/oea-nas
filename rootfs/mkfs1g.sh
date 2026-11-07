#!/bin/bash
export PATH="/c/cygwin64/usr/bin:/c/cygwin64/usr/sbin:$PATH"
cd "C:/Users/cheng2007/Documents/deepseek-harness/default-workspace/oea"
OUT=newrootfs.img
STAGE=stage_out
rm -f $OUT
rm -rf $STAGE
echo "[1] recording symlinks..."
find rootfs_out -type l -printf '%p\t%l\n' 2>/dev/null > symlinks.tsv
echo "    $(wc -l < symlinks.tsv) symlinks recorded"
echo "[2] copying tree..."
mkdir -p $STAGE
cp -a rootfs_out/. $STAGE/ 2>/dev/null
echo "[3] removing symlinks from stage..."
find $STAGE -type l -delete 2>/dev/null
echo "    remaining symlinks: $(find $STAGE -type l 2>/dev/null | wc -l)"
echo "[4] creating ext4 (262144 blocks x 4096 = 1 GiB)..."
mke2fs -q -t ext4 -b 4096 -F -d $STAGE -L rootfs $OUT 262144 2>&1 | head -6
echo "[5] result:"
ls -la $OUT
