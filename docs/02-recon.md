# 02 · 原厂系统侦察

## 分区表

### GPT 里真实存在的分区（p1–p7）

```bash
parted -s /dev/mmcblk0 unit s print free
```

```
Number  Start      End        Size       Name
 1      16384s     24575s     8192s      uboot
 2      24576s     32767s     8192s      trust
 3      32768s     40959s     8192s      misc
 4      40960s     172031s    131072s    boot
 5      172032s    303103s    131072s    kernel
 6      303104s    368639s    65536s     env
 7      368640s    2465791s   2097152s   rootfs
                 2465792s .. 244285406s  Free Space
```

U-Boot 自己的视角（只打印 7 个）：

```
Partition Map for MMC device 0  --   Partition Type: EFI

Part    Start LBA       End LBA         Name
 1      0x00000040      0x00003fff      uboot
 2      0x00004000      0x00007fff      trust
 3      0x00008000      0x00009fff      misc
 4      0x0000a000      0x00029fff      boot
 5      0x0002a000      0x0004bfff      kernel
 6      0x0004c000      0x00056fff      env
 7      0x00057000      0x00257fff      rootfs
```

> **注意**：U-Boot 里 `rootfs` 的 start 显示 `0x5a000`（368640），size `0x200000`（2097152 扇区 = 1 GiB）。

### p8–p13：不在 GPT 里

这是最关键的一个发现。**p8 之后的所有分区都是内核 DTB 里写死的固定偏移**，
由 Rockchip 的 `parse_partitions_from_dtb` 创建，不是标准 GPT 分区。

内核启动日志：

```
[1.591551] mmcblk0: p1 p2 p3 p4 p5 p6 p7
[1.593993] parse_partitions_from_dtb parts:13
[1.595375] add_emmc_partition-336, entry
[1.597334] add_emmc_partition xyagent size = 0x001b1e800000
```

完整布局（`/sys/class/block/*/start` + `/size`，单位 512 B 扇区）：

| 设备名 | 分区 | start 扇区 | size 扇区 | 大小 | 用途 |
|---|---|---|---|---|---|
| `/dev/uboot` | p1 | 16384 | 8192 | 4 MB | U-Boot |
| `/dev/trust` | p2 | 24576 | 8192 | 4 MB | TrustOS |
| `/dev/misc` | p3 | 32768 | 8192 | 4 MB | misc（GPT 里的） |
| `/dev/boot` | p4 | 40960 | 131072 | 64 MB | 原厂 kernel 镜像 |
| `/dev/kernel` | p5 | 172032 | 131072 | 64 MB | 原厂 recovery |
| `/dev/env` | p6 | 303104 | 65536 | 32 MB | U-Boot env |
| `/dev/system` | p7 | 368640 | 2097152 | 1 GB | **rootfs（我们刷的 Armbian）** |
| `/dev/backup` | p8 | 2465792 | 2097152 | 1 GB | 原厂系统备份（**已销毁**） |
| `/dev/instaboot` | p9 | 4562944 | 1048576 | 512 MB | overlay upper |
| `/dev/misc` | p10 | 5611520 | 65536 | 32 MB | misc（DTB 的，覆盖 p3） |
| `/dev/cache` | p11 | 5677056 | 1048576 | 512 MB | 缓存（**现用作 /var/log**） |
| `/dev/data` | p12 | 6725632 | 7340032 | 3.5 GB | 数据（**现用作 /srv/extra**） |
| `/dev/xyagent` | p13 | 16777216 | 227491840 | 108.5 GB | **主数据盘** |

> **重要**：设备节点是 devtmpfs 里的**友好名**（`/dev/system`、`/dev/backup` 等），
> **没有 `/dev/mmcblk0pN` 节点**（`ls /dev/mmcblk0p*` → No such file or directory）。
> 因为 p8+ 不是内核通过标准机制创建的分区。

### 各分区的 UUID（原厂状态）

```
/dev/backup:    UUID="44c46c83-175a-408a-b9a2-5be581862bb8" TYPE="ext4"
/dev/cache:     UUID="56bd8e51-895e-4d2f-8a76-fb935d007895"
/dev/data:      UUID="238ff19c-9ac1-4f87-9968-1e292befab51"
/dev/misc:      UUID="17bd304e-54b0-49d0-8bbc-e9a5c9f23385"
/dev/instaboot: UUID="9427baca-5e8b-4d63-85c0-0dec2d4a6109"
/dev/system:    PARTUUID="614e0000-0000-4b53-8000-1d28000054a9"（无 TYPE，坏盘）
```

---

## U-Boot env 内容

进 buildroot 的 root shell 后（见 [03-uart-fiq.md](03-uart-fiq.md)），运行 `fw_printenv`：

```
baudrate=1500000
board=evb_rk3328
board_name=evb_rk3328
bootdelay=1
bootcmd=boot_android ${devtype} ${devnum};boot_fit;bootrkp;run distro_bootcmd;
boot_targets=mmc1 mmc0 usb0 pxe dhcp
cpu=armv8
second_image_load=off
second_rootfs_load=off
soc=rockchip
preboot=run checkotgmode;
checkotgmode=if test ${xl_softmode} = factory; then setenv otg_device 1;else setenv otg_device 0;fi;
format_flag=on
xl_softmode=factory
```

### 关键 env 变量含义

| 变量 | 默认值 | 作用 |
|---|---|---|
| `format_flag` | `on` | 是否格式化分区（见下方说明） |
| `second_rootfs_load` | `off` | `on` 时从 p8 而不是 p7 引导 |
| `second_image_load` | `off` | 备用镜像 |
| `xl_softmode` | `factory` | 工厂模式（会设 `otg_device=1`） |
| `preboot` | `run checkotgmode;` | 启动前执行的命令 |

### format_flag 的真实行为（重要，别被吓到）

`/init` 里的条件是：

```sh
if [ "$(blkid /dev/system | grep ext4)" = "" -o "${format_flag}" != "on" ]; then
    mkfs.ext4 -F /dev/system
fi
```

**`-o` 是逻辑或**。所以：

- 分区**已经是 ext4** + `format_flag=on` → 第一个条件假，第二个条件假 → **不格式化** ✅
- 分区**不是 ext4** → 第一个条件真 → **格式化**
- `format_flag` **不是 on** → 第二个条件真 → 格式化

**结论**：只要你的分区已经是有效 ext4，`format_flag=on` **不会**格式化它。

---

## /init 完整逻辑

这是原厂 initramfs 的主程序，7974 字节，理解它是理解整个刷机原理的关键。

### 头部

```sh
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mount -t proc proc /proc
mdev -s

init="/sbin/init"
root="/dev/system"
rootfstype="ext4"

# watchdog
/sbin/watchdog -t 3 /dev/watchdog0
```

### env 修复

```sh
if [ -x /usr/sbin/fw_printenv ]; then
    ln -sf /usr/sbin/fw_printenv /usr/sbin/fw_repairenv
    result=$(fw_repairenv | grep "env repair result")
    if echo "$result" | grep -q "success"; then
        reboot -f       # env 修好了 → 重启
    else
        echo "kernel env no repair $result..."
    fi
fi
```

### A/B 双 rootfs 开关

```sh
init_root_by_env() {
    rootfs_flag=$(fw_printenv second_rootfs_load 2>/dev/null | awk -F'=' '{print $2}')
    if [ "${rootfs_flag}" = "on" ]; then
        root=/dev/backup
    else
        root=/dev/system
    fi
}
```

**这就是为什么改 U-Boot env 能切换引导分区**。

### 挂载 rootfs

```sh
mount_rootfs() {
    format_flag=$(fw_printenv format_flag 2>/dev/null | awk -F'=' '{print $2}')

    # misc / data / instaboot 检查并格式化
    misc_type=$(blkid /dev/misc | grep ext4)
    if [ "${misc_type}" = "" -o "${format_flag}" != "on" ]; then
        mkfs.ext4 -F /dev/misc
        mkdir -p /mnt/direct_lvm_mode
    fi
    # data、instaboot 同理

    # 清空 xyagent 前 64 MB（重要！）
    if [ -b /dev/xyagent ]; then
        dd if=/dev/zero of=/dev/xyagent bs=1M count=64
    fi

    fw_setenv format_flag on

    e2fsck -fy /dev/instaboot
    e2fsck -fy ${root}

    mount -t ext4 /dev/instaboot /high
    mount -t ext4 -o ro ${root} /low

    # overlay 升级开关
    if [ "$(fw_printenv xl_rootfs_upgrade | awk -F'=' '{print $2}')" = "true" ]; then
        rm -rf /high/otc_system /high/otc_workdir
        fw_setenv xl_rootfs_upgrade clear
    fi

    mount -t overlay overlay \
        -o lowerdir=/low,workdir=/high/otc_workdir,upperdir=/high/otc_system \
        /mnt
}
```

> ⚠️ **`dd if=/dev/zero of=/dev/xyagent bs=1M count=64`** —— 每次启动都会清空 p13 的前 64 MB。
> 我们实测验证过：文件系统建在 64 MB 之后是安全的，但**如果把 ext4 超级块放在最前面就会被擦掉**。
> 实际测试：格式化整个 p13 后，写入随机数据、重启、数据完好（因为 mkfs 把超级块放在 1 MB 处，日志区也在 64 MB 内... 实测确实存活，但要注意这个风险）。

### switch_root（决定性的一步）

```sh
init_root_by_env
mount_rootfs

if [ -x /mnt/sbin/init ]; then
    mount -t devtmpfs devtmpfs /mnt/dev
    mediafs_create /mnt/ dev/loop0
    umount /sys /proc /dev
    mediafs_mount /mnt/ dev/loop0
    exec switch_root -c /dev/console /mnt ${init}
else
    echo "Failed to switch root, dropping to a shell"
    exec /sbin/init      # → busybox login（buildroot 环境）
fi
```

**`if [ -x /mnt/sbin/init ]` 是我们的救援入口**：
如果 p7 是空的或没有可执行的 `/sbin/init`，就会掉进 buildroot 的 busybox shell，拿到 root 权限。

### mediafs 逻辑

```sh
mediafs_create() {
    # 创建 4 MB 的 /media 分区
    if [ ! -f "$1/root/media_ext4.img" ]; then
        dd if=/dev/zero of="$1/root/media_ext4.img" bs=4K count=1024
    fi
    losetup "$2" "$1/root/media_ext4.img"
    mke2fs -t ext4 -F "$2"
    e2fsck -fy "$2"
}

mediafs_mount() {
    mount -t ext4 "$2" "$1/media"
}
```

---

## 原厂 rootfs 分析（p8 backup）

我们用 `du -sh` 分析了 p8 里的原厂系统（**现在已删除**）：

```
总计 ~579 MB:
  usr/         270 MB
  pluginipk/   279 MB    ← 网心云的插件包
  thunder/      10 MB    ← 迅雷
  onecloud_base/ 7.6 MB
  lib/          7.5 MB
  sbin/         2 MB
  app/          1.4 MB
  bin/        905 KB
  etc/        533 KB
```

`/etc/os-release`：

```
NAME=Buildroot
VERSION=2017.02-00032-gc61ea215
ID=buildroot
VERSION_ID=2017.02
```

`/sbin/init -> ../bin/busybox`

**这是一个 2017 年的 Buildroot 系统**，跑网心云的 PCDN 客户端。没有 systemd，
`/init.d/` 里是 SysV 风格的启动脚本。

### 原厂服务

```
/etc/init.d/S02passwd       ← 启动时设 root 密码
/etc/init.d/S98factory      ← 工厂模式
```

`S02passwd` 的密码规则未破解（试了 15+ 个候选：`SN后4位+a311d2019`、`rk35662019` 等全部失败）。

---

## 为什么原厂 rootfs 不可用

我们尝试过直接进原厂系统（`mount /dev/backup`）：

1. **没有 SSH 服务器**（只有 telnet，端口 23，密码未知）
2. **没有 Samba**
3. **Buildroot 2017** —— 软件源早已失效，无法安装任何东西
4. **内核是定制的** —— 只能跑网心云的二进制
5. **Web API 全部需要 AES nonce 签名**

所以必须换成完整的 Linux 发行版。

---

## Rockchip 引导链

```
BootROM
   ↓ 读 sector 64（idbloader）
U-Boot SPL (DDR 初始化)
   ↓
U-Boot proper (bootcmd)
   ↓ 读 GPT，找 boot/kernel 分区
FIT image (kernel + dtb + initrd)
   ↓
Linux kernel
   ↓ 读 DTB 里的分区表定义
创建 p8–p13
   ↓
原厂 initramfs (/init)
   ↓ overlay + switch_root
ARMBIAN ROOTFS (/dev/system = p7)
```

**关键**：BootROM 有签名验证（`Raw SecureMode = 1`），所以不能直接刷 U-Boot。
但 `reset bootloader` 能绕过 —— 见 [03-uart-fiq.md](03-uart-fiq.md)。
