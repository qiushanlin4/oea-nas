# 附录 A · 系统包清单

## 板端系统信息

```bash
$ uname -a
Linux oeanas 4.19.232-ab871 #1 SMP Fri Apr 11 17:25:17 CST 2025 XL-V2.1.3 aarch64 GNU/Linux

$ cat /etc/os-release
PRETTY_NAME="Armbian-unofficial 26.11.0-trunk noble"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION="24.04 LTS (Noble Numbat)"
ID=ubuntu
ID_LIKE=debian
UBUNTU_CODENAME=noble

$ free -m
               total        used        free      shared  buff/cache   available
Mem:             950         137         623           1         189         769
Swap:              0           0           0

$ df -h /
Filesystem      Size  Used Avail Use% Mounted on
overlay         488M  104M  350M  23% /
```

---

## 我们装的包（非 Armbian 基础）

以下全部通过 `apt-get install` 装的，是**实现 NAS 功能所必需**的最小集合。

### NAS 核心

| 包 | 版本 | 用途 |
|---|---|---|
| `samba` | `2:4.19.5+dfsg-4ubuntu9.8` | SMB/CIFS 服务端 |
| `samba-common` | `2:4.19.5+dfsg-4ubuntu9.8` | 配置文件和工具 |
| `samba-common-bin` | `2:4.19.5+dfsg-4ubuntu9.8` | `testparm` / `smbpasswd` |
| `samba-libs` | `2:4.19.5+dfsg-4ubuntu9.8` | 库 |
| `smbclient` | `2:4.19.5+dfsg-4ubuntu9.8` | 客户端测试工具 |
| `python3-samba` | 随 samba | Samba 的 Python 绑定 |
| `libldb2` | 依赖 | LDAP 数据库 |
| `libsmbclient0` | 依赖 | 客户端库 |

### 网络发现

| 包 | 版本 | 用途 |
|---|---|---|
| `avahi-daemon` | `0.8-13ubuntu6.2` | mDNS（`oeanas.local`） |
| `avahi-autoipd` | `0.8-13ubuntu6.2` | IPv4LL 支持 |
| `libavahi-core7` | `0.8-13ubuntu6.2` | Avahi 核心库 |
| `libavahi-client3` | `0.8-13ubuntu6.2` | 客户端库 |
| `libavahi-common3` | `0.8-13ubuntu6.2` | 公共库 |
| `libavahi-common-data` | `0.8-13ubuntu6.2` | 数据文件 |
| `libnss-mdns` | `0.15.1-4build1` | glibc 的 `.local` 解析插件 |
| `wsdd2` | `1.8.7+dfsg-1.1` | WSD（Windows 10/11 网络发现） |

### 存储

| 包 | 版本 | 用途 |
|---|---|---|
| `mergerfs` | `2.33.5-1ubuntu2` | 多盘合并成单一目录树 |
| `fuse3` | `3.14.0-5build1` | FUSE 用户态文件系统 |
| `libfuse3-3` | 依赖 | FUSE 库 |

### 网络

| 包 | 版本 | 用途 |
|---|---|---|
| `dhcpcd-base` | `1:10.0.6-1ubuntu3.2` | DHCP 客户端（常驻，自动适应网线插拔） |
| `chrony` | `4.5-1ubuntu4.2` | NTP 时间同步（无 RTC 时靠这个） |
| `tcpdump` | `4.99.4-3ubuntu4.24.04.1` | 抓包排查 |

### 系统

| 包 | 版本 |
|---|---|
| `systemd` | `255.4-1ubuntu8.18` |
| `libpam0g` | `1.5.3-5ubuntu5.7` |
| `libpam-modules` | `1.5.3-5ubuntu5.7` |
| `libpam-runtime` | `1.5.3-5ubuntu5.7` |

---

## 一键安装脚本

```bash
#!/bin/bash
# 装齐所有 NAS 需要的包
set -e

export DEBIAN_FRONTEND=noninteractive

# 1. 换源（国内加速；国外用户跳过这步）
sed -i 's|http://ports.ubuntu.com/|http://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports/|g' \
    /etc/apt/sources.list.d/ubuntu.sources
# Armbian 自己的源在国内 TLS 容易失败，先禁掉
mv /etc/apt/sources.list.d/armbian.sources /etc/apt/sources.list.d/armbian.sources.disabled 2>/dev/null || true
mv /etc/apt/sources.list.d/armbian-config.sources /etc/apt/sources.list.d/armbian-config.sources.disabled 2>/dev/null || true

# 2. 更新
apt-get update

# 3. 装包
apt-get install -y --no-install-recommends \
    samba samba-common-bin smbclient \
    avahi-daemon libnss-mdns \
    wsdd2 \
    mergerfs fuse3 \
    dhcpcd-base \
    chrony \
    tcpdump

# 4. 清理
apt-get clean
rm -rf /var/lib/apt/lists/*
```

### 关于 `--no-install-recommends`

**必须加。** 不加会多装几十个包（推荐包包括各种文档、字体、python 库），
我们的 `/` 只有 488 MB，装不下。

实测：`--no-install-recommends` 装完 Samba 全套只增加约 60 MB。

---

## 完整的 dpkg 包列表

板子共 **550 个包**。这里只列我们自己装的，其余是 Armbian 基础镜像自带的。

```bash
# 在板子上导出完整列表
dpkg-query -W -f='${Package}\t${Version}\n' | sort > /srv/pool/packages.txt
```

如果你要和自己的系统对比，用这个命令。

---

## Armbian 基础镜像信息

| 项目 | 值 |
|---|---|
| 版本 | Armbian-unofficial 26.11.0-trunk noble |
| 构建日期 | 2026-10 |
| 基础 | Ubuntu 24.04 LTS (Noble Numbat) |
| 内核 | 4.19.232-ab871（**原厂，不是 Armbian 自带**） |
| 架构 | aarch64 |
| 原始镜像 | `Armbian_26.11.0-trunk_Rk3318-box_noble_current_6.18.xx.img` |
| 原始大小 | 3,699,376,128 B |
| rootfs 分区偏移 | 553,648,128 B（528 MB） |
| rootfs 分区大小 | 3,144,679,424 B |

> **注意**：我们用的是 Armbian 的 **rootfs**，但 **kernel 保持原厂 4.19.232**。
> Armbian 自带的 6.18 内核**没有 fiq_debugger**，装上去就永久失去串口救援通道。

---

## PC 端工具

### Windows

| 工具 | 版本 | 用途 | 获取方式 |
|---|---|---|---|
| .NET SDK | 8.0+ | 编译 fbc | https://dotnet.microsoft.com/download |
| Cygwin | 任意 | e2fsprogs（mke2fs/debugfs） | https://cygwin.com/ |
| e2fsprogs | 1.44.5+ | 上面 Cygwin 包里的 | Cygwin 安装时选 |
| PowerShell | 5.1+ | 所有脚本 | Windows 自带 |
| OpenSSH 客户端 | 任意 | ssh | Windows 10+ 自带 |

**Cygwin 需要安装的包**（在 setup.exe 里搜索）：

```
e2fsprogs
util-linux
```

装完路径：

```bash
export PATH="/c/cygwin64/usr/bin:/c/cygwin64/usr/sbin:$PATH"
dumpe2fs -V
# dumpe2fs 1.44.5 (15-Dec-2018)
```

### Linux / macOS

```bash
# Debian/Ubuntu
sudo apt-get install e2fsprogs dotnet-sdk-8.0 openssh-client

# macOS
brew install e2fsprogs dotnet-sdk
```

macOS 上 e2fsprogs 是 keg-only，需要手动加 PATH：

```bash
export PATH="$(brew --prefix e2fsprogs)/sbin:$PATH"
```

---

## 版本兼容性说明

| 组件 | 我们用的 | 最低要求 | 说明 |
|---|---|---|---|
| Ubuntu | 24.04 noble | 22.04 | 更老的版本 Samba 可能不支持 SMB3_11 |
| Samba | 4.19.5 | 4.13+ | 4.13 起支持 SMB3_11 |
| systemd | 255 | 245+ | 需要 `x-systemd.requires-mounts-for` |
| mergerfs | 2.33.5 | 2.30+ | 更老版本 `category.create=mfs` 语法不同 |
| 内核 | 4.19.232 | **必须是原厂** | 见下 |

### 为什么内核必须是原厂 4.19.232

1. **fiq_debugger**：串口救援通道（`ttyFIQ0` + FIQ debugger）
2. **DTB 分区表**：`parse_partitions_from_dtb parts:13` 创建 p8–p13
3. **原厂 boot 分区无法备份**：fastboot 没有读命令，换了就回不去

换内核的代价：

- 失去 FIQ debugger → 系统挂了只能拆机短接（而短接这条路已证明无效）
- p8–p13 可能不再出现 → 108 GB 数据盘消失
- 无法回滚

**强烈建议保持原厂内核。**

---

## 磁盘空间预算

装完所有包后的实际占用：

```
/                      488M  total
├── /usr               825M  (只读下层，在 p7 里)
│   ├── /usr/bin
│   ├── /usr/lib
│   └── /usr/share
├── /var               
│   ├── /var/lib       444M  → apt 已 bind 到数据盘
│   ├── /var/cache     129M  → apt 已 bind 到数据盘
│   └── /var/log       1.7M  → 挂到 p11 (512M)
├── /etc               2.6M
├── /root               20K
└── /home               16K

可写层实际使用：104M / 488M (23%)
```

**关键**：`/usr` 在只读下层，不会占用可写层空间。真正会增长的是
`/var/lib`、`/var/cache`、`/var/log` —— 这三个都已外挂。
