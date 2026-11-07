#!/bin/bash
export PATH="/c/cygwin64/usr/bin:/c/cygwin64/usr/sbin:$PATH"
cd "C:/Users/cheng2007/Documents/deepseek-harness/default-workspace/oea"
# Use debugfs "ls -l -p -R"? Not supported. Instead: use a single interactive-ish batch
# Trick: debugfs can take many -R in one run only one. So use "rdump"-like listing:
# Simplest robust: use "debugfs -R 'ls -l -p <dir>'" but batch 50 dirs per debugfs invocation via -f
CMDS=lsbatch.txt
: > $CMDS
while IFS= read -r d; do
  [ -z "$d" ] && continue
  echo "ls -l -p $d" >> $CMDS
done < dirs.txt
echo "batch cmds: $(wc -l < $CMDS)"
debugfs -f $CMDS armroot.img > lsout.txt 2>/dev/null
echo "output lines: $(wc -l < lsout.txt)"
head -3 lsout.txt
