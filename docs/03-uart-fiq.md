# 03 · 串口与 FIQ Debugger

## 为什么串口是唯一入口

这块板子没有 HDMI、没有 SD 卡槽、OTG 未引出、U-Boot 命令表被清空。
当你把系统刷坏、网络起不来、SSH 连不上时，**只剩串口**。

好消息是：原厂内核编译时带了 Rockchip 的 **FIQ debugger**，它在内核崩溃、网络挂掉、
systemd 卡死时都能响应 —— 因为它跑在 FIQ（快速中断）上下文，不依赖内核调度器正常工作。

---

## 串口工具

### Windows PowerShell 版本

#### `ser.ps1` —— 单次发送 + 捕获

```powershell
param(
    [string]$Port = "COM5",
    [int]$Baud = 1500000,
    [int]$Seconds = 20,
    [string]$Cmd = ""
)

$sp = New-Object System.IO.Ports.SerialPort
$sp.PortName = $Port
$sp.BaudRate = $Baud
$sp.DataBits = 8
$sp.Parity = [System.IO.Ports.Parity]::None
$sp.StopBits = [System.IO.Ports.StopBits]::One
$sp.Handshake = [System.IO.Ports.Handshake]::None
$sp.DtrEnable = $false
$sp.RtsEnable = $false

try { $sp.Open() } catch { Write-Output ("OPENFAIL: " + $_.Exception.Message); exit 1 }
Write-Output "OPEN-OK"

$sb = New-Object System.Text.StringBuilder
$sw = [Diagnostics.Stopwatch]::StartNew()
$sent = $false

while ($sw.Elapsed.TotalSeconds -lt $Seconds) {
    # 第 1 秒之后才发命令，给串口一个稳定的时间
    if (-not $sent -and $Cmd -ne "" -and $sw.Elapsed.TotalSeconds -gt 1) {
        $sp.Write($Cmd + "`r")
        $sent = $true
        Write-Output ("SENT: " + $Cmd)
    }
    $n = $sp.BytesToRead
    if ($n -gt 0) {
        $b = New-Object byte[] $n
        [void]$sp.Read($b, 0, $n)
        [void]$sb.Append([Text.Encoding]::ASCII.GetString($b))
    }
    Start-Sleep -Milliseconds 60
}
$sp.Close()
$t = $sb.ToString()
Write-Output ("LEN=" + $t.Length)
Write-Output "----"
Write-Output $t
```

**用法**：

```powershell
# 进入 FIQ debugger
.\ser.ps1 -Port COM5 -Seconds 8 -Cmd "fiq"

# 看内核日志
.\ser.ps1 -Port COM5 -Seconds 10 -Cmd "kmsg"

# 切到 fastboot
.\ser.ps1 -Port COM5 -Seconds 15 -Cmd "reset bootloader"

# 只监听，不发送
.\ser.ps1 -Port COM5 -Seconds 30
```

#### `keys.ps1` —— 多条命令序列

```powershell
param(
    [string]$Port = "COM5",
    [int]$Baud = 1500000,
    [int]$Seconds = 8,
    [string]$Seq = "",
    [double]$Gap = 2.0
)

# Seq: 命令用 | 分隔；等输出静默 0.7 秒或 gap 到了才发下一条

$sp = New-Object System.IO.Ports.SerialPort
$sp.PortName = $Port; $sp.BaudRate = $Baud; $sp.DataBits = 8
$sp.Parity = [System.IO.Ports.Parity]::None
$sp.StopBits = [System.IO.Ports.StopBits]::One
$sp.Handshake = [System.IO.Ports.Handshake]::None
$sp.DtrEnable = $false; $sp.RtsEnable = $false

try { $sp.Open() } catch { Write-Output ("OPENFAIL: " + $_.Exception.Message); exit 1 }
Write-Output "OPEN-OK"

$items = @()
if ($Seq -ne "") { $items = $Seq -split '\|' }

$sb = New-Object System.Text.StringBuilder
$sw = [Diagnostics.Stopwatch]::StartNew()
$ki = 0
$nextAt = 1.2
$lastByte = 0.0

while ($sw.Elapsed.TotalSeconds -lt $Seconds) {
    $el = $sw.Elapsed.TotalSeconds
    # 两个条件：到了预定时间 AND 距离上次收到数据至少 0.7 秒
    if ($ki -lt $items.Count -and $el -ge $nextAt -and ($el - $lastByte) -ge 0.7) {
        $sp.Write($items[$ki] + "`r")
        $ki++
        $nextAt = $el + $Gap
    }
    $n = $sp.BytesToRead
    if ($n -gt 0) {
        $b = New-Object byte[] $n
        [void]$sp.Read($b, 0, $n)
        [void]$sb.Append([Text.Encoding]::ASCII.GetString($b))
        $lastByte = $sw.Elapsed.TotalSeconds
    }
    Start-Sleep -Milliseconds 50
}
$sp.Close()
$t = $sb.ToString()
Write-Output ("LEN=" + $t.Length)
Write-Output "----"
Write-Output $t
```

**用法**：

```powershell
# 登录 buildroot：root + id + 配置网络 + 测试
.\keys.ps1 -Seq "root|id|ifconfig eth0 10.9.8.77 netmask 255.255.255.0 up|ping -c 1 -W 2 10.9.8.10" -Seconds 20 -Gap 3
```

> ⚠️ **注意**：传给 `-Seq` 的字符串**不要包含 `|` 之外的分隔符**。
> 在 Bash 里调用时小心 PowerShell 数组语法（会报 syntax error）。

#### `baudscan.ps1` —— 自动探测波特率

```powershell
param([int[]]$Bauds = @(1500000, 115200, 921600, 460800), [int]$Seconds = 6)

foreach ($b in $Bauds) {
    $sp = New-Object System.IO.Ports.SerialPort
    $sp.PortName = "COM5"; $sp.BaudRate = $b; $sp.DataBits = 8
    $sp.Parity = [System.IO.Ports.Parity]::None
    $sp.StopBits = [System.IO.Ports.StopBits]::One
    $sp.Handshake = [System.IO.Ports.Handshake]::None
    $sp.DtrEnable = $false; $sp.RtsEnable = $false

    try { $sp.Open() }
    catch { Write-Output ("BAUD " + $b + " OPENFAIL: " + $_.Exception.Message); continue }

    $sb = New-Object System.Text.StringBuilder
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $sent = $false

    while ($sw.Elapsed.TotalSeconds -lt $Seconds) {
        if (-not $sent -and $sw.Elapsed.TotalSeconds -gt 1) {
            $sp.Write("`r`n")
            $sent = $true
        }
        $n = $sp.BytesToRead
        if ($n -gt 0) {
            $b2 = New-Object byte[] $n
            [void]$sp.Read($b2, 0, $n)
            [void]$sb.Append([Text.Encoding]::ASCII.GetString($b2))
        }
        Start-Sleep -Milliseconds 50
    }
    $sp.Close()
    $t = $sb.ToString()
    Write-Output ("BAUD " + $b + " LEN=" + $t.Length)
    if ($t.Length -gt 0) { Write-Output $t }
}
```

这个脚本在**不知道板子当前波特率**时非常有用 —— 比如 Armbian 启动到某个阶段改了波特率，
或者你在纠结板子到底在不在线。

实测输出示例：

```
BAUD 1500000 LEN=247
^J??? Please enter system locale name or number (empty to skip, "list" to list options): No data entered, skipping.
??? Please enter timezone name or number (empty to skip, "list" to list options):
BAUD 115200 LEN=1
BAUD 921600 LEN=2
BAUD 460800 LEN=1993   ← 乱码
```

### Linux / macOS 版本

```bash
#!/bin/bash
# serial.sh <command>
PORT=${SERIAL_PORT:-/dev/ttyUSB0}
BAUD=1500000
SECS=${2:-8}

stty -F "$PORT" $BAUD raw -echo -echoe -echok
cat "$PORT" &
CATPID=$!
sleep 1
printf '%s\r' "$1" > "$PORT"
sleep "$SECS"                      # $SECS 秒后停止
kill $CATPID 2>/dev/null
```

**用法**：

```bash
./serial.sh 'fiq'
./serial.sh 'reset bootloader' 15
```

---

## FIQ Debugger

### 进入

板子运行时（任何状态：内核跑着、systemd 卡住、SSH 连不上），从串口连续发送 `fiq`：

```bash
./serial.sh 'fiq'
```

输出：

```
Welcome to fiq debugger mode
Enter ? to get command help
debug>
```

**原理**：`fiq_debugger` 驱动注册了一个 UART 中断处理器，扫描输入流中连续的 `fiq` 字符序列。
一旦匹配就在 FIQ 上下文接管串口，不需要内核正常工作。

### 退出

```
debug> console
```

`console` 命令把 ttyFIQ0 还给内核。如果内核还活着，你会看到 getty 提示符或当前输出。

### 完整命令表

来自内核源码 `fiq.c` 的 `cmd_buf[][]`：

```c
static char cmd_buf[][16] = {
    {"pc"},
    {"regs"},
    {"allregs"},
    {"bt"},
    {"reboot"},
    {"irqs"},
    {"kmsg"},
#ifdef CONFIG_ROCKCHIP_DEBUG
    {"pcsr"},
#endif
    {"version"},
    {"sleep"},
    {"nosleep"},
    {"console"},
    {"cpu"},
    {"ps"},
    {"sysrq"},
    {"reset"},
#ifdef CONFIG_KGDB
    {"kgdb"},
#endif
};
```

| 命令 | 作用 | 实用场景 |
|---|---|---|
| `pc` | 当前 PC 寄存器 | 内核崩溃定位 |
| `regs` | 寄存器 dump | 同上 |
| `allregs` | 全部 CPU 的寄存器 | SMP 调试 |
| `bt` | 调用栈回溯 | 内核 hang 时看卡在哪 |
| `reboot` | 重启（优雅） | 系统卡死时重启 |
| `irqs` | 中断统计 | 看是否在疯狂中断 |
| `kmsg` | **打印内核日志** | ⭐ **网络挂了时的唯一信息来源** |
| `pcsr` | Rockchip 专有状态 | — |
| `version` | FIQ debugger 版本 | — |
| `sleep` | 让 CPU 睡眠（省电调试） | — |
| `nosleep` | 恢复 | — |
| `console` | **把串口还给内核** | ⭐ 退出 debugger |
| `cpu` | 当前 CPU 号 | — |
| `ps` | 进程列表（极小） | — |
| `sysrq` | 发送 SysRq 命令 | 强制 sync / 杀进程 |
| `reset` | **切换 boot 模式** | ⭐⭐ **核心命令，见下** |
| `kgdb` | KGDB 调试 | 通常未编译 |

### `kmsg` —— 最重要的诊断命令

当板子 SSH 连不上、ping 不通时：

```bash
./serial.sh 'fiq'
./serial.sh 'kmsg'
```

输出是内核环形缓冲区的完整内容。**这比 journalctl 还有用**，因为它不依赖 systemd 或磁盘。

实战案例 —— 我们靠它定位了网络不起的问题：

```
[    5.568298] rk_gmac-dwmac ff550000.ethernet end1: renamed from eth0
```

原来网卡被 systemd 的可预测命名规则改成了 `end1`，而我们的配置写死 `eth0`。
**整个日志里没有任何 `Link is Up`** —— 说明没有任何程序把网卡 admin-up。

### `reset` —— 切换 boot 模式

```
debug> reset
Usage: reset <loader|bootloader|recovery|ums>
```

| 参数 | 效果 | 实测 |
|---|---|---|
| `reset loader` | 进入 Rockusb 模式（`VID_2207&PID_320C`） | ✅ 有效，但写入被拒绝 |
| `reset bootloader` | **进入 Android Fastboot**（`VID_18D1&PID_D00D`） | ⭐✅ **核心通道** |
| `reset recovery` | 尝试 recovery | ❌ `No resource partition` |
| `reset ums` | USB Mass Storage | ❌ 被忽略（boot mode: None） |

**`reset bootloader` 是整个项目的关键。** 它绕过了 BootROM 的签名验证，直接让
U-Boot 进入 fastboot 模式 —— 而 fastboot 的 `flash` 命令**不检查 secure boot**。

源证据（`boot_mode.c`）：

```c
case BOOT_MODE_BOOTLOADER:
    snprintf(env_preboot, 256,
             "setenv preboot; mmc dev %x; fastboot usb 0; ",
             ...);
    env_set("preboot", env_preboot);
    run_command("fastboot usb 0", 0);
    break;
```

---

## U-Boot 命令表为空（死路记录）

我们尝试了 88 条 U-Boot 命令，全部返回：

```
Unknown command '<x>' - try 'help'
```

包括 `help`、`printenv`、`saveenv`、`reset`、`ums 0 mmc 0`、`sysboot` 等基础命令。

**能停在 `=>` 提示符**（Ctrl-C 在 autoboot 期间有效），但**不能执行任何命令**。

### 为什么 env 注入也走不通

我们以为可以通过 U-Boot env 的 `preboot` 注入 `ums mmc 0` 来导出 eMMC。
从源码看这条路是死的：

```c
case BOOT_MODE_UMS:
    env_set("preboot", "setenv preboot; ums mmc 0");   // ← 只是设置字符串
    break;
```

真正的执行在别处，最终会走 `run_command()`：

```c
case BOOT_MODE_LOADER:
    env_set("preboot", "setenv preboot; download");
    run_command("download", 0);     // ← 命令表空，必然失败
    break;
```

**命令表空 = `run_command()` 必然 `Unknown command`**，所以任何 env 注入方案都无效。

### 唯一的 C 级注入通道（未使用）

`uboard.c:1478-1482` 有一段代码：

```c
env = env_get("sys_bootargs");
if (env) {
    env_update("bootargs", env);
    if (verbose)
        printf("## sys_bootargs: %s\n\n", env);
}
```

**这是 C 级追加内核 cmdline 的通道，不经过命令表。** 我们评估后没有使用它，
因为 fastboot 写 p7 已经能解决问题。但如果将来需要给内核加参数
（比如 `init=/bin/sh` 进单用户），这是唯一可行的 env 通道。

> 注：`oea/envtool` 工具可以生成 U-Boot env 镜像，但我们写的 CRC32 格式不对，
> 写进去后 U-Boot 报 `bad CRC, using default environment`。如果你要尝试，需要用
> 正确的环境镜像格式（参考 U-Boot 的 `tools/env/fw_env.c`）。

---

## 掉进 buildroot shell（救援模式）

这是本项目最有用的一个技巧。

### 原理

原厂 `/init` 的最后一步：

```sh
if [ -x /mnt/sbin/init ]; then
    exec switch_root ...
else
    echo "Failed to switch root, dropping to a shell"
    exec /sbin/init      # → busybox
fi
```

只要 p7 里的 `/sbin/init` **不可执行或不存在**，就会掉进 busybox 的 root shell。

### 操作

1. 生成一个**没有 `/sbin/init` 的 32 MB ext4 镜像**（"阻断镜像"）：

```bash
mke2fs -q -F -t ext4 -L shellstub -d empty_dir/ stub32.img 32768
```

只有 `lost+found`，没有 `/sbin/init`。

2. 通过 fastboot 把它写进 p7：

```bash
./fbc flash rootfs stub32.img
./fbc reboot
```

3. 重启后串口会看到：

```
mount overlay /dev/system /dev/instaboot complete 0
initd_setup entry...
Failed to switch root, dropping to a shell
fatorymode = factory, S98 exit
buildroot login:
```

4. 输入 `root`（**空密码**）：

```
Welcome to Buildroot
buildroot login: root
login[183]: root login on 'console'
root@buildroot:/root#
```

### 这个 shell 能做什么

```bash
# 完全 root 权限
id                  # uid=0(root) gid=0(root) groups=0(root),10(wheel)

# 所有分区可读写
/dev/system    (179,7)   p7
/dev/backup    (179,8)   p8
/dev/instaboot (179,9)   p9
/dev/misc      (179,10)  p10
/dev/cache     (179,11)  p11
/dev/data      (179,12)  p12
/dev/xyagent   (179,13)  p13

# 有 wget、dd、mke2fs、e2fsck、losetup、tar、xz、nc（无）
# 有 ping、ifconfig、ip、route
# 没有 ssh、curl、fw_setenv（要自己造符号链接）
```

### 恢复网络

这个环境里网络默认是 down 的，需要手动配：

```bash
ifconfig eth0 10.9.8.77 netmask 255.255.255.0 up
# 内核日志会立刻出现：
# rk_gmac-dwmac ff550000.ethernet eth0: Link is Up - 100Mbps/Full
ping -c 2 10.9.8.10
```

**然后就能从 PC 的 HTTP 服务器拉镜像了**：

```bash
(wget -O - http://10.9.8.10:8899/final.img | dd of=/dev/system bs=1M conv=fsync) &
```

实测速度 **19 MB/s**（跑满百兆），1 GB 镜像约 55 秒。

### 创建删除符号链接

buildroot 里没有 `fw_setenv`，但 `/init` 会自己造：

```bash
ln -sf /usr/sbin/fw_printenv /usr/sbin/fw_setenv
```

---

## 串口踩坑记录

| 问题 | 现象 | 解决 |
|---|---|---|
| 命令被吞 | 长命令（>200 字符）发出去后乱码 | **大文件一律走 HTTP，不要走串口** |
| 登录失败 | `armbian login:` 下输入 root 后 `Login incorrect` | 串口行编辑时序问题，**改用 SSH** |
| `keys.ps1` 分隔符 | 用 `\|` 分隔时产生多余空行冲掉密码 | 用 `-Seq "cmd1\|cmd2"`，不要在命令里含 `\|` |
| 波特率错 | 全乱码 | 用 `baudscan.ps1` 扫 |
| 端口占用 | `The semaphore timeout period has expired` | 别的程序占着 COM 口 |

### 串口登录为什么不可靠

我们试了很多次，最终结论：**`armbian login:` 提示符下的交互登录是不可靠的**。

原因：
1. `agetty --autologin root` 的 override 与真实 `login` 程序冲突
2. `--keep-baud` 参数导致输入时序错乱
3. 1500000 波特率下字符间需要精确的间隔

**解决方案：用 SSH。** 只要网络通，SSH 永远比串口可靠。

如果必须用串口（网络挂了），就靠 **FIQ debugger 的 `kmsg`** 诊断，不要试图登录。
