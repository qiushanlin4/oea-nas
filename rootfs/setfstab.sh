#!/bin/bash
# setfstab.sh -- write /etc/fstab into the image.
#
# IMPORTANT: do NOT put a "/" entry in this file.
#
#   On this box / is not a block device.  The factory initramfs assembles an
#   overlayfs (lowerdir=p7, upperdir=p9) and hands it to systemd as /.  If
#   fstab also claims to mount / (with a PARTUUID that no longer even exists
#   after we overwrote p7), systemd-remount-fs fails on every boot and you drop
#   into:
#       Give root password for maintenance
#   which is a slow, confusing failure that looks like a boot problem but is
#   really a one-line fstab mistake.
#
#   The same applies to UUID= references: udev on this device does not create
#   /dev/disk/by-uuid/* for the DTB partitions (they are not real GPT entries),
#   so UUID= entries time out too.  Use the friendly device nodes:
#       /dev/xyagent  /dev/data  /dev/backup  /dev/cache  /dev/system
#
# Usage:
#   WORK=~/oea IMG=~/oea/newrootfs.img ./setfstab.sh

set -e
export PATH="/c/cygwin64/usr/bin:/c/cygwin64/usr/sbin:$PATH"

WORK="${WORK:-$PWD}"
IMG="${IMG:-$WORK/newrootfs.img}"

cd "$WORK"

if [ ! -f "$IMG" ]; then
    echo "missing image: $IMG" >&2
    exit 1
fi

cat > fstab.new <<'FSTAB'
# /etc/fstab - OEA NAS
# / is an overlayfs assembled by the factory initramfs; do NOT add a / entry.

tmpfs /tmp tmpfs defaults,nosuid 0 0

# --- data disk (p13 xyagent, 108G) ---
/dev/xyagent /mnt/d1 ext4 defaults,noatime,nofail,x-systemd.device-timeout=10 0 2

# --- reclaimed factory partitions ---
/dev/data    /mnt/d2 ext4 defaults,noatime,nofail,x-systemd.device-timeout=10 0 2
/dev/backup  /mnt/d3 ext4 defaults,noatime,nofail,x-systemd.device-timeout=10 0 2

# --- merged pool: only the "data" subdir of each disk, so .sys / lost+found
#     stay out of the share ---
/mnt/d1/data:/mnt/d2/data:/mnt/d3/data /srv/pool fuse.mergerfs defaults,allow_other,use_ino,moveonenospc=true,minfreespace=200M,category.create=mfs,fsname=pool,x-systemd.requires-mounts-for=/mnt/d1,x-systemd.requires-mounts-for=/mnt/d2,x-systemd.requires-mounts-for=/mnt/d3 0 0

# --- keep the 488 MB overlay root from filling up ---
/mnt/d1/.sys/apt-lists /var/lib/apt/lists none bind,nofail 0 0
/mnt/d1/.sys/apt-cache /var/cache/apt     none bind,nofail 0 0
/dev/cache             /var/log           ext4 defaults,noatime,nofail,x-systemd.device-timeout=10 0 2
FSTAB

# debugfs write needs a relative destination name after a cd
{
    echo "cd /etc"
    echo "rm fstab"
    echo "write $WORK/fstab.new fstab"
    echo "sif fstab mode 0100644"
    echo "sif fstab uid 0"
    echo "sif fstab gid 0"
} > "$WORK/.setfstab.txt"

debugfs -w -f "$WORK/.setfstab.txt" "$IMG" >/dev/null 2>&1 || true

echo "=== fstab now in $IMG ==="
debugfs -R "cat /etc/fstab" "$IMG" 2>/dev/null
echo
echo "=== hostname ==="
debugfs -R "cat /etc/hostname" "$IMG" 2>/dev/null

cat <<'NOTE'

NOTE: this fstab assumes you have already created the mount points and the
      "data" subdirectories inside the image.  If /srv/pool and /mnt/d1..d3
      do not exist, create them before flashing:

        for d in mnt/d1 mnt/d2 mnt/d3 srv/pool mnt/d1/data mnt/d2/data mnt/d3/data; do
            mkdir -p rootfs_out/$d
        done

      ...or simply run the mkdir commands on the board after first boot; the
      mounts use "nofail" so a missing target is not fatal.
NOTE
