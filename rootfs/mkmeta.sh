#!/bin/bash
export PATH="/c/cygwin64/usr/bin:/c/cygwin64/usr/sbin:$PATH"
cd "C:/Users/cheng2007/Documents/deepseek-harness/default-workspace/oea"
OUT=meta_cmds.txt
: > $OUT
cur=""
while IFS= read -r line; do
  if [[ "$line" == "debugfs: ls -l -p "* ]]; then
    cur="${line#debugfs: ls -l -p }"
    [ "$cur" = "/" ] && cur=""
    continue
  fi
  [[ "$line" != /* ]] && continue
  mode=$(echo "$line" | cut -d'/' -f3)
  uid=$(echo "$line" | cut -d'/' -f4)
  gid=$(echo "$line" | cut -d'/' -f5)
  name=$(echo "$line" | cut -d'/' -f6)
  [ -z "$name" ] && continue
  case "$name" in "."|"..") continue;; esac
  full="$cur/$name"; full=$(echo "$full" | sed 's|//*|/|g')
  echo "sif \"$full\" mode 0$mode" >> $OUT
  echo "sif \"$full\" uid $uid" >> $OUT
  echo "sif \"$full\" gid $gid" >> $OUT
done < lsout.txt
echo "TOTAL cmds: $(wc -l < $OUT)"
