# 把网心云 OEA 改造成 111 GB 本地 NAS

> RK3318 / 1 GB RAM / 128 GB eMMC 的小盒子，从「原厂锁死」到跑 Armbian + Samba + 一个 111 GB 共享盘，
> 全程只用一根串口线和一根网线。

---

## 这是什么

网心云 OEA 是一台 RK3318 的「矿机」盒子，原厂系统锁得很死：

- U-Boot **命令表被清空**（88 条命令全部 `Unknown command`）
- **Rockusb 被主动拒绝**（`Can't do_rkusb by onething...`）
- **CPU 有 secure mode 锁**（`Raw SecureMode = 1`）
- **没有 SD 卡槽**，eMMC 焊死
- eMMC 上没有可短接的 maskrom 测试点

网上关于它的资料几乎为零。这个项目记录了把它完整改造成家用 NAS 的全过程，
包括**所有失败路线** —— 这部分可能比成功的部分更有价值。

### 最终成果

| 项目 | 结果 |
|---|---|
| 系统 | Armbian 26.11 (Ubuntu 24.04 noble) |
| 内核 | 原厂 4.19.232（**故意保留**，见下文） |
| 内存 | 950 MB 可用 |
| 存储 | 单一大目录 **111 GB**（mergerfs 合并三个分区） |
| 可用空间 | **108.8 GB**（`116,849,790,976` 字节） |
| 共享协议 | SMB2/SMB3（含 Windows 11 25H2 强制签名支持） |
| 网络发现 | NetBIOS + mDNS + WSD（Windows 资源管理器可见） |
| 网络 | 有线百兆，DHCP + 静态双地址并存 |
| 功耗 | 约 3 W（待机） |
| 访问地址 | `\\oeanas.local\nas` / `\\192.168.101.33\nas` |

---

## 为什么保留原厂内核

这是整个项目最关键的一个决定。

原厂内核 `4.19.232-ab871` 编译时带了 Rockchip 的 **FIQ debugger**，在 `/dev/ttyFIQ0` 上提供：

- 一个**永远可用**的调试控制台（`debug>` 提示符）—— 跑在 FIQ 上下文，不依赖内核调度器
- `kmsg` 命令能直接 dump 内核日志（网络挂了、SSH 连不上时唯一的信息来源）
- `reset loader` / `reset bootloader` 能强制切换 USB 模式
- `console` 能把它还给内核 getty

换成 Armbian 官方 6.18 内核后，`fiq_debugger` 和 `ttyFIQ0` **全部消失**：

```bash
$ grep -r 'fiq_debugger' /usr/lib/modules/6.18.*/
（空）
```

而**原厂 boot 分区读不出来**（fastboot 没有读命令），一旦刷掉就无法恢复。

> 所以我们保留原厂 kernel/boot 分区，**只替换 rootfs**。这块板子只有 1 GB 内存，4.19 内核完全够用。

---

## 三条路，只有一条走通

### ❌ 路线 A：Maskrom + rkdeveloptool（标准 Rockchip 刷机法）

**为什么不行**：这块板子的 eMMC 在物理上找不到可短接的 CLK/D0 点。我们做了 9 倍放大的显微检查：

- eMMC 右侧 2×5 金孔阵列 —— **是无铜过孔**（via），不是测试点
- eMMC 左侧 3 个镀金测试孔 —— 短接无效
- `1V8 + GND` 组合 —— 无法进入

而且 U-Boot 报 `Raw SecureMode = 1`，CPU 有锁（参考 [ophub#3549](https://github.com/ophub/amlogic-s9xxx-armbian/issues/3549)）。

### ❌ 路线 B：Rockusb 直接写入

**为什么不行**：`VID_2207&PID_320C` 能被 `CreateFileW` 打开，但所有 `WriteFile` 都返回 err=6/87/31/121。
即使用 WinUSB 注册表直改绕过驱动层，`WinUsb_WritePipe` 仍然 err=31→121。

根因在原厂 U-Boot 里：

```
[lzj-i][do_rkusb-169]: Can't do_rkusb by onething...
```

它**主动拒绝**了 Rockusb 写入。

### ✅ 路线 C：FIQ debugger → Fastboot → 写 rootfs

这是唯一走通的路，也是本项目的核心。

```
串口 → FIQ debugger → reset bootloader → fastboot
                                      ↓
                          写「阻断镜像」(32 MB) → 重启
                                      ↓
                          buildroot shell (root, 空密码)
                                      ↓
                          ifconfig eth0 + wget | dd
                                      ↓
                          写 1 GiB Armbian rootfs 到 p7
                                      ↓
                          清 overlay upper → 重启
                                      ↓
                               Armbian 启动 ✅
```

---

## 整体架构

```
                    ┌──────────────────────────────────────┐
                    │  原厂 initramfs (/init)              │
                    │  - 挂载 p7 (system) 为 lowerdir      │
                    │  - 挂载 p9 (instaboot) 为 upperdir   │
                    │  - 组成 overlayfs 作为 /             │
                    └──────────────────────────────────────┘
                                      ↓
                    ┌──────────────────────────────────────┐
                    │  Armbian rootfs (写在 p7)            │
                    │  /sbin/init → systemd                │
                    │  原厂 4.19.232 内核（保留 FIQ）      │
                    └──────────────────────────────────────┘
                                      ↓
        ┌─────────────────┬───────────────┬─────────────────┐
        │  p13 xyagent    │  p12 data     │  p8 backup      │
        │  108 GB         │  3.4 GB       │  1 GB           │
        │  (原厂系统已销毁)                │                 │
        └─────────────────┴───────────────┴─────────────────┘
                    ↓ mergerfs 合并各自的 data/ 子目录 ↓
                    ┌──────────────────────────────────────┐
                    │  /srv/pool  = 111 GB 单一目录树      │
                    └──────────────────────────────────────┘
                                      ↓
                    ┌──────────────────────────────────────┐
                    │  Samba  [nas] → \\oeanas.local\nas    │
                    └──────────────────────────────────────┘
```

### 关键点：p8–p13 不在 GPT 里

它们是**内核 DTB 里写死的固定偏移**。这不是标准分区，`parted` / `gdisk` / `fastboot` 都碰不到它们。

内核启动日志：

```
[1.591551] mmcblk0: p1 p2 p3 p4 p5 p6 p7
[1.593993] parse_partitions_from_dtb parts:13
[1.597334] add_emmc_partition xyagent size = 0x001b1e800000
```

后果：
- `/dev/mmcblk0p13` **不存在**，只有 `/dev/xyagent`
- udev **不建** `/dev/disk/by-uuid/` 软链 → fstab 里不能用 UUID
- fastboot 只认 GPT 里的 7 个分区（p1–p7）

详见 [docs/02-recon.md](docs/02-recon.md) 和 [docs/09-storage.md](docs/09-storage.md)。

---

## 快速开始

### 你需要准备

| 东西 | 说明 | 大概价格 |
|---|---|---|
| 网心云 OEA | RK3318 / 1 GB / 128 GB 版本 | 二手 30–60 元 |
| CH340 串口模块 | **跳线必须拨到 3.3V** | 5 元 |
| 杜邦线 | 公对母 × 3 | 1 元 |
| 12V 电源 | 原装即可 | 附带 |
| 网线 | 1 根 | — |
| 电脑 | Windows / Linux / macOS 都行 | — |

> ⚠️ **绝对不要接 CH340 的 VCC** —— 板子自己供电，接了可能烧。

### 五步走

```powershell
# 0. 接串口（COM5 @ 1500000 8N1），确认能收到输出
.\scripts\windows\serial\ser.ps1 -Seconds 6

# 1. 进 FIQ debugger
.\scripts\windows\serial\ser.ps1 -Seconds 8 -Cmd "fiq"
# Welcome to fiq debugger mode
# debug>

# 2. 切换到 fastboot 模式
.\scripts\windows\serial\ser.ps1 -Seconds 15 -Cmd "reset bootloader"
# enter fastboot!
# Enter fastboot...OK

# 3. 编译并写入「阻断镜像」（触发救援 shell）
cd scripts\windows\fbc
dotnet build -c Release
.\bin\Release\net8.0\fbc.exe flash rootfs ..\..\..\stub32.img
.\bin\Release\net8.0\fbc.exe reboot

# 4. buildroot shell 里（串口登录，root 空密码），配网络 + 拉镜像
# ifconfig eth0 10.9.8.77 netmask 255.255.255.0 up
# (wget -O - http://10.9.8.10:8899/rootfs.img | dd of=/dev/system bs=1M conv=fsync) &

# 5. 清 overlay upper，重启
# umount -l /mnt; umount -l /low
# rm -rf /high/otc_system /high/otc_workdir
# reboot -f
```

详细步骤从 [docs/01-hardware.md](docs/01-hardware.md) 开始。

---

## 现成的 rootfs 镜像（不用自己构建）

如果你不想按 [docs/05-rootfs.md](docs/05-rootfs.md) 从头裁 rootfs，可以直接下载我们实际刷进板子的那份镜像：

**[Releases → v1.0-rootfs](https://github.com/qiushanlin4/oea-nas/releases/tag/v1.0-rootfs)**

| 文件 | 大小 | 说明 |
|---|---|---|
| `oea-nas-rootfs.img.xz` | 187 MB | 1 GiB ext4 rootfs 的 xz 压缩包 |
| `oea-nas-rootfs.img.xz.sha256` | 88 B | 压缩包校验 |
| `final4.sha256` | 77 B | 解压后原始镜像的校验 |

```bash
# 解压（得到 1073741824 字节的 final4.img）
xz -dk oea-nas-rootfs.img.xz
sha256sum -c final4.sha256

# 在 buildroot 救援 shell 里（PC 起 HTTP 服务，见 docs/06-flashing.md）
wget -O - http://<你的PC>:8899/final4.img | dd of=/dev/system bs=1M conv=fsync
```

> ⚠️ **安全提示（务必先做）**：镜像里是 Armbian 默认配置。
> 1. root 默认密码 `1234`，首次启动立刻 `passwd root` 改掉；
> 2. SSH 主机密钥是 Armbian 镜像预置的通用密钥，执行
>    `rm /etc/ssh/ssh_host_*_key*; ssh-keygen -A` 重新生成。
>
> 该镜像 FS UUID 为 `5fcbedc6-e842-4d14-b6b6-db38bee44d6d`，fstab 指向
> `PARTUUID=614e0000-0000`（即 p7 `/dev/system`），已含 `ttyFIQ0` 串口 getty、
> 无 `pam_lastlog`、netup.service 等修复。刷写与首次启动细节见
> [docs/06-flashing.md](docs/06-flashing.md) 和 [docs/10-troubleshooting.md](docs/10-troubleshooting.md)。

---

## 文档目录

| 文档 | 内容 |
|---|---|
| [00-steps.md](docs/00-steps.md) | ⭐ **完整步骤清单**（照着做一遍的顺序版） |
| [01-hardware.md](docs/01-hardware.md) | 硬件规格、串口接线、网络方案 |
| [02-recon.md](docs/02-recon.md) | 原厂系统侦察、13 个分区、U-Boot env、`/init` 逐行解析 |
| [03-uart-fiq.md](docs/03-uart-fiq.md) | 串口工具、FIQ debugger 全部命令、阻断镜像法 |
| [04-fastboot.md](docs/04-fastboot.md) | WinUSB 绑定、自写 fastboot 客户端、协议细节 |
| [05-rootfs.md](docs/05-rootfs.md) | 构建 1 GiB Armbian rootfs（符号链接/权限/PAM 三大坑） |
| [06-flashing.md](docs/06-flashing.md) | HTTP+dd 写入流程、**overlay upper 陷阱** |
| [07-network.md](docs/07-network.md) | 三重缺失排查、netup.sh、DHCP+静态双地址、DNS |
| [08-samba.md](docs/08-samba.md) | smb.conf 全文、Windows 11 兼容、网络发现 |
| [09-storage.md](docs/09-storage.md) | 分区回收、mergerfs 合并、ext4 预留回收 |
| [10-troubleshooting.md](docs/10-troubleshooting.md) | 按症状组织的故障排查 |
| [A-packages.md](docs/A-packages.md) | 完整的系统包清单与版本 |
| [B-deadends.md](docs/B-deadends.md) | 13 条死路的详细记录 |

---

## 走过的所有死路（省你时间）

| 尝试 | 结果 | 原因 |
|---|---|---|
| U-Boot `help` / `ums` / `saveenv` | `Unknown command` | 命令表被清空，88 条全部无效 |
| Maskrom 短接 | 进不去 | eMMC 上没有可用测试点 |
| `rkdeveloptool db/wl` | 失败 | BootROM 拒绝，`Raw SecureMode = 1` |
| Rockusb 写入 | err=6/87/31/121 | 原厂 U-Boot 主动拒绝 |
| `fastboot flash backup` | `FAILcannot find partition` | fastboot 只暴露 7 个分区名 |
| `reset ums` | 被忽略 | boot mode = None |
| `reset recovery` | `No resource partition` | 没有 recovery 分区 |
| Web API 命令注入 | 无 | 需要合法参数 + AES nonce |
| root 密码爆破 | 失败 | 规则未知，试了 15+ 候选 |
| U-Boot env 注入 `preboot` | 无效 | `preboot` 走 `run_command()`，命令表空 |
| 换 Armbian 6.18 内核 | 放弃 | 失去 `ttyFIQ0`，且无法备份原厂 boot |
| SD 卡 / USB 启动 | 不可用 | 无卡槽；OTG 未引出 |
| 路由器 API 查设备列表 | 404 | 荣耀 XD28 的接口全加密（RSA 整页） |

完整记录见 [docs/B-deadends.md](docs/B-deadends.md)。

---

## 项目结构

```
.
├── docs/                    # 完整技术文档（00–10 + A 包清单 + B 死路）
├── scripts/
│   ├── windows/
│   │   ├── serial/          # 串口工具（ser.ps1 / keys.ps1 / baudscan.ps1）
│   │   └── fbc/             # 自写 fastboot 客户端（C# / .NET 8）
│   ├── pc/                  # HTTP 服务器 + 代理（纯 PowerShell）
│   └── board/               # 板端脚本（netup.sh / netup.service / install.sh）
└── rootfs/                  # rootfs 构建脚本
    ├── ddroot.sh            # 提取 rootfs 分区
    ├── mk1g.sh              # 裁剪到 1 GiB
    ├── mkfs1g.sh            # 建 ext4 + 记录符号链接
    ├── walkall.sh           # dump 原始 uid/gid/mode
    ├── mkmeta.sh            # 生成 sif 命令
    ├── apply-fixes.sh       # 打补丁（PAM 等）
    ├── setfstab.sh          # 写 fstab
    └── patches/             # 补丁文件本体
```

所有脚本都从环境变量取路径（`WORK` / `IMG` / `SRC`），没有任何硬编码路径，
可以直接 `git clone` 下来在别人的机器上跑。

---

## 这份文档是怎么来的

整个改造经历了**两个长会话**，累计 1774 次工具调用。所有结论都来自实测，不是推测。

- 前半段（迁移前）：从零开始侦察硬件、尝试三条刷机路线、逆向原厂 `/init` 和 U-Boot
- 后半段（迁移后）：构建 rootfs、解决 PAM/网络/overlay 三大障碍、装 Samba、合并分区

文档里的每个「坑」都附了**原始报错文本**，方便你 grep 自己的日志对号入座。

---

## 已知限制

- **没有 WiFi**：硬件上没有 WiFi 芯片（`lsusb` 只有 root hub，`/sys/class/ieee80211/` 不存在）。
  只能用网线接路由器，然后通过局域网访问。
- **没有 HDMI**：视频输出未引出，所有操作必须走串口或网络。
- **1 GB 内存**：跑 Samba 约用 140 MB，不适合跑 Docker 虚拟机。
- **百兆网口**：理论 12.5 MB/s，实测 SMB 写入约 11 MB/s。
- **没有 RTC 电池**：断电后时间丢失，靠 `fake-hwclock` 恢复到最后关机时间，联网后 NTP 校正。
- **overlay 根分区只有 488 MB 可写**：必须把 `/var/log`、`/var/cache/apt`、`/var/lib/apt/lists`
  外挂出去，否则会被写满。

---

## 风险提示

**这个改造不可逆。**

- p7 被覆盖后，原厂系统无法恢复（我们销毁了 p8 的备份）
- 没有 maskrom，没有 SD 卡，没有 USB 启动
- 如果 boot 分区（p4/p5）被破坏，板子会变砖

我们在每一步都保留了串口救援通道（FIQ debugger + buildroot shell），
但**你必须先接好串口再动手**。

---

## 致谢

- [Armbian](https://www.armbian.com/) —— 提供 RK3318 的 rootfs
- [ophub/amlogic-s9xxx-armbian](https://github.com/ophub/amlogic-s9xxx-armbian) —— 关于 CPU 锁的线索
- [mergerfs](https://github.com/trapexit/mergerfs) —— 多盘合并
- 网心云社区 —— 分区表信息

## License

MIT —— 代码随便用，出问题自己负责。
