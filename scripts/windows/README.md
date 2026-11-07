# Windows 端脚本说明

## serial/ —— 串口工具

板子的串口参数：**COM 口由系统分配**（我们的是 COM5），**1500000 8N1**。

| 脚本 | 用途 |
|---|---|
| `ser.ps1` | 单次发送一条命令并捕获输出 |
| `keys.ps1` | 按顺序发送多条命令（`\|` 分隔） |
| `baudscan.ps1` | 扫描常见波特率，找出板子当前的速率 |

### 用法

```powershell
# 只监听 6 秒
.\ser.ps1 -Port COM5 -Seconds 6

# 进 FIQ debugger
.\ser.ps1 -Port COM5 -Seconds 8 -Cmd "fiq"

# 切 fastboot
.\ser.ps1 -Port COM5 -Seconds 15 -Cmd "reset bootloader"

# 多条命令（buildroot shell 里）
.\keys.ps1 -Port COM5 -Seq "root|id|ifconfig eth0 10.9.8.77 netmask 255.255.255.0 up" -Seconds 20 -Gap 3

# 找波特率
.\baudscan.ps1
```

### 已知限制

- **长命令会被吞**：超过约 200 字符的命令在 1500000 波特率下会丢字符。
  **大文件一律走网络（HTTP），不要走串口。**
- **交互式登录不可靠**：`armbian login:` 下的密码输入会因为时序问题失败。
  **用 SSH。**
- **不要用 `|` 作为命令内容的一部分**：`keys.ps1` 用它做分隔符。

---

## fbc/ —— fastboot 客户端

自写的极简 fastboot 客户端（C# / .NET 8），149 行。

### 编译

```powershell
cd fbc
dotnet build -c Release
# 产物：bin\Release\net8.0\fbc.exe
```

### 用法

```powershell
# 读变量
.\fbc.exe getvar:version
.\fbc.exe getvar:max-download-size

# 写分区（文件必须 ≤ 64 MB）
.\fbc.exe flash rootfs stub32.img

# 重启（不要用官方 fastboot reboot，会挂死）
.\fbc.exe reboot
```

### 前置条件：WinUSB 绑定

设备的 USB ID 是 `VID_18D1&PID_D00D`（Google fastboot）。Windows 默认会用
`usbser.sys` 抢走它，必须手动改成 WinUSB。

**方法一（推荐）**：用 [Zadig](https://zadig.akeo.ie/)，选 `18D1 D00D` → `WinUSB` → `Replace Driver`。

**方法二（无网络）**：注册表直改，见 [../../docs/04-fastboot.md](../../docs/04-fastboot.md)。

### 已知限制

- **只能写 ≤ 64 MB 的文件**（受 `max-download-size` 限制）
- **只能写 GPT 里的 7 个分区**（`uboot trust misc boot kernel env rootfs`）
- 写 p8–p13 必须进 buildroot shell 用 `dd`

---

## 为什么不用官方 fastboot.exe

Google 的 `fastboot.exe` 能连上、能 `getvar`，但：

- `fastboot flash` 会卡住
- `fastboot reboot` 直接挂死（120 秒无输出）

原厂 U-Boot 的 fastboot 实现和 AOSP 版有细微差异。自写的客户端只做必要的协议子集，
反而更稳。
