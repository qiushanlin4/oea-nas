#!/bin/bash
# walkall.sh -- dump a byte-exact (mode/uid/gid/name) listing of every directory
#               inside the source image, for mkmeta.sh to consume.
#
# WHY THIS IS A SEPARATE PASS
#   mke2fs -d copies file *contents* but normalises ownership and permissions,
#   and (as mkfs1g.sh explains) drops symlinks entirely.  To put the original
#   metadata back we first need to know what it was.
#
#   debugfs has `ls -l -p <dir>` (one machine-readable line per entry) but it
#   cannot recurse, and a single debugfs process handles many commands when
#   fed through -f.  So: enumerate all directories first, then dump them all
#   in one debugfs run.
#
# Usage:
#   WORK=~/oea ./walkall.sh
#
# Output:  $WORK/lsout.txt      (consumed by mkmeta.sh)
#          $WORK/dirs.txt       (directory list, for reference)

set -e
export PATH="/c/cygwin64/usr/bin:/c/cygwin64/usr/sbin:$PATH"

WORK="${WORK:-$PWD}"
IMG="${IMG:-$WORK/armroot.img}"      # the ORIGINAL extracted partition

cd "$WORK"

if [ ! -f "$IMG" ]; then
    echo "missing image: $IMG" >&2
    exit 1
fi

Q="$WORK/.queue.txt"          # pending directories (BFS frontier)
SEEN="$WORK/.seen.txt"        # everything we have queued, to avoid loops

# ---------------------------------------------------------------- enumerate
# `debugfs -R "rdump"` would copy files; we only want names.
# `ls -l -p -R` does not exist, so do a BFS one directory at a time.
echo "/" > "$Q"
: > "$SEEN"
: > dirs.txt

while :; do
    # pop one directory off the front
    d=$(sed -n '1p' "$Q")
    [ -z "$d" ] && break
    sed -i '1d' "$Q"

    # list it
    echo "ls -l -p $d" > "$WORK/.one.txt"
    debugfs -f "$WORK/.one.txt" "$IMG" 2>/dev/null |
        grep '^/' |
        awk -F'/' '{print $NF}' |
        sed 's|/$||' > "$WORK/.names.txt" || true

    # the -p format puts the mode in field 3; directories have it ending in /,
    # but the reliable test is a `stat` (rare enough to be cheap)
    while IFS= read -r n; do
        [ -z "$n" ] && continue
        case "$n" in "."|"..") continue ;; esac
        if [ "$d" = "/" ]; then child="/$n"; else child="$d/$n"; fi
        grep -qxF "$child" "$SEEN" 2>/dev/null && continue
        echo "$child" >> "$SEEN"
        if debugfs -R "stat $child" "$IMG" 2>/dev/null | grep -qi 'Type: directory'; then
            echo "$child" >> dirs.txt
            echo "$child" >> "$Q"
        fi
    done < "$WORK/.names.txt"
done

echo "directories found: $(wc -l < dirs.txt)"

# ---------------------------------------------------------------- dump them
echo "dumping full listing..."
: > lsbatch.txt
{ echo "ls -l -p /"; while IFS= read -r d; do echo "ls -l -p $d"; done < dirs.txt; } > lsbatch.txt

debugfs -f lsbatch.txt "$IMG" > lsout.txt 2>/dev/null || true
echo "lsout.txt: $(wc -l < lsout.txt) lines"
head -3 lsout.txt

echo
echo "NEXT: ./mkmeta.sh"
