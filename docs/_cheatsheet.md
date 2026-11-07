# 02-recon.md 里提到的关键点，单独抄一份方便对照

## 分区（出厂状态）

| # | 名字 | 起始扇区 | 大小(扇区) | 大小 | 说明 |
|---|---|---|---|---|---|
| 1 | uboot | 16384 | 8192 | 4 MB | U-Boot |
| 2 | trust | 24576 | 8192 | 4 MB | BL31 |
| 3 | misc/oemmisc | 32768 | 8192 | 4 MB | |
| 4 | boot | 40960 | 131072 | 64 MB | boot.img |
| 5 | kernel | 172032 | 131072 | 64 MB | |
| 6 | env | 303104 | 65536 | 32 MB | U-Boot env |
| 7 | rootfs/system | 368640 | 2097152 | 1 GiB | **我们写 Armbian 的地方** |
| 8 | backup | 2465792 | 2097152 | 1 GiB | 原厂 Buildroot（已销毁） |
| 9 | instaboot | 4562944 | 1048576 | 512 MB | **overlay upper，绝对不能动** |
| 10 | misc | 5611520 | 65536 | 32 MB | **不要动** |
| 11 | cache | 5677056 | 1048576 | 512 MB | → /var/log |
| 12 | data | 6725632 | 7340032 | 3.5 GB | → 合并池 |
| 13 | xyagent | 16777216 | 227491840 | 108.5 GB | → 合并池主力 |

p1–p7 在 GPT 里；p8–p13 由内核 DTB 直接写死。

## U-Boot env 里几个重要的值

```
bootdelay=1
bootcmd=boot_android ${devtype} ${devnum};boot_fit;bootrkp;run distro_bootcmd;
preboot=run checkotgmode;
second_image_load=off
second_rootfs_load=off        <- 设成 on 会让 /init 改用 /dev/backup 当根
format_flag=on
xl_softmode=factory
```

## /init 的两个关键分支

```sh
# 1) A/B 双 rootfs
rootfs_flag=`fw_printenv second_rootfs_load | awk -F'=' '{print $2}'`
if [ "${rootfs_flag}" = "on" ]; then root=/dev/backup; else root=/dev/system; fi

# 2) switch_root 失败就掉 shell —— 这就是我们的"救援入口"
if [ -x /mnt/sbin/init ]; then
    exec switch_root -c /dev/console /mnt ${init}
else
    echo "Failed to switch root, dropping to a shell"
    exec /sbin/init
fi
```

## overlay 结构

```
lowerdir = /low  = p7 (/dev/system)      <- 我们写的 Armbian
upperdir = /high/otc_system = p9         <- 可写层，优先级更高！
workdir  = /high/otc_workdir
```

**上行层里的文件会盖住下行层。** 这就是为什么改了镜像却不生效。
