# 06 · 把镜像写进板子

## 两条写入路线

| 路线 | 速度 | 大小限制 | 适用 |
|---|---|---|---|
| A. fastboot（fbc） | 26 MB/s | **≤ 64 MB**（`max-download-size`） | 小镜像、env、boot |
| B. **HTTP + dd** | 19 MB/s | 无 | ⭐ **1 GiB rootfs** |

我们最终用的是 B。A 用来做「阻断镜像」触发救援 shell。

---

## 路线 A：fastboot 直接写

```powershell
# 1. FIQ → fastboot
.\ser.ps1 -Seconds 8  -Cmd "fiq"
.\ser.ps1 -Seconds 15 -Cmd "reset bootloader"

# 2. 写（文件必须 ≤ 64 MB）
cd fbc
.\bin\Release\net8.0\fbc.exe flash rootfs ..\stub32.img
# SENT 33554432 bytes in 1.2s (26.19 MB/s)
# RESP2: OKAY
# RESP3: OKAY

# 3. 重启
.\bin\Release\net8.0\fbc.exe reboot
```

**限制原因**：`fbc` 是「一次 download 会话发完整个文件」，而 fastboot 协议的
`download:<size>` 必须与后续发送字节数严格相等。设备报 `max-download-size 0x04000000`
（64 MB），超过就 FAIL。

> 理论上可以用 **sparse 镜像**（U-Boot 的 `fb_mmc.c:469` 支持
> `is_sparse_image()` 分支），我们早期试过 18 片 sparse 分片
> （`oea/sparse/p00.img` ~ `p15.img`，各 60 MB），`fastboot flash rootfs` 全部 OKAY，
> **但重启后系统没变** —— 因为 p7 只是 OTA 暂存区，这一点当时还没搞明白。
> 有了 `/init` 的知识后，其实 sparse 路线是可行的，只是没必要（HTTP 更快更简单）。

---

## 路线 B：HTTP + dd（推荐）

### 1. PC 端起 HTTP 服务器

不需要 IIS、不需要 Python、不需要管理员权限。纯 PowerShell：

```powershell
# scripts/pc/httpsrv.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\httpsrv.ps1 `
    -Port 8899 -Root "C:\path\to\images"
```

特性：
- 手写 HTTP/1.0 解析（`TcpListener`）
- 支持 `Range: bytes=` → 206 Partial Content
- 1 MB 缓冲流式发送，不占内存
- 单线程串行服务（够用）

验证：

```bash
curl --noproxy '*' -s http://127.0.0.1:8899/index.txt
```

> ⚠️ **`--noproxy '*'` 必须加**。我们机器上有个坏掉的 `HTTP_PROXY=http://127.0.0.1:80`
> 环境变量，不带这个参数 curl 会走代理然后 502。

### 2. 板子进入 buildroot shell

用「阻断镜像」触发（见 [03-uart-fiq.md](03-uart-fiq.md)）：

```bash
# PC 端生成 32 MB 空 ext4（没有 /sbin/init）
mkdir -p empty_dir
mke2fs -q -F -t ext4 -L shellstub -d empty_dir stub32.img 32768
# 32768 块 × 1024 = 32 MB
```

> 注意这里 `mke2fs` 的最后参数是**块数**，块大小默认 1024 → 32 MB。
> 如果要 4096 块大小写 `-b 4096 ... 8192`。

```powershell
.\fbc.exe flash rootfs stub32.img
.\fbc.exe reboot
```

重启后串口：

```
mount overlay /dev/system /dev/instaboot complete 0
initd_setup entry...
Failed to switch root, dropping to a shell
fatorymode = factory, S98 exit
buildroot login:
```

输入 `root`，空密码。

### 3. 配置网络

```bash
ifconfig eth0 10.9.8.77 netmask 255.255.255.0 up
# dmesg: rk_gmac-dwmac ff550000.ethernet eth0: Link is Up - 100Mbps/Full
ping -c 2 10.9.8.10
```

### 4. 流式写入

**不要先下载到 `/tmp`** —— 板子 `/tmp` 是 tmpfs，只有 **475 MB**，装不下 1 GiB。

直接管道：

```bash
(wget -O - http://10.9.8.10:8899/final4.img | dd of=/dev/system bs=1M conv=fsync) \
    > /tmp/dl.log 2>&1 &
```

- `-O -` 输出到 stdout
- `conv=fsync` 保证数据真正落盘（不然断电会丢）
- 后台跑，日志重定向（`wget` 会自己写 `wget-log`，所以 `dl.log` 里可能只有一行
  `Redirecting output to 'wget-log'.`）

监控进度：

```bash
ps                          # 看 wget / dd 还在不在
cat /proc/268/io            # rchar / wchar 字节数
```

实测：**19 MB/s**（跑满百兆网口），1 GiB 约 **55–70 秒**。

完成标志：shell 打出 `[1]+ Done`。

### 5. 校验

```bash
blkid /dev/system
# LABEL="rootfs" UUID="5fcbedc6-e842-4d14-b6b6-db38bee44d6d" TYPE="ext4"
#                                       ↑ 必须与你本地镜像的 UUID 一致
```

UUID 一致 = 写入成功。

---

## ⚠️ 陷阱：写完之后 `/dev/system` 还挂在 overlay 上

写完以后如果你直接 `mount /dev/system /tmp/x`，会遇到：

```
mount: /dev/system: Device or resource busy
```

或者更糟 —— 挂上了但看到的是**旧内容**：

```
EXT4-fs error: htree_dirblock_to_tree: bad entry in directory: rec_len % 4 != 0
```

**原因**：`/init` 在这个阶段已经把 p7 挂在 `/low` 作为 overlay 的 lowerdir，
内核缓存里还是旧的（stub 镜像的）元数据。

**正确步骤**：

```bash
sync
umount -l /mnt
umount -l /low
echo 3 > /proc/sys/vm/drop_caches     # ← 关键！清掉 inode/dentry 缓存
mkdir -p /tmp/v4
mount -t ext4 -o ro /dev/system /tmp/v4
ls /tmp/v4
```

（`-l` 是 lazy umount，因为 overlay 还引用着挂载点，普通 umount 会 busy。）

---

## 🚨 最大的坑：overlay upper 层会覆盖你的修改

**这个坑让我们白折腾了两个小时，镜像改了 5 版都不生效。**

### 现象

`final3.img`、`final4.img` 依次写进 p7，每次 `blkid` 校验 UUID 都对，
`mount` 进去 `cat` 文件内容也对 —— **但重启后行为完全没变**。
PAM 还是失败、iperf3 还在跑、ssh 还是起不来。

### 原因

`/init` 挂的不是 p7，而是：

```sh
mount -t overlay overlay \
    -o lowerdir=/low,upperdir=/high/otc_system,workdir=/high/otc_workdir \
    /mnt
```

- **lowerdir = `/low`** = p7（`/dev/system`）= 你写的 Armbian
- **upperdir = `/high/otc_system`** = p9（`/dev/instaboot`）

**overlayfs 的规则：upper 里的同名文件优先级高于 lower。**

而 `/high/otc_system/` 里残留着早期 factory 模式产生的旧文件：

```
/high/otc_system/etc/passwd            ← 旧的！会盖住 p7 里的
/high/otc_system/etc/shadow            ← 旧的！
/high/otc_system/etc/group
/high/otc_system/etc/gshadow
/high/otc_system/etc/machine-id
/high/otc_system/etc/ld.so.cache
/high/otc_system/etc/systemd/system/multi-user.target.wants/iperf3.service
/high/otc_system/etc/systemd/system/multi-user.target.wants/systemd-networkd.service
...
```

当时我们百思不得其解：`/high/otc_system/` 里明明**没有** `pam.d`，为什么 PAM 还失败？

答案是 **`passwd` / `shadow` 被覆盖了**。upper 里的 `/etc/passwd` root 那行是：

```
root:x:0:0::/root:/bin/bash
```

（注意 `::` —— GECOS 字段为空，和 p7 里的 `root:x:0:0:root:/root:/bin/bash` 不同）。
这种不一致会让 PAM 的行为异常。

### 正确做法

**每次写完 p7，都要清空 overlay upper**：

```bash
umount -l /mnt
umount -l /low
rm -rf /high/otc_system /high/otc_workdir
mkdir -p /high/otc_system /high/otc_workdir
sync
```

然后重启。重启后 `/high/otc_system` 里只剩 `etc/init.d/S02passwd`（`/init` 自己重建的）。

### 教训

> **p7 的内容会被 p9 上的 overlay upper 覆盖。任何对镜像的修改都必须同时清空 upper 才能生效。**

如果你以后还要改 p7，记住这个顺序：

```
写 p7 → umount → 清 upper → reboot
```

少一步都不行。

---

## 完整流程（复制粘贴版）

```bash
### PC 端 ###
export PATH="/c/cygwin64/usr/bin:/c/cygwin64/usr/sbin:$PATH"
cd /path/to/images
powershell -NoProfile -ExecutionPolicy Bypass -File ..\httpsrv.ps1 -Port 8899 -Root . &

# 生成阻断镜像
mkdir -p empty_dir
mke2fs -q -F -t ext4 -L shellstub -d empty_dir stub32.img 32768

# 串口切 fastboot + 写阻断镜像 + 重启
.\ser.ps1 -Seconds 8  -Cmd "fiq"
.\ser.ps1 -Seconds 15 -Cmd "reset bootloader"
cd ../fbc
.\bin\Release\net8.0\fbc.exe flash rootfs ../images/stub32.img
.\bin\Release\net8.0\fbc.exe reboot
```

```sh
### 板端（buildroot shell）###
root                              # 空密码登录
ifconfig eth0 10.9.8.77 netmask 255.255.255.0 up
ping -c 1 -W 2 10.9.8.10

# 写镜像
(wget -O - http://10.9.8.10:8899/final4.img | dd of=/dev/system bs=1M conv=fsync) \
    > /tmp/dl.log 2>&1 &
# 等待 [1]+ Done

# 校验
blkid /dev/system

# ⚠️ 清 overlay upper（必须！）
sync
umount -l /mnt
umount -l /low
rm -rf /high/otc_system /high/otc_workdir
mkdir -p /high/otc_system /high/otc_workdir

# 重启
reboot -f
```

---

## 为什么不用 fastboot 写 p8/p9/p12/p13

fastboot 只认识 GPT 里的 7 个分区：

```
$ fbc.exe getvar:partition-type:backup
FAILunknown variable
```

p8–p13 是**内核 DTB 写死的分区**（`parse_partitions_from_dtb parts:13`），
它们不在 GPT 里，fastboot 的 `fb_mmc.c` 通过 `part_get_info_by_name()` 查表，查不到就失败。

要写这些分区，**只能进 buildroot shell 用 `dd`**。

---

## 首次启动会发生什么

镜像写对以后，第一次启动会走 Armbian 的 firstrun 交互（通过串口）：

```
Create root password: ****
Repeat password: ****
Warning: Weak password        ← 我们输入的是 1234

Choose default system command shell:
  1) bash
  2) zsh
Enter your choice [1/2]: 1

Please provide a username: nas
Create password: *********
Repeat password: *********
Real name:                ← 直接回车

Dear Nas, your account nas has been created and is sudo enabled.

Please provide your timezone:
  4) Asia
...
  11) China
  1) Beijing Time
TZ='Asia/Shanghai'
```

最终 banner：

```
 v26.11.0 for RK.Rk3318-Box running Armbian Linux 4.19.232-ab871
 Packages:     Ubuntu stable (noble)
 IPv4:        (LAN) 10.9.8.77
 Memory usage: 17% of 950M    CPU temp: 48°C    Usage of /: 3% of 488M

 root@armbian:/#
```

**看到 IPv4 和内存数字时，你已经成功了。**

继续 → [07-network.md](07-network.md)
