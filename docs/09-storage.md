# 09 · 存储：分区回收 + mergerfs 合并

## 分区全貌

```
mmcblk0  116.5 GB
├─ p1  uboot       4 MB      ← 不能动
├─ p2  trust       4 MB      ← 不能动
├─ p3  oemmisc     4 MB      ← 不能动
├─ p4  boot       64 MB      ← 不能动（原厂内核）
├─ p5  kernel     64 MB      ← 不能动（原厂内核）
├─ p6  env        32 MB      ← 不能动（U-Boot 环境）
├─ p7  system      1 GB      ← 我们的 Armbian rootfs（overlay lowerdir）
├─ p8  backup      1 GB      ← 原厂 Buildroot 备份 → 已回收
├─ p9  instaboot 512 MB      ← overlay upperdir（不能动）
├─ p10 misc       32 MB      ← direct_lvm_mode
├─ p11 cache     512 MB      ← 已回收（给 /var/log）
├─ p12 data      3.5 GB      ← 已回收
└─ p13 xyagent  108.5 GB     ← 主数据盘
```

### ⚠️ p8–p13 不在 GPT 里

这是本项目最容易误解的一点。`parted` 只能看到 p1–p7：

```
$ parted -s /dev/mmcblk0 unit s print free
Number  Start     End       Size      File system  Name     Flags
 1      16384s    24575s    8192s                  uboot
 2      24576s    32767s    8192s                  trust
 3      32768s    40959s    8192s                  misc
 4      40960s    172031s   131072s                boot     boot, esp
 5      172032s   303103s   131072s                kernel
 6      303104s   368639s   65536s                 env
 7      368640s   2465791s  2097152s               rootfs
         2465792s  244285406s                      Free Space
```

p8 之后全部显示为 "Free Space" —— **但它们实际存在且数据完好**。

原因：**p8–p13 是内核 DTB 写死的**。dmesg 原文：

```
[    1.591551] mmcblk0: p1 p2 p3 p4 p5 p6 p7
[    1.593993] parse_partitions_from_dtb parts:13
[    1.595375] add_emmc_partition-336, entry
[    1.597334] add_emmc_partition xyagent size = 0x001b1e800000
```

`parse_partitions_from_dtb parts:13` —— 内核在 MMC 驱动里调用了 Rockchip 私有函数，
按设备树里的固定偏移表创建 13 个分区。

### 后果

1. **`parted` / `sfdisk` 改不了 p8–p13**
2. **`fastboot` 看不到它们**（只认 GPT 里的 7 个）
3. **没有 `/dev/mmcblk0pN` 设备节点**，只有 devtmpfs 的友好名：

```bash
$ ls /dev/mmcblk0p*
ls: cannot access '/dev/mmcblk0p*': No such file or directory

$ ls -l /dev/system /dev/backup /dev/instaboot /dev/data /dev/xyagent
brw-rw---- 1 root disk 179,  7 /dev/system
brw-rw---- 1 root disk 179,  8 /dev/backup
brw-rw---- 1 root disk 179,  9 /dev/instaboot
brw-rw---- 1 root disk 179, 12 /dev/data
brw-rw---- 1 root disk 179, 13 /dev/xyagent
```

### 精确扇区表（从 `/sys/class/block/*/` 读）

```bash
for d in system backup instaboot misc cache data xyagent; do
    printf "%-10s start=%-10s size=%s\n" "$d" \
        "$(cat /sys/class/block/$d/start)" "$(cat /sys/class/block/$d/size)"
done
```

输出（512 字节扇区）：

| 分区 | start | size | 字节数 |
|---|---|---|---|
| system | 368640 | 2097152 | 1,073,741,824 |
| backup | 2465792 | 2097152 | 1,073,741,824 |
| instaboot | 4562944 | 1048576 | 536,870,912 |
| misc | 5611520 | 65536 | 33,554,432 |
| cache | 5677056 | 1048576 | 536,870,912 |
| data | 6725632 | 7340032 | 3,758,096,384 |
| xyagent | 16777216 | 227491840 | 116,475,821,080 |

**xyagent 结束扇区 = 244,269,056，磁盘 = 244,285,440** → 尾部只剩 **16384 扇区（8 MB）**。
所以数据盘**无法再向后扩张**。

---

## 回收原厂系统

### 前提

确认：
1. 原厂系统确实不再需要（`/dev/backup` 里是 Buildroot 2017 的完整副本，~579 MB）
2. 你接受「原厂恢复机制彻底失效」

**我们的用户明确授权**：
> 「我确认永远不使用原厂系统，所有成果我自己承担」

### p8 `/dev/backup`（1 GB，原厂 Buildroot）

```bash
# 先确认内容
mount -t ext4 -o ro /dev/backup /mnt/chk
cat /mnt/chk/etc/os-release
# NAME=Buildroot
# VERSION=2017.02-00032-gc61ea215
# ID=buildroot
# VERSION_ID=2017.02
du -sh /mnt/chk/*
# 270M  usr
# 279M  pluginipk
# 10M   thunder
# ...
umount /mnt/chk

# 销毁
dd if=/dev/zero of=/dev/backup bs=1M count=32 conv=fsync
# 32+0 records in, 32+0 records out, 33554432 bytes (34 MB, 32 MiB) copied,
# 0.465 s, 72.1 MB/s

mkfs.ext4 -q -F -L oldsys-wiped /dev/backup
blkid /dev/backup
# LABEL="oldsys-wiped" UUID="617246cb-..." BLOCK_SIZE="4096" TYPE="ext4"
```

`dd` 清零前 32 MB 是为了**确保旧超级块被覆盖**（`mkfs` 也会做，但双保险）。

### p11 `/dev/cache`（512 MB）

```bash
mkfs.ext4 -q -F -L logfs /dev/cache
```

### p12 `/dev/data`（3.5 GB）

```bash
mkfs.ext4 -q -F -L extra /dev/data
```

### p10 `/dev/misc`（32 MB）—— 不要动

`/init` 会读它判断是否需要格式化（`misc_type=blkid /dev/misc`），
改动可能导致每次启动都重新 mkfs。**留着。**

### p9 `/dev/instaboot`（512 MB）—— 绝对不能动

它是 overlay 的 **upperdir**。格式化等于清空系统里所有改动。

---

## 挂载布局

### 为什么需要 `/var/log` 单独挂

`/` 是 overlayfs，可写层只有 **488 MB**（p9 instaboot）。

我们曾经被 `apt-get update` 写满过：

```
Error writing to file - write (28: No space left on device)
mkdir: cannot create directory '/srv/nas': No space left on device
```

`/tmp` 更是只有 475 MB。

实测占用分布（当时）：

| 路径 | 大小 | 位置 |
|---|---|---|
| `/usr` | 825 MB | 只读下层（p7），搬不动也不必搬 |
| `/var/lib` | 444 MB | 其中 `/var/lib/apt` 422 MB（已 bind 到数据盘） |
| `/var/cache` | 129 MB | 已 bind |
| `/var/log` | 1.7 MB | **会无限增长，必须外挂** |
| `/etc` | 2.6 MB | — |
| `/root` | 20 KB | — |
| `/home` | 16 KB | — |

所以把 **512 MB 的 p11 专门给 `/var/log`**。

### apt 缓存外挂

```bash
mkdir -p /srv/nas/.sys/apt-lists /srv/nas/.sys/apt-cache
mount --bind /srv/nas/.sys/apt-lists /var/lib/apt/lists
mount --bind /srv/nas/.sys/apt-cache /var/cache/apt
```

---

## mergerfs 合并三个分区

### 为什么要合并

回收后有 3 个可用盘（108 GB + 3.4 GB + 1 GB），对用户来说开三个网络驱动器
很不方便。目标是**一个 111 GB 的目录**。

### mergerfs 关键设计：只合并 `data/` 子目录

**不要合并整个盘**，否则 `.sys`（apt 缓存 547 MB）和 `lost+found` 会出现在共享里。

```bash
# 每个盘建一个 data 子目录
mkdir -p /mnt/d1/data /mnt/d2/data /mnt/d3/data

# 原来的内容搬进去
mv /mnt/d1/share  /mnt/d1/data/ 2>/dev/null
mv /mnt/d1/media  /mnt/d1/data/ 2>/dev/null
mv /mnt/d1/backup /mnt/d1/data/ 2>/dev/null
```

最终结构：

```
/mnt/d1/          108 GB
├── .sys/         ← apt 缓存（不共享）
├── data/         ← 共享
│   ├── share/
│   ├── media/
│   └── backup/
└── lost+found/   ← 不共享

/mnt/d2/          3.4 GB
├── data/
└── lost+found/

/mnt/d3/          1 GB
├── data/
└── lost+found/
```

### 安装

```bash
apt-get install -y mergerfs
# mergerfs 2.33.5-1ubuntu2

ls -l /sbin/mount.mergerfs
# -rwxr-xr-x 1 root root ... /sbin/mount.mergerfs

# FUSE 支持
ls -l /dev/fuse
# crw-rw-rw- 1 root root 10, 229 /dev/fuse

cat /proc/filesystems | grep -E 'fuse|fuseblk'
# nodev	fuse
# nodev	fuseblk
# nodev	fusectl
```

### `/etc/fuse.conf`

mergerfs 要用 `allow_other`（让 samba 的 `nobody` 能访问），必须开这个：

```bash
echo "user_allow_other" >> /etc/fuse.conf
```

---

## 完整 `/etc/fstab`

```fstab
# /etc/fstab - OEA NAS (oeanas)
# / is an overlayfs assembled by the factory initramfs; do NOT add a / entry here.
#
# Layout:
#   /mnt/d1 = /dev/xyagent (108G)   /mnt/d2 = /dev/data (3.4G)   /mnt/d3 = /dev/backup (1G)
#   /srv/pool merges only the "data" subdir of each disk -> one 111G tree,
#   system dirs (.sys, lost+found) stay outside the share.

tmpfs /tmp tmpfs defaults,nosuid 0 0

# --- physical disks ---
/dev/xyagent /mnt/d1 ext4 defaults,noatime,nofail,x-systemd.device-timeout=10 0 2
/dev/data    /mnt/d2 ext4 defaults,noatime,nofail,x-systemd.device-timeout=10 0 2
/dev/backup  /mnt/d3 ext4 defaults,noatime,nofail,x-systemd.device-timeout=10 0 2

# --- merged pool (108G + 3.4G + 1G = 111G single tree) ---
/mnt/d1/data:/mnt/d2/data:/mnt/d3/data /srv/pool fuse.mergerfs defaults,allow_other,use_ino,moveonenospc=true,minfreespace=200M,category.create=mfs,fsname=pool,x-systemd.requires-mounts-for=/mnt/d1,x-systemd.requires-mounts-for=/mnt/d2,x-systemd.requires-mounts-for=/mnt/d3 0 0

# --- system dirs kept off the small overlay root ---
/mnt/d1/.sys/apt-lists /var/lib/apt/lists none bind,nofail 0 0
/mnt/d1/.sys/apt-cache /var/cache/apt     none bind,nofail 0 0
/dev/cache             /var/log           ext4 defaults,noatime,nofail,x-systemd.device-timeout=10 0 2
```

### ⚠️ 为什么 `/` 不能出现在 fstab

我们第一版 fstab 里写了：

```
PARTUUID=614e0000-0000  /  ext4  defaults,...  0 1
```

结果重启后**掉进维护模式**：

```
Give root password for maintenance
(or press Control-D to continue)
$ systemctl is-system-running
maintenance
$ systemctl --failed
systemd-remount-fs.service   loaded failed failed
```

日志：

```
systemd-remount-fs[331]: mount: /: can't find PARTUUID=614e0000-0000.
/usr/bin/mount for / exited with exit status 1.
```

**原因**：`/` 是 initramfs 拼出来的 **overlayfs**，不是任何块设备。
那个 PARTUUID 指向的 p7 已经被 mount 到 `/low`（overlay 的 lowerdir），
`systemd-remount-fs` 找不到它来 remount。

**修复：删掉 `/` 那一行。**

### ⚠️ 为什么用 `/dev/xyagent` 而不是 UUID

我们试过 UUID：

```
UUID=360e5227-784e-468a-905c-bdb6a97a2b42  /srv/nas  ext4  ...
```

结果：

```
Timed out waiting for device dev-disk-by\x2duuid-360e5227\x2d784e...device
local-fs.target  failed
```

原因：**udev 不给 eMMC 分区建 `/dev/disk/by-uuid/` 软链**。

```bash
$ ls /dev/disk/by-uuid/
（只有 loop0 一条，没有 xyagent）

$ udevadm info /dev/xyagent
P: /devices/platform/ff0d0000.dwmmc/mmc_host/mmc0/mmc0:0001/block/mmcblk0/mmcblk0p13
N: xyagent
S: disk/by-partlabel/xyagent
E: DEVPATH=...
E: DEVNAME=/dev/xyagent
E: PARTN=13
E: MAJOR=179
E: MINOR=13
E: SUBSYSTEM=block
# ← 注意：没有 ID_FS_UUID！没有 ID_FS_TYPE！
```

`blkid` 却读得到：

```bash
$ blkid /dev/xyagent
/dev/xyagent: LABEL="nasdata" UUID="360e5227-..." TYPE="ext4"
```

**但 udev 就是不建链**（因为 p13 不是内核分区，`60-persistent-storage.rules` 没匹配上）。

**解决：fstab 里直接用设备路径 `/dev/xyagent`。**

> 这个规律对所有 p8–p13 都成立。`lsblk -f` 里它们的 FSTYPE 全是空的。

---

## ext4 预留空间：104 GB → 109 GB

### 问题

用户看到 Windows 只显示 104 GB，问：「可用是104g 怎么少这么多」

### 原因

ext4 默认给 root 预留 **5%** 的块：

```bash
tune2fs -l /dev/xyagent | grep -E 'Block count|Reserved block|Block size|Inode'
# Block count:          28436480
# Reserved block count: 1421824      ← 5%
# Block size:           4096
# Inode count:          7110656
# Inode size:           256
```

换算：`1421824 × 4096 = 5,823,784,960` ≈ **5.5 GB** 被预留。

三个盘合计约 **5.6 GB**。

### 修复

```bash
tune2fs -m 1 /dev/xyagent    # 5% → 1%
tune2fs -m 1 /dev/data
tune2fs -m 1 /dev/backup
```

**立即生效，不用重启、不用重格式化。**

效果：

```
改前：/srv/pool   111G  546M  105G  1%
改后：/srv/pool   111G  546M  109G  1%
```

Windows 侧：

```cmd
dir N:\
# 116,849,790,976 bytes free
```

板端：

```bash
df -B1 /srv/pool
# Filesystem     1B-blocks        Used   Available  Use%  Mounted on
# pool        118685704192  572510208 116849790976   1%  /srv/pool
```

**完全一致。**

### 为什么留 1%

**不要降到 0。** ext4 在写满时需要预留空间做日志提交，0% 会导致写满后系统卡死。

### 剩余 2 GB 差额去哪了

`111 GB 总 - 109 GB 可用 = 2 GB`：

| 项目 | 大小 |
|---|---|
| 1% 预留 | ~1.1 GB |
| inode 表（7,110,656 × 256 B） | ~1.7 GB |
| 日志区（journal） | ~128 MB |

这是 ext4 的固有开销，无法消除（除非用 XFS/btrfs，但 4.19 内核上 ext4 最稳）。

---

## 最终状态

```bash
$ df -h
Filesystem      Size  Used Avail Use% Mounted on
overlay         488M  104M  350M  23% /
tmpfs           475M     0  475M   0% /dev/shm
/dev/xyagent    107G  569M  105G   1% /mnt/d1
/dev/data       3.4G   28K  3.4G   1% /mnt/d2
/dev/backup     974M   28K  948M   1% /mnt/d3
/dev/cache      488M  2.8M  449M   1% /var/log
pool            111G  569M  109G   1% /srv/pool

$ mount | grep -E 'pool|mnt/d|var/'
/dev/xyagent on /mnt/d1 type ext4 (rw,noatime)
/dev/data on /mnt/d2 type ext4 (rw,noatime)
/dev/backup on /mnt/d3 type ext4 (rw,noatime)
/dev/cache on /var/log type ext4 (rw,noatime)
/dev/xyagent on /var/cache/apt type ext4 (rw,noatime)
/dev/xyagent on /var/lib/apt/lists type ext4 (rw,noatime)
pool on /srv/pool type fuse.mergerfs (rw,relatime,user_id=0,group_id=0,default_permissions,allow_other)
```

`mergerfs -V` → `mergerfs version: 2.33.5`

下一节 → [10-troubleshooting.md](10-troubleshooting.md)
