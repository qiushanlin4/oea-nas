# 附录 B · 死路清单

这份清单记录了我们**试过但失败**的所有方案，以及失败的确切原因。

目的是让后来者不要重复我们的时间浪费。每一条都附了证据。

---

## 1. Maskrom 短接 ❌

### 尝试

Rockchip 的 Maskrom 模式是芯片级的恢复模式，理论上无法被锁。进入方法通常是短接
eMMC 的 CLK 或 D0 到 GND，然后上电。

### 我们的尝试

| 位置 | 方法 | 结果 |
|---|---|---|
| eMMC 右侧 2×5 金孔阵列 | 9 倍放大确认 | **只是无铜过孔**，不是测试点 |
| eMMC 左侧 3 个镀金测试孔 | 两两短接 | 无效 |
| `1V8` + `GND` | 短接 | 无效 |

### 为什么失败

**这块板子的 maskrom 测试点没有引出。**

而且 deeper 的问题：即使进了 maskrom 也没用 ——

```
Raw SecureMode = 1
```

CPU 有锁。GitHub 上 ophub 项目的 issue #3549 也有用户报告：
> 「CPU 也是有锁的」

**结论：不要浪费时间和烙铁在这上面。**

---

## 2. Rockusb 写入 ❌

### 尝试

`reset loader` 能让设备进入 Rockusb 模式：

```
VID_2207&PID_320C
```

这是 Rockchip 官方的低级刷机模式，理论上可以直接写整个 eMMC。

### 我们的尝试

| 方法 | 结果 |
|---|---|
| 官方 `RKDevTool v5.14` | 设备能识别，写入超时 |
| 官方 `RKDevelopTool v1.37` | 同上 |
| 自写 WinUSB 客户端 | `CreateFileW` 打开成功，`WriteFile` 返回 `err=6/87/31/121` |
| 注册表直改 WinUSB 后再试 | `WinUsb_WritePipe` 仍 `err=31 → 121` |
| 换 32 位/64 位客户端 | 同样失败 |

### 根因（U-Boot 日志原文）

```
[lzj-i][do_rkusb-169]: Can't do_rkusb by onething...
```

原厂 U-Boot **主动拒绝**了 Rockusb 写入。这是厂商故意设的限制。

`do_rkusb()` 函数里有条件判断，不满足就直接打印这句话然后返回。我们没有源码
（`rockusb.c` 我们下载到了，8818 字节，但那是通用实现，不是这个定制版）。

**结论：Rockusb 路线死路。唯一可行的是 fastboot。**

---

## 3. U-Boot 命令 ❌

### 尝试

Ctrl-C 能在 autoboot 期间停在 `=>` 提示符。然后我们试了 **88 条命令**：

```
help, ?, printenv, setenv, saveenv, reset, reboot,
ums 0 mmc 0, ums 0 mmc 1, mmc dev 0, mmc list,
sysboot, boot, bootm, booti, bootz, bootrkp,
fatls, ext4ls, ls, load, go, dhcp, tftp, ping,
env default -a, env import, env export,
fastboot usb 0, fastboot, rkusb, download, recovery,
...
```

### 结果

**全部返回**：

```
Unknown command '<cmd>' - try 'help'
```

包括 `help` 本身！

### 为什么

厂商在编译 U-Boot 时**移除了整个命令表**（`CONFIG_CMD_*` 全部 unset），
只保留了 C 代码里硬编码的路径。

`boot_mode.c` 里的 `run_command("fastboot usb 0", 0)` 能工作，
是因为 `fastboot` 功能被编译进了 C 代码（`fb_mmc.c`），
**不是**通过命令表调用的。

**结论：U-Boot 提示符没有任何用处，不要试图在 `=>` 下做任何事。**

---

## 4. env 注入 ❌

### 尝试

既然 U-Boot 命令表是空的，能不能通过 **U-Boot env** 注入命令？

`boot_mode.c` 里有：

```c
case BOOT_MODE_UMS:
    env_set("preboot", "setenv preboot; ums mmc 0");
    break;

case BOOT_MODE_LOADER:
    env_set("preboot", "setenv preboot; download");
    run_command("download", 0);
    break;
```

理论上设置 `preboot=setenv preboot; ums mmc 0` 就能启动 USB Mass Storage 模式，
把 eMMC 挂到 PC 上。

### 我们的尝试

1. 写了 `oea/envtool`（C# 工具，149 行），用标准 zlib CRC32 生成 U-Boot env 镜像：

```csharp
static uint Crc(byte[] b, int off, int len) {
    uint crc = 0xFFFFFFFFu;
    for (int i = off; i < off + len; i++) {
        crc ^= b[i];
        for (int j = 0; j < 8; j++)
            crc = (crc >> 1) ^ (0xEDB88320u & (uint)(-(int)(crc & 1)));
    }
    return crc ^ 0xFFFFFFFFu;
}
```

2. 用 fastboot 写进 env 分区（32 MB 一次写完，26 MB/s，`OKAY`）

3. 重启

### 结果

```
read_env crc from env error
read_env crc from backup env ok
*** Warning - bad CRC, using default environment
```

`bootdelay` 仍然是 1（我们写的是 10）→ **env 没生效**。

### 为什么

**我们生成的 CRC 格式不对。** 可能的原因：

- 环境区大小不是我们假设的 131072
- 有多个 env 副本（primary / redundant）
- 头部结构不同（Rockchip 定制版可能加了额外字段）

### 更深层的问题

**即使 env 注入成功，也没用。**

`preboot` 的执行路径最终会走 `run_command()`：

```c
run_command("download", 0);
```

而命令表是空的 → `Unknown command 'ums'` → 失败。

**所以 env 注入这条路从设计上就是死的。**

### 唯一可能的 C 级通道

`uboard.c:1478-1482`：

```c
env = env_get("sys_bootargs");
if (env) {
    env_update("bootargs", env);
    if (verbose)
        printf("## sys_bootargs: %s\n\n", env);
}
```

这是 **C 级追加内核 cmdline** 的通道，不经过命令表。

我们评估后没有使用（fastboot 写 p7 已经解决问题了）。如果你需要给内核加参数
（比如 `init=/bin/sh` 进单用户），这是唯一可行的 env 通道。

但前提是：**你得先把 env 写对**。

---

## 5. 直接写 p7 期望生效 ❌（部分成功）

### 尝试

```bash
fastboot flash rootfs final2.img
```

18 片 sparse 分片，每片 60 MB，26 MB/s，**全部 OKAY**。

重启。

### 结果

**仍然进原厂系统。**

### 为什么

当时我们以为 p7 是 rootfs，写进去就该生效。

后来读了 `/init` 才明白：

```sh
root="/dev/system"      # = p7
...
mount -t ext4 -o ro ${root} /low
mount -t overlay overlay -o lowerdir=/low,upperdir=/high/otc_system,... /mnt
```

**p7 确实被用了，但只是 overlay 的 lowerdir。**
如果 upperdir 里有旧文件，它们会盖住 p7 的内容。

而且当时 p7 里的镜像本身有问题（缺 `pam_lastlog.so`），
即使 overlay 正常也会 PAM 失败。

**这个「失败」其实是成功的铺垫** —— 它让我们发现了 overlay 机制。

---

## 6. fastboot 写 p8/p9 ❌

### 尝试

```bash
fastboot flash backup p8.img
fastboot flash instaboot p9.img
```

### 结果

```
getvar:partition-type:backup      → FAILunknown variable
getvar:partition-type:instaboot   → FAILunknown variable
flash backup                      → RESP3: FAILcannot find partition
```

### 为什么

fastboot 只认识 **GPT 里的 7 个分区**（p1–p7）。
p8–p13 是内核 DTB 写死的，不在 GPT 里，fastboot 的 `fb_mmc.c`
通过 `part_get_info_by_name()` 查表，查不到就失败。

**只能进 buildroot shell 用 `dd`。**

---

## 7. `reset recovery` ❌

### 尝试

```
debug> reset recovery
```

期望进入 recovery 模式（可能能挂载 p7 并修复）。

### 结果

```
No resource partition
Failed to load DTB, ret=-19
```

### 为什么

`misc_require_recovery()` 要求 BCB（Bootloader Control Block，在 p10 misc 里）：

```
command == "boot-recovery"
recovery == "recovery\n--rk_fwupdate\n"     → BCB_MSG_RECOVERY_RK_FWUPDATE
recovery == "recovery\n--factory_mode=whole\n" → BCB_MSG_RECOVERY_PCBA
```

我们的 misc 分区里没有这些内容，所以 recovery 不可用。

理论上可以写 BCB 进去触发，但：

1. `recovery` 分区本身不存在（`No resource partition`）
2. 即使触发 `BCB_MSG_RECOVERY_PCBA`（factory mode），也只是我们后来走通的那条路

**结论：不用绕这个弯，直接走 factory mode（阻断镜像）更简单。**

---

## 8. `reset ums` ❌

### 尝试

```
debug> reset ums
```

期望把 eMMC 作为 USB 大容量存储设备挂到 PC 上。

### 结果

**被忽略**，日志显示 `boot mode: None`。

### 为什么

`BOOT_MODE_UMS` 分支设置 `preboot` 后**没有 `run_command`**：

```c
case BOOT_MODE_UMS:
    env_set("preboot", "setenv preboot; ums mmc 0");
    break;
```

它只是设置了一个字符串，真正的执行在别处（下次 autoboot 时 `preboot` 会被执行），
而命令表是空的 → 失败。

---

## 9. 通过 Web API 拿 root ❌

### 尝试

板子原厂系统跑着 8080 / 18080 端口的 Web 服务。我们试图：

1. 找命令注入漏洞
2. 找默认密码规则
3. 绕过 AES nonce 验证

### 结果

| 尝试 | 结果 |
|---|---|
| 所有写接口 | 需要合法参数 + AES nonce，**无注入点** |
| root 密码 `SN后4位 + a311d2019`（OEC 规则） | FAIL |
| root 密码 `SN后4位 + rk35662019` | FAIL |
| 15+ 个其他候选 | 全 FAIL |
| ssh / telnet | 服务存在，密码未知 |

### 为什么

原厂系统启动时会跑 `passwd root`，但**不是每次**（看启动模式）。
密码规则未知，不是常见的 OEC/OEA 规则。

**结论：软件面是排空的，不要在上面浪费时间。**

---

## 10. SD 卡启动 ❌

### 尝试

很多 Rockchip 板子支持从 SD 卡启动（跳过 eMMC）。

### 结果

**这块板子没有 SD 卡槽。**

用户确认：
> 「没有，我仔细看过了」

### 类似：USB 启动

`boot_targets=mmc1 mmc0 usb0 pxe dhcp`

理论上支持 `usb0`，但：

1. USB-A 口挂的是 `dwc2 ff580000.usb`（OTG，设备模式）
2. A-A 线插上 PC 后无反应（设备端 OTG 未引出）
3. `lsusb` 只有 root hub

**结论：USB 启动不可用。**

---

## 11. 换 Armbian 官方内核 ❌

### 尝试

用 Armbian 6.18 内核（`vmlinuz-6.18.59-ophub` 43,874,816 B + `uInitrd` 18,019,564 B）

### 结果

**否决，没有实际刷入。**

### 为什么

1. **Armbian 6.18 内核里 grep 不到 `fiq_debugger` / `ttyFIQ`**

```bash
$ grep -r 'fiq_debugger' /usr/lib/modules/6.18.*/ 2>/dev/null
（空）
$ strings vmlinuz-6.18.59-ophub | grep -i ttyfiq
（空）
```

装上去 = **永久失去串口救援通道**。

2. **原厂 boot 分区读不出来** —— fastboot 没有读命令，`dd if=/dev/boot` 在
   buildroot shell 里能读，但那时 boot 分区可能已经被覆盖。

所以**无法备份原厂内核**，换了就回不去。

3. 两个文件合计 61,894,380 B，boot 分区只有 67,108,864 B —— **能塞进去，但没有余量**。

**结论：必须保留原厂 4.19.232 内核。**

---

## 12. 直接 dd 原厂完整镜像 ❌

### 尝试

```bash
dd if=armbian-rk3318.img of=/dev/xyagent
```

### 结果

没有实际执行（被我们自己否决）。

### 为什么

1. `/dev/xyagent` 是我们的数据盘，写进去会毁掉 108 GB 空间
2. 原厂镜像 3.7 GB，xyagent 有 108 GB，**物理上放得下**，但：
   - **没有机制能从 xyagent 启动**（boot 分区在 p4，U-Boot 只从 p4/p5 读内核）
   - `boot_targets` 里没有 p13
3. 需要改 `/init` 里的 `root=` 参数，而 `/init` 在 initramfs 里（uInitrd），
   要重新打包 —— 我们没试

**结论：理论上可行但工程量大，不如直接写 p7。**

---

## 13. 通过重启进 factory mode 获得 shell（意外成功）

这不是死路，但值得记录，因为**它是我们获得 root shell 的方式**。

### 意外触发

我们本来在做 env 注入实验（写了一个 canary 到 env 分区），结果**意外触发了
factory 模式**：

```
[lzj-d][check_reset_key-450]: factory_mode = 1, check_cnt = 20
```

cmdline 里 `otg_device=1`（原为 0）。

然后：

```
mount /dev/instaboot now.
EXT4-fs (mmcblk0p9): mounted
mount overlay /dev/system /dev/instaboot start
mount: mounting /dev/system on /low failed: Invalid argument
initd_setup entry...
S02passwd
Failed to switch root, dropping to a shell
fatorymode = factory, S98 exit
buildroot login:
```

**我们得到了 root shell（空密码）。**

### 后来可控化

不用靠运气 —— 用「阻断镜像」（32 MB 空 ext4，没有 `/sbin/init`）就能稳定触发：

```bash
mke2fs -q -F -t ext4 -L shellstub -d empty_dir stub32.img 32768
fastboot flash rootfs stub32.img
fastboot reboot
```

`/init` 检查 `/mnt/sbin/init` 失败 → `Failed to switch root, dropping to a shell`。

### 为什么这个 shell 这么重要

- **完全 root 权限**（`uid=0(root)`）
- **所有分区可读写**（`/dev/system`、`/dev/backup`、`/dev/xyagent`...）
- **有 `wget`** → 可以从 PC 拉镜像
- **有 `dd`** → 可以写任何分区
- **有 `mke2fs` / `e2fsck`** → 可以格式化

**这是整个项目的转折点。**

---

## 总结：唯一可行的路线

```
串口 → FIQ debugger → reset bootloader → fastboot
                                      ↓
                          写阻断镜像 (32 MB) → 重启
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

三条替代路线全部死：

| 路线 | 状态 | 原因 |
|---|---|---|
| A. Maskrom/Rockusb | ❌ | 测试点未引出 + CPU 有锁 + U-Boot 主动拒绝 |
| B. 直接 fastboot 写 1 GiB | ❌ | `max-download-size` 64 MB 限制 |
| C. 换内核 + 官方镜像 | ❌ | 失去 FIQ + 无备份 + 2 GB 放不进 1 GiB |

**剩下唯一的路：保留原厂内核，自己造 1 GiB rootfs，用 buildroot shell 的 `dd` 写。**
