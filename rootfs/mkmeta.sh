#!/bin/bash
# mkmeta.sh -- turn the `ls -l -p` dump into debugfs `sif` commands.
#
# INPUT   lsout.txt   (from walkall.sh)
# OUTPUT  meta_cmds.txt
#
# The -p (machine readable) format of `debugfs ls -l -p <dir>` is:
#
#     /inode/mode/uid/gid/name/size/
#
# with a `debugfs: ls -l -p <dir>` banner line before each directory block.
# Real sample (from an Armbian 26.11 rk3318 rootfs):
#
#     debugfs: ls -l -p /
#     /2/040755/0/0/.//
#     /11/040700/0/0/lost+found//
#     /13/040755/0/0/bin.usr-is-merged//
#     /100644/...  <- no, mode is always field 3
#
# NOTE the leading slash: with `cut -d'/'` field 1 is empty, so
#     field 2 = inode, field 3 = mode, field 4 = uid, field 5 = gid, field 6 = name
#
# Mode already carries the file-type bits (040000 dir, 0100000 file,
# 0120000 symlink), so it goes straight into `sif <path> mode 0<mode>`.
#
# We emit three commands per entry (mode, uid, gid) -> ~71157 lines for a full
# Armbian rootfs; debugfs applies them in well under a minute.
#
# Usage:
#   WORK=~/oea ./mkmeta.sh

set -e
export PATH="/c/cygwin64/usr/bin:/c/cygwin64/usr/sbin:$PATH"

WORK="${WORK:-$PWD}"
cd "$WORK"

if [ ! -f lsout.txt ]; then
    echo "missing lsout.txt -- run ./walkall.sh first" >&2
    exit 1
fi

OUT=meta_cmds.txt
: > "$OUT"
cur=""
count=0

while IFS= read -r line; do
    # remember which directory the following lines belong to
    if [[ "$line" == "debugfs: ls -l -p "* ]]; then
        cur="${line#debugfs: ls -l -p }"
        [ "$cur" = "/" ] && cur=""
        continue
    fi

    [[ "$line" != /* ]] && continue      # skip debugfs chatter / blank lines

    mode=$(echo "$line" | cut -d'/' -f3)
    uid=$(echo  "$line" | cut -d'/' -f4)
    gid=$(echo  "$line" | cut -d'/' -f5)
    name=$(echo "$line" | cut -d'/' -f6)

    [ -z "$name" ] && continue
    case "$name" in "."|"..") continue ;; esac
    [ -z "$mode" ] && continue

    full="$cur/$name"
    full=$(echo "$full" | sed 's|//*|/|g')

    # mode already includes the type bits; prefix 0 so debugfs reads it as octal
    echo "sif \"$full\" mode 0$mode" >> "$OUT"
    echo "sif \"$full\" uid $uid"    >> "$OUT"
    echo "sif \"$full\" gid $gid"    >> "$OUT"
    count=$((count + 1))
done < lsout.txt

echo "entries : $count"
echo "commands: $(wc -l < "$OUT")"
echo
echo "NEXT:  debugfs -w -f $OUT newrootfs.img"
echo
cat <<'CHECK'
SPOT-CHECK these afterwards -- they are what actually broke our first build:
  debugfs -R 'stat /bin'   newrootfs.img   # must be a symlink -> usr/bin
  debugfs -R 'stat /lib'   newrootfs.img   # must be a symlink -> usr/lib
  debugfs -R 'stat /sbin'  newrootfs.img   # must be a symlink -> usr/sbin
  debugfs -R 'stat /usr/bin/sudo' newrootfs.img   # mode must include 04755
CHECK
