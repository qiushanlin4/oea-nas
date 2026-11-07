#!/bin/bash
export PATH="/c/cygwin64/usr/bin:/c/cygwin64/usr/sbin:$PATH"
cd "C:/Users/cheng2007/Documents/deepseek-harness/default-workspace/oea"
IMG=final2.img
# fstab: mount / by PARTUUID (what the stock kernel cmdline uses), drop the /boot entry
cat > fstab.new <<'FSTAB'
PARTUUID=614e0000-0000  /      ext4  defaults,noatime,nodiratime,commit=600,errors=remount-ro  0 1
tmpfs                   /tmp   tmpfs defaults,nosuid                                             0 0
FSTAB
debugfs -w -R "rm /etc/fstab" $IMG 2>/dev/null
debugfs -w -R "write fstab.new /etc/fstab" $IMG 2>/dev/null
debugfs -w -R "sif /etc/fstab mode 0100644" $IMG 2>/dev/null
debugfs -w -R "sif /etc/fstab uid 0" $IMG 2>/dev/null
debugfs -w -R "sif /etc/fstab gid 0" $IMG 2>/dev/null
echo "=== fstab in image ==="
debugfs -R "cat /etc/fstab" $IMG 2>/dev/null
echo "=== hostname ==="
debugfs -R "cat /etc/hostname" $IMG 2>/dev/null
