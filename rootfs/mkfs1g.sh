#!/bin/bash
# mkfs1g.sh -- build the 1 GiB ext4 and work around mke2fs dropping symlinks.
#
# THE PROBLEM
#   `mke2fs -d <dir>` copies a directory tree into a fresh filesystem, but it
#   does NOT recreate symbolic links.  We measured 3189 of them silently
#   lost in one pass.
#
#   Three of those losses are fatal:
#       /bin  -> usr/bin
#       /lib  -> usr/lib
#       /sbin -> usr/sbin
#   Without them /lib/aarch64-linux-gnu/security/pam_unix.so is unreachable,
#   PAM aborts with "Critical error - immediate abort", and the board boots
#   fine but you can never log in.  (We lost two days to this.)
#
# THE WORKAROUND (3 phases)
#   1. record every symlink (path -> target) into symlinks.tsv
#   2. delete the symlinks from the staging copy, then mke2fs -d the staging copy
#   3. after the image exists, recreate all symlinks with debugfs
#      (sym_cmds.txt, generated below)
#
# Usage:
#   WORK=~/oea ./mkfs1g.sh

set -e
export PATH="/c/cygwin64/usr/bin:/c/cygwin64/usr/sbin:$PATH"

WORK="${WORK:-$PWD}"
SRC="${SRC:-$WORK/rootfs_out}"
OUT="${OUT:-$WORK/newrootfs.img}"
STAGE="${STAGE:-$WORK/stage_out}"
BLOCKS=262144          # 262144 x 4096 = 1 GiB, exactly p7's size

cd "$WORK"

if [ ! -d "$SRC" ]; then
    echo "missing rootfs tree: $SRC" >&2
    exit 1
fi

echo "[1/5] recording symlinks"
find "$SRC" -type l -printf '%p\t%l\n' 2>/dev/null > symlinks.tsv
n=$(wc -l < symlinks.tsv)
echo "      $n symlinks recorded"
if [ "$n" -lt 100 ]; then
    echo "WARNING: suspiciously few symlinks -- find -printf may not work here" >&2
fi

echo "[2/5] staging copy"
rm -f "$OUT"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -a "$SRC"/. "$STAGE"/ 2>/dev/null || true

echo "[3/5] removing symlinks from the staging copy"
find "$STAGE" -type l -delete 2>/dev/null || true
left=$(find "$STAGE" -type l 2>/dev/null | wc -l)
echo "      symlinks left in stage: $left (should be 0)"

echo "[4/5] creating ext4: $BLOCKS blocks x 4096 = $((BLOCKS * 4096 / 1048576)) MiB"
mke2fs -q -t ext4 -b 4096 -F -d "$STAGE" -L rootfs "$OUT" "$BLOCKS" 2>&1 | head -6 || true

echo "[5/5] generating symlink commands"
# symlinks.tsv lines look like:  rootfs_out/bin<TAB>usr/bin
# debugfs accepts an absolute path on `symlink` (unlike `write`, which does NOT).
# Relative targets ("usr/bin") must stay relative -- the kernel resolves them
# against the link's own directory, which is exactly what the original did.
awk -F'\t' -v prefix="$SRC" '
BEGIN { p = prefix "/"; lp = length(p) }
{
    src = $1
    if (substr(src, 1, lp) == p) src = substr(src, lp + 1)
    if (src == "") next
    printf "symlink \"/%s\" \"%s\"\n", src, $2
}' symlinks.tsv > sym_cmds.txt

echo "      sym_cmds.txt: $(wc -l < sym_cmds.txt) lines"
head -3 sym_cmds.txt

echo
echo "done: $OUT"
ls -la "$OUT"
echo
cat <<'NEXT'
NEXT:
  debugfs -w -f sym_cmds.txt  newrootfs.img    # recreate ~3189 symlinks
  ./mkmeta.sh                                   # -> meta_cmds.txt
  debugfs -w -f meta_cmds.txt newrootfs.img    # ~71157 sif commands
  debugfs -w -f fix-pam.txt   newrootfs.img    # MUST NOT SKIP
  e2fsck -fy newrootfs.img
NEXT
