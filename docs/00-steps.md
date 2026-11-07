# 完整步骤清单

从头到尾的顺序，每一步都标了「需要什么」「怎么验证」「失败怎么办」。

这是给「我要照着做一遍」的人准备的。原理和细节在每个链接的文档里。

---

## 阶段 0：准备（不需要拆机）

| # | 步骤 | 工具 | 验证 | 失败处理 |
|---|---|---|---|---|
| 0.1 | 确认型号是 OEA / RK3318 / 1 GB / 128 GB | 看板号 `OEA-A-V1.0` | — | 型号不同则本教程不适用 |
| 0.2 | 买 CH340 串口模块，**跳线拨到 3.3V** | — | 模块上有跳线帽 | 5V 会烧板子 |
| 0.3 | 装 CH340 驱动 | Windows 自动 / 厂商驱动 | `Get-PnpDevice` 看到 `VID_1A86&PID_7523` | 换 USB 口 |
| 0.4 | 装 .NET 8 SDK | https://dotnet.microsoft.com | `dotnet --version` | — |
| 0.5 | 装 Cygwin + e2fsprogs | https://cygwin.com | `dumpe2fs -V` | 见 [05-rootfs.md](05-rootfs.md) |
| 0.6 | 下载 Armbian RK3318 镜像 | armbian.com | 约 3.7 GB | 用国内镜像 |

---

## 阶段 1：接串口，确认活着

| # | 步骤 | 命令 | 期望 |
|---|---|---|---|
| 1.1 | 接线 | GND→GND, RX→TX, TX→RX, **VCC 不接** | 见 [01-hardware.md](01-hardware.md) |
| 1.2 | 找 COM 口 | 设备管理器 | 通常是 COM5 |
| 1.3 | 扫波特率 | `.\baudscan.ps1` | 1500000 有可读输出 |
| 1.4 | 监听 | `.\ser.ps1 -Seconds 10` | 看到启动日志或 getty 提示 |

**如果串口完全没输出**：不是接线问题就是板子没上电。用 `ping 10.9.8.77` 判断板子是否其实在跑。

---

## 阶段 2：进 FIQ，切 fastboot

| # | 步骤 | 命令 | 期望 |
|---|---|---|---|
| 2.1 | 进 FIQ | `.\ser.ps1 -Cmd "fiq"` | `Welcome to fiq debugger mode` + `debug>` |
| 2.2 | 看内核日志 | `.\ser.ps1 -Cmd "kmsg"` | 内核日志全文 |
| 2.3 | 切 fastboot | `.\ser.ps1 -Seconds 15 -Cmd "reset bootloader"` | `enter fastboot!` + `Enter fastboot...OK` |
| 2.4 | 确认 USB 设备 | `Get-PnpDevice \| ? InstanceId -like "*18D1*"` | `VID_18D1&PID_D00D` |

**如果 `fiq` 没反应**：内核可能没跑（在 U-Boot 阶段），等 3 秒 autoboot。

---

## 阶段 3：绑 WinUSB，编译 fbc

| # | 步骤 | 命令 | 期望 |
|---|---|---|---|
| 3.1 | 绑 WinUSB | Zadig 或注册表 | 设备管理器里是 `WinUSB device` |
| 3.2 | 编译 | `cd scripts\windows\fbc && dotnet build -c Release` | `fbc.exe` |
| 3.3 | 测试 | `.\bin\Release\net8.0\fbc.exe getvar:version` | `OKAY0.4` |
| 3.4 | 读上限 | `... getvar:max-download-size` | `OKAY0x04000000`（64 MB） |

**如果 `OPEN FAIL 2`**：设备不在 fastboot 模式，或 WinUSB 没绑对。

---

## 阶段 4：进 buildroot 救援 shell

| # | 步骤 | 命令 | 期望 |
|---|---|---|---|
| 4.1 | 造阻断镜像 | `mke2fs -q -F -t ext4 -L shellstub -d empty_dir stub32.img 32768` | 32 MB，无 `/sbin/init` |
| 4.2 | 写入 | `.\fbc.exe flash rootfs stub32.img` | `RESP3: OKAY` |
| 4.3 | 重启 | `.\fbc.exe reboot` | `OKAY` |
| 4.4 | 串口看 | `.\ser.ps1 -Seconds 40` | `Failed to switch root, dropping to a shell` + `buildroot login:` |
| 4.5 | 登录 | `.\keys.ps1 -Seq "root"` | `root@buildroot:/root#`（空密码） |

**这一步是整个项目的转折点** —— 拿到完全 root 权限的 shell，所有分区可读写。

---

## 阶段 5：配网络，传镜像

| # | 步骤 | 在哪 | 命令 |
|---|---|---|---|
| 5.1 | 网线直连 PC | 物理 | 板子 ↔ PC |
| 5.2 | PC 配静态 IP | Windows | `10.9.8.10/24` |
| 5.3 | PC 起 HTTP | PC | `powershell -File httpsrv.ps1 -Port 8899 -Root <镜像目录>` |
| 5.4 | 板子配 IP | 板子 | `ifconfig eth0 10.9.8.77 netmask 255.255.255.0 up` |
| 5.5 | 测试连通 | 板子 | `ping -c 2 10.9.8.10` |
| 5.6 | 测试 HTTP | 板子 | `wget -O /dev/null http://10.9.8.10:8899/index.txt` |

---

## 阶段 6：构建 rootfs（PC 上做）

这是一整条流水线，详见 [05-rootfs.md](05-rootfs.md)。

| # | 步骤 | 脚本 | 产物 |
|---|---|---|---|
| 6.1 | 提取 rootfs 分区 | `dd if=armbian.img of=armroot.img bs=1M skip=528 count=2999` | `armroot.img` 3.1 GB |
| 6.2 | 导出文件树 | `debugfs -R "rdump / rootfs_out" armroot.img` | `rootfs_out/` |
| 6.3 | 裁剪 | `./rootfs/mk1g.sh` | 815 MB |
| 6.4 | 记录符号链接 | `find rootfs_out -type l -printf '%p\t%l\n' > symlinks.tsv` | 3189 行 |
| 6.5 | 建壳 | `./rootfs/mkfs1g.sh` | `newrootfs.img` 1 GiB |
| 6.6 | 重建符号链接 | `debugfs -w -f sym_cmds.txt final.img` | 3189 个 link |
| 6.7 | 恢复权限 | `debugfs -w -f meta_cmds.txt final.img` | 71157 条 sif |
| 6.8 | **修 PAM** | `debugfs -w -f fix-pam.txt final.img` | 删掉 `pam_lastlog.so` |
| 6.9 | 定制 fstab/getty | 见 [05-rootfs.md](05-rootfs.md) | — |
| 6.10 | 验收 | `e2fsck -fy final.img` | 无错 |

**6.8 是最关键的一步** —— 不删那行 `pam_lastlog.so`，系统能启动但永远登录不了。

---

## 阶段 7：写入 p7

| # | 步骤 | 在哪 | 命令 |
|---|---|---|---|
| 7.1 | 把镜像放进 HTTP 目录 | PC | `cp final.img http/` |
| 7.2 | 板子配网络（如果重启过） | 板子 | `ifconfig eth0 ...` |
| 7.3 | 流式写入 | 板子 | `(wget -O - http://10.9.8.10:8899/final.img \| dd of=/dev/system bs=1M conv=fsync) &` |
| 7.4 | 等完成 | 板子 | `[1]+ Done`，约 60 秒 |
| 7.5 | 校验 | 板子 | `blkid /dev/system` 有 `TYPE="ext4"` |
| 7.6 | **清 overlay upper** | 板子 | `umount -l /mnt; umount -l /low; rm -rf /high/otc_system /high/otc_workdir; mkdir -p /high/otc_system /high/otc_workdir` |
| 7.7 | 重启 | 板子 | `reboot -f` |

> ⚠️ **7.6 绝对不能省。** 少这一步，你的所有修改都不会生效（我们踩了两次）。

---

## 阶段 8：首次启动配置

串口会走 Armbian firstrun 交互：

| # | 提示 | 输入 |
|---|---|---|
| 8.1 | `Create root password:` | 你的密码（两次） |
| 8.2 | `Choose default system command shell:` | `1`（bash） |
| 8.3 | `Please provide a username:` | `nas` |
| 8.4 | `Create password:` | 你的密码（两次） |
| 8.5 | `Real name:` | 回车 |
| 8.6 | 时区 | `4`(Asia) → `11`(China) → `1`(Beijing) |

成功后：

```
 v26.11.0 for RK.Rk3318-Box running Armbian Linux 4.19.232-ab871
 IPv4:        (LAN) 10.9.8.77
 Memory usage: 17% of 950M    CPU temp: 48°C    Usage of /: 3% of 488M
 root@armbian:/#
```

---

## 阶段 9：装 SSH 公钥（摆脱串口）

```bash
# PC
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 -C "oea-nas"

# 板子（串口）
mkdir -p /root/.ssh
echo '<公钥>' > /root/.ssh/authorized_keys
chmod 700 /root/.ssh && chmod 600 /root/.ssh/authorized_keys

# PC 验证
ssh root@10.9.8.77 "hostname"
```

**从此不用再受串口登录的气。**

---

## 阶段 10：网络修复

详见 [07-network.md](07-network.md)。

| # | 步骤 | 命令 |
|---|---|---|
| 10.1 | 关 NetworkManager | `systemctl disable --now NetworkManager` |
| 10.2 | 关 systemd-resolved | `systemctl disable --now systemd-resolved` |
| 10.3 | 装 netup | `install -m 0755 netup.sh /usr/local/sbin/` |
| 10.4 | 装 service | `install -m 0644 netup.service /etc/systemd/system/` |
| 10.5 | 启用 | `systemctl daemon-reload && systemctl enable --now netup` |
| 10.6 | 验证 | `ip -4 -br addr` → `10.9.8.77/24` |
| 10.7 | 写 DNS | `resolv.conf` 三条 `nameserver` |
| 10.8 | 验证 | `getent hosts baidu.com` |

**接上路由器后**：`netup.sh` 会自动 DHCP，得到第二个地址（如 `192.168.101.33/24`），
两个地址并存。

---

## 阶段 11：主机名 + mDNS + WSD

```bash
hostnamectl set-hostname oeanas
sed -i 's/^127\.0\.1\.1.*/127.0.1.1\toeanas/' /etc/hosts

apt-get install -y --no-install-recommends avahi-daemon libnss-mdns wsdd2

systemctl enable --now avahi-daemon
systemctl enable --now wsdd2

# 验证
getent hosts oeanas.local          # 板子自己
ping oeanas.local                  # PC
```

---

## 阶段 12：apt 换源 + 装包

```bash
# 换源
sed -i 's|http://ports.ubuntu.com/|http://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports/|g' \
    /etc/apt/sources.list.d/ubuntu.sources
mv /etc/apt/sources.list.d/armbian.sources{,.disabled}
mv /etc/apt/sources.list.d/armbian-config.sources{,.disabled}

# 修时间（没 RTC，时间可能离谱，会导致仓库签名报 "not valid yet"）
date -u -s '2026-11-07 00:36:25'      # 用 PC 的 UTC 时间
/usr/sbin/fake-hwclock save

# 更新
apt-get update

# 装包（完整清单见 A-packages.md）
apt-get install -y --no-install-recommends \
    samba samba-common-bin smbclient \
    avahi-daemon libnss-mdns wsdd2 \
    mergerfs fuse3 \
    dhcpcd-base chrony tcpdump
```

> ⚠️ **`apt-get update` 会下载 329 MB 索引，会把 488 MB 的 `/` 写满。**
> 必须先做阶段 13 的 apt 缓存外挂，或者临时 `rm -rf /var/lib/apt/lists/*`。

---

## 阶段 13：分区回收 + 挂载

详见 [09-storage.md](09-storage.md)。

| # | 步骤 | 命令 |
|---|---|---|
| 13.1 | 确认 p8 是原厂系统 | `mount -o ro /dev/backup /mnt/chk && cat /mnt/chk/etc/os-release` |
| 13.2 | 销毁 p8 | `dd if=/dev/zero of=/dev/backup bs=1M count=32; mkfs.ext4 -F -L oldsys-wiped /dev/backup` |
| 13.3 | 格式化 p11 | `mkfs.ext4 -F -L logfs /dev/cache` |
| 13.4 | 格式化 p12 | `mkfs.ext4 -F -L extra /dev/data` |
| 13.5 | 建挂载点 | `mkdir -p /mnt/d1 /mnt/d2 /mnt/d3 /srv/pool` |
| 13.6 | 建 data 子目录 | `mkdir -p /mnt/d1/data /mnt/d2/data /mnt/d3/data` |
| 13.7 | 搬家 | `mv /mnt/d1/{share,media,backup} /mnt/d1/data/` |
| 13.8 | 装 mergerfs | `apt-get install -y mergerfs` |
| 13.9 | 开 allow_other | `echo user_allow_other >> /etc/fuse.conf` |
| 13.10 | 写 fstab | 见 [09-storage.md](09-storage.md) |
| 13.11 | 回收预留空间 | `tune2fs -m 1 /dev/xyagent; tune2fs -m 1 /dev/data; tune2fs -m 1 /dev/backup` |
| 13.12 | 验证 | `df -h /srv/pool` → `111G 109G` |

> ⚠️ **不要动 p9（instaboot）** —— 它是 overlay 的 upperdir。
> **不要动 p10（misc）** —— `/init` 拿它判断是否需要格式化。

---

## 阶段 14：Samba

详见 [08-samba.md](08-samba.md)。

| # | 步骤 | 命令 |
|---|---|---|
| 14.1 | 禁用 AD-DC | `systemctl disable samba-ad-dc.service` |
| 14.2 | 写 smb.conf | 见 [08-samba.md](08-samba.md) |
| 14.3 | 测配置 | `testparm -s` |
| 14.4 | 建用户 | `smbpasswd -a nas` + `smbpasswd -e nas` |
| 14.5 | 设权限 | `chmod 777 /srv/pool` |
| 14.6 | 启用 | `systemctl enable --now smbd nmbd` |
| 14.7 | 板端测 | `smbclient -L //127.0.0.1 -U nas%密码 -m SMB3` |
| 14.8 | Windows 测 | `net use N: \\oeanas.local\nas /user:nas 密码 /persistent:yes` |

---

## 阶段 15：重启验证

```bash
reboot
```

等 60 秒，然后：

| 检查 | 命令 | 期望 |
|---|---|---|
| 系统状态 | `systemctl is-system-running` | `running` |
| 失败单元 | `systemctl --failed` | `0 loaded units` |
| 网络 | `ip -4 -br addr` | 两个地址 |
| 存储 | `df -h /srv/pool` | `111G ... 109G` |
| Samba | `systemctl is-active smbd nmbd` | `active` |
| mDNS | `getent hosts oeanas.local` | 有 IP |
| 共享 | `smbclient -L //127.0.0.1 -U nas%密码` | 列出 `nas` |

**全部通过 = 完成。**

---

## 阶段 16（可选）：Windows 网络发现

如果需要资源管理器「网络」里看到 NAS：

1. 板子侧已经装好 `wsdd2` + `nmbd`（阶段 14）
2. **Windows 侧必须把网络配置文件从「公用」改成「专用」**

```
设置 → 网络和 Internet → 以太网 → 网络配置文件 → 专用
```

或者（需要管理员）：

```powershell
Set-NetConnectionProfile -InterfaceIndex 20 -NetworkCategory Private
```

> 这一步我们**没法自动化** —— agent 进程没有管理员权限，
> `Set-NetConnectionProfile` 报 `not running PowerShell elevated`。

---

## 时间轴（两个会话的大致分布）

| 阶段 | 大致在哪 |
|---|---|
| 0–3（侦察、三条死路、FIQ、fastboot） | 迁移前的会话 |
| 4（意外进 buildroot shell，b58） | 迁移前，会话末尾 |
| 5–7（rootfs 构建、写入、overlay 陷阱） | 迁移后的会话，前半 |
| 8–10（首次启动、SSH、网络） | 迁移后，中段 |
| 11–16（Samba、分区回收、mergerfs、验证） | 迁移后，后半 |

---

## 一句话总结

> **保住原厂内核（FIQ 通道），用 fastboot 写一个 32 MB 的空盘骗出 buildroot shell，
> 然后用 HTTP + dd 把自建的 1 GiB Armbian 写进 p7，最后清 overlay upper 重启。**

其余的（网络、Samba、mergerfs）都是普通 Linux 运维工作。
