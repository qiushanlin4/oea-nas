# 04 · Fastboot：绕过锁的写盘通道

## 为什么需要自己写 fastboot 客户端

原厂 U-Boot **没有** `fastboot` 命令（命令表是空的），但 `boot_mode.c` 里硬编码了一条路径：

```c
case BOOT_MODE_BOOTLOADER:
    run_command("fastboot usb 0", 0);
    break;
```

也就是说，当启动模式被设为 `BOOT_MODE_BOOTLOADER` 时，U-Boot 会在 C 代码里直接调用
`fastboot usb 0`，**不经过命令表**。这条路径是通的。

我们用 FIQ debugger 的 `reset bootloader` 触发它：

```
debug> reset bootloader
enter fastboot!
Enter fastboot...OK
```

然后设备的 USB ID 变成 `VID_18D1&PID_D00D`（Google 的 fastboot 厂商 ID）。

### 为什么不用官方 fastboot.exe

我们试过 `platform-tools/fastboot.exe`（Google 官方），它能连上、能列变量，但：

- `fastboot getvar` 能读，`fastboot flash` 会卡住
- `fastboot reboot` 直接挂死（120 秒无输出）
- 原厂 fastboot 实现的响应格式和 AOSP 版有细微差异

所以**自己写一个最小客户端**，只有 149 行 C#。

---

## WinUSB 绑定（Windows 关键步骤）

Windows 自带的 `usbser.sys` 驱动会抢走这个设备，导致 `CreateFileW` 打开的句柄无法用于
`WinUsb_Initialize`。必须手动把设备绑定到 **WinUSB**。

### 方法一：Zadig（推荐）

下载 [Zadig](https://zadig.akeo.ie/)，插上设备后选择 `18D1 D00D`，驱动选 `WinUSB`，
点 `Replace Driver`。

### 方法二：注册表直改（无网络环境）

我们当时没有网络下载 Zadig，直接改注册表。**注意：两个位置都要改。**

```powershell
# 设备实例路径中的序列号，用设备管理器能看到（这里是 84CD9182ECBF9DE7）
$inst = "HKLM:\SYSTEM\CurrentControlSet\Enum\USB\VID_18D1&PID_D00D\84CD9182ECBF9DE7"

Set-ItemProperty $inst -Name "Service" -Value "WinUSB"
Set-ItemProperty $inst -Name "ClassGUID" -Value "{88BAE032-5A81-49F0-BC3D-A4FF138216D6}"
Set-ItemProperty $inst -Name "Driver" -Value "{88BAE032-5A81-49F0-BC3D-A4FF138216D6}\0000"

# 设备接口 GUID（用于枚举）
New-Item -Path "$inst\Device Parameters" -Force | Out-Null
Set-ItemProperty "$inst\Device Parameters" -Name "DeviceInterfaceGUIDs" `
    -Value "{a5dcbf10-6530-11d2-901f-00c04fb951ed}"

# 关键：Class 键也必须补，否则设备管理器会显示 CM_PROB_FAILED_INSTALL (28)
$cls = "HKLM:\SYSTEM\CurrentControlSet\Control\Class\{88BAE032-5A81-49F0-BC3D-A4FF138216D6}\0000"
New-Item -Path $cls -Force | Out-Null
Set-ItemProperty $cls -Name "InfPath"          -Value "winusb.inf"
Set-ItemProperty $cls -Name "InfSection"       -Value "WINUSB.NT"
Set-ItemProperty $cls -Name "MatchingDeviceId" -Value "USB\MS_COMP_WINUSB"
Set-ItemProperty $cls -Name "DriverDesc"       -Value "WinUSB device"
Set-ItemProperty $cls -Name "ProviderName"     -Value "Microsoft"
```

改完后拔插 USB（或在设备管理器里「禁用 → 启用」）。

### 验证

```powershell
Get-PnpDevice | Where-Object { $_.InstanceId -like "*VID_18D1*" }
# Status 应为 OK，Class 应为 USBDevice
```

---

## 自写 fastboot 客户端（fbc）

源码见 [`scripts/windows/fbc/Program.cs`](../scripts/windows/fbc/Program.cs)。

### 编译

```bash
cd scripts/windows/fbc
dotnet build -c Release
# 产物：bin/Release/net8.0/fbc.exe
```

需要 **.NET 8 SDK**。项目文件：

```xml
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net8.0</TargetFramework>
    <Nullable>disable</Nullable>
    <AllowUnsafeBlocks>true</AllowUnsafeBlocks>
    <AssemblyName>fbc</AssemblyName>
  </PropertyGroup>
</Project>
```

### 关键实现细节

#### 1. 设备枚举

```csharp
foreach (var p in L("a5dcbf10-6530-11d2-901f-00c04fb951ed"))
    if (p.ToUpper().Contains("18D1")) path = p;
```

用 `CM_Get_Device_Interface_ListW` + 上面注册表里写的设备接口 GUID 枚举。

#### 2. 打开设备 —— 第 6 个参数是坑

```csharp
IntPtr h = CreateFileW(path, 0xC0000000, 3, IntPtr.Zero, 3,
                       0x40000000,   // ← FILE_FLAG_OVERLAPPED，必须！
                       IntPtr.Zero);
```

**必须带 `FILE_FLAG_OVERLAPPED (0x40000000)`**，否则 `WinUsb_Initialize` 恒定失败，返回
`err=6`（`ERROR_INVALID_HANDLE`）。这个坑我们踩了很久。

- `0xC0000000` = `GENERIC_READ | GENERIC_WRITE`
- `3` = `FILE_SHARE_READ | FILE_SHARE_WRITE`
- `3` = `OPEN_EXISTING`

#### 3. 端点自动探测

```csharp
WinUsb_QueryInterfaceSettings(iface, 0, out d);
for (byte i = 0; i < d.bNumEndpoints; i++) {
    WinUsb_QueryPipe(iface, 0, i, out pi);
    if ((pi.PipeId & 0x80) != 0) { epIn = pi.PipeId; maxPkt = pi.MaximumPacketSize; }
    else                          epOut = pi.PipeId;
}
```

高端点（`0x80` 位）是 IN，低端点是 OUT。不要写死 `0x81`/`0x02`。

#### 4. 超时

```csharp
uint timeout = 20000;  // 毫秒
WinUsb_SetPipePolicy(iface, epIn,  3, 4, ref timeout);  // 3 = PIPE_TRANSFER_TIMEOUT
WinUsb_SetPipePolicy(iface, epOut, 3, 4, ref timeout);
```

不设的话默认无限等待，出错时会挂死。

### 用法

```bash
# 读版本
./fbc.exe getvar:version
# OKAY0.4

# 读最大下载尺寸
./fbc.exe getvar:max-download-size
# OKAY0x04000000          ← 64 MB

# 写分区（分区名用小写，见「分区名」一节）
./fbc.exe flash rootfs stub32.img
# RESP1: DATA04000000
# device wants 33554432 bytes, file has 33554432
# SENT 33554432 bytes in 1.2s (26.19 MB/s)
# RESP2: OKAY
# RESP3: OKAY

# 重启（else 分支会把裸命令直接发给设备）
./fbc.exe reboot
# OKAY
```

> ⚠️ **`fastboot reboot` 会挂死**，用 `./fbc.exe reboot`。

### fastboot 协议流程

```
主机                         设备
 |                            |
 |-- "download:2000000" ----->|      十六进制长度，不带 0x
 |<----- "DATA2000000" -------|      设备确认能收这么多
 |                            |
 |-- <二进制数据，分块写> --->|      WinUsb_WritePipe 循环
 |                            |
 |<----- "OKAY" --------------|      数据接收完成
 |                            |
 |-- "flash:rootfs" --------->|      指定分区名
 |<----- "OKAY" --------------|      写入完成
```

**为什么 fbc 只能一次写一个文件**：协议本身要求 `download` 的长度与后续发送的字节数
严格相等（`if (want != size) SIZE MISMATCH`）。1 GiB 的镜像超过 `max-download-size`
（64 MB），所以**不能直接用 fbc 刷 1 GiB 镜像**。

我们最终改用「HTTP + dd」的方式写大镜像，见 [06-flashing.md](06-flashing.md)。

---

## 分区名（fastboot 只认 7 个）

实测 `fastboot getvar:partition-type:<name>` 与 `flash` 的成功/失败：

| 名字 | 结果 |
|---|---|
| `uboot` | ✅ 可写 |
| `trust` | ✅ 可写 |
| `misc` | ✅ 可写 |
| `boot` | ✅ 可写 |
| `kernel` | ✅ 可写 |
| `env` | ✅ 可写（32 MB，一次写完） |
| `rootfs` | ✅ 可写（= p7 `/dev/system`） |
| `backup` | ❌ `FAILunknown variable` |
| `instaboot` | ❌ `FAILunknown variable` |
| `data` | ❌ `FAILunknown variable` |
| `xyagent` | ❌ `FAILunknown variable` |

**fastboot 只暴露 GPT 里的 7 个分区**（p1–p7）。p8–p13 是内核 DTB 写死的，fastboot 看不到。

所以：
- 想写 p7 的 Armbian rootfs → `flash rootfs`
- 想写 p8/p9/p12/p13 → **只能进 buildroot shell 用 `dd`**

---

## Rockusb 路线为什么失败（死路记录）

`reset loader` 能让设备进入 Rockusb 模式（`VID_2207&PID_320C`），这是 Rockchip 官方的
低级刷机模式，理论上可以直接写整个 eMMC。

我们试了：

1. **官方 RKDevTool / rkdeveloptool** → 设备能被识别，但任何写入都超时
2. **自写 WinUSB 客户端** → `CreateFileW` 能打开，`WriteFile` 返回 `err=6/87/31/121`
3. **注册表改成 WinUSB 后再试** → `WinUsb_WritePipe` 仍 `err=31 → 121`
4. **Maskrom 短接**（eMMC 旁边的测试点）→ 无效

**根因**（U-Boot 日志）：

```
[lzj-i][do_rkusb-169]: Can't do_rkusb by onething...
```

原厂 U-Boot **主动拒绝**了 Rockusb 写入。这是厂商故意设的限制，配合 `Raw SecureMode = 1`
（CPU 有锁）一起生效。

**结论：Rockusb 路线彻底死路，不要再试。** 唯一可行的是 fastboot。

---

## 完整操作流程

```powershell
# 1. 串口进 FIQ
.\scripts\windows\serial\ser.ps1 -Port COM5 -Seconds 8 -Cmd "fiq"

# 2. 切到 fastboot
.\scripts\windows\serial\ser.ps1 -Port COM5 -Seconds 15 -Cmd "reset bootloader"

# 3. 确认设备被识别
Get-PnpDevice | Where-Object { $_.InstanceId -like "*VID_18D1*" }

# 4. 写分区
cd scripts\windows\fbc
.\bin\Release\net8.0\fbc.exe flash rootfs ..\..\..\stub32.img

# 5. 重启
.\bin\Release\net8.0\fbc.exe reboot
```

如果第 4 步报 `OPEN FAIL 2`，说明设备不在 fastboot 模式（或者 WinUSB 没绑对）。
重新执行第 2 步。
