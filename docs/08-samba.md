# 08 · Samba 服务

## 安装

```bash
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    samba samba-common-bin
```

装完会自动创建这些 systemd 单元和链接：

```
smb.service   -> smbd.service
nmb.service   -> nmbd.service
samba.service -> samba-ad-dc.service    ← 这个不需要，禁用掉
```

```bash
systemctl disable samba-ad-dc.service
rm -f /etc/systemd/system/multi-user.target.wants/samba-ad-dc.service
```

版本：`smbd --version` → `Version 4.19.5-Ubuntu`

---

## `/etc/samba/smb.conf`（完整可用版本）

```ini
[global]
   workgroup = WORKGROUP
   server string = OEA NAS
   netbios name = OEA-NAS
   server role = standalone server
   map to guest = bad user
   guest account = nobody
   security = user
   bind interfaces only = no

   log file = /var/log/samba/log.%m
   max log size = 1000
   logging = file

   server min protocol = SMB2_10
   server max protocol = SMB3_11
   server signing = mandatory
   client signing = mandatory
   ntlm auth = ntlmv2-only
   server smb encrypt = auto

   unix charset = UTF-8
   dos charset = CP936

   load printers = no
   printing = bsd
   printcap name = /dev/null
   disable spoolss = yes

[homes]
   comment = Home Directories
   browseable = no
   read only = no
   create mask = 0644
   directory mask = 0755
   valid users = %S

[nas]
   comment = OEA NAS - merged 111G pool
   path = /srv/pool
   browseable = yes
   guest ok = yes
   force user = nobody
   force group = nogroup
   read only = no
   create mask = 0666
   directory mask = 0777
```

### 每个非默认项的说明

| 配置 | 为什么 |
|---|---|
| `bind interfaces only = no` | **重要**：这块板子的网卡名在 `eth0` / `end1` 之间随重启变化，写死名字会导致 smbd 启动失败 |
| `server min protocol = SMB2_10` | Windows 11 25H2 已禁用 SMB1 |
| `server signing = mandatory` | Windows 11 25H2 **强制要求 SMB 签名** |
| `ntlm auth = ntlmv2-only` | Windows 11 默认禁用 NTLMv1 |
| `dos charset = CP936` | 中文文件名不乱码（`unix charset = UTF-8` 管 Linux 侧） |
| `load printers = no` / `disable spoolss = yes` | 关掉打印子系统，省内存（1 GB 的板子要精打细算） |
| `map to guest = bad user` | 允许匿名访问公共共享 |
| `force user = nobody` | 所有访问都用 `nobody` 身份，避免权限地狱 |

### 验证配置

```bash
testparm -s
```

这条命令会打印解析后的配置，有语法错误会直接报出来。

---

## 共享目录的权限（必踩的坑）

`force user = nobody` 意味着 **smbd 会用 `nobody` 用户的身份去读写**。
如果共享目录的属主是 `root` 且权限是 `755`，写入会失败：

```
NT_STATUS_ACCESS_DENIED
```

**必须先 `chmod 777`**：

```bash
chmod 777 /srv/pool
chmod -R 777 /mnt/d1/data /mnt/d2/data /mnt/d3/data
```

或者在 `smb.conf` 里指定 `create mask` / `directory mask`（我们已经配了
`0666` / `0777`），但**目录本身仍需可写**。

---

## 用户管理

### 创建 Samba 用户

Samba 有自己的密码库（`/var/lib/samba/private/passdb.tdb`），**必须**用 `smbpasswd`
给已存在的 Linux 用户设置密码：

```bash
# 交互式
smbpasswd -a nas

# 非交互（脚本里用）
(echo 'YOUR_SMB_PASSWORD'; echo 'YOUR_SMB_PASSWORD') | smbpasswd -a -s nas
# Added user nas.

smbpasswd -e nas          # 启用
# Enabled user nas.
```

**注意**：Linux 用户 `nas` 必须**先存在**（`useradd` 或 Armbian firstrun 创建）。

### 密码同步

Samba 密码和 Linux 密码是**独立**的。改了 Linux 密码不影响 Samba：

```bash
# 改了 Linux 密码
passwd nas

# Samba 密码不变，需要单独改
smbpasswd nas
```

---

## 板端自测

### 列出共享

```bash
# 匿名（会被拒，正常）
smbclient -L //127.0.0.1 -N
# session setup failed: NT_STATUS_ACCESS_DENIED

# 带账号
smbclient -L //127.0.0.1 -U nas%YOUR_SMB_PASSWORD -m SMB3
```

预期输出：

```
        Sharename       Type      Comment
        ---------       ----      -------
        nas             Disk      OEA NAS - merged 111G pool
        IPC$            IPC       IPC Service (OEA NAS)

Reconnecting with SMB1 for workgroup listing.
```

### 读写测试

```bash
echo "hello" > /tmp/t.txt

smbclient //127.0.0.1/nas -U nas%YOUR_SMB_PASSWORD -m SMB3 \
    -c 'put /tmp/t.txt smbtest.txt; ls'
```

预期：

```
putting file /tmp/t.txt as \smbtest.txt
 111374684 blocks of size 1024. 105113092 blocks available
```

### 端口检查

```bash
ss -lntp | grep -E ':(139|445)'
```

预期：

```
LISTEN 0 4096 0.0.0.0:445 0.0.0.0:* users:(("smbd",pid=...))
LISTEN 0 4096 0.0.0.0:139 0.0.0.0:* users:(("smbd",pid=...))
LISTEN 0 4096   [::]:445   [::]:* users:(("smbd",pid=...))
LISTEN 0 4096   [::]:139   [::]:* users:(("smbd",pid=...))
```

---

## Windows 连接

### 方法一：资源管理器地址栏（最快）

```
\\192.168.101.33
\\oeanas.local
```

### 方法二：映射网络驱动器（持久化）

```cmd
net use N: \\oeanas.local\nas /user:nas YOUR_SMB_PASSWORD /persistent:yes
```

验证：

```cmd
net use
# OK           N:        \\oeanas.local\nas     Microsoft Windows Network

dir N:\
# 116,849,790,976 bytes free
```

**`/persistent:yes` 是关键** —— 不加的话重启电脑后映射消失。

> ⚠️ 用 PowerShell 的 `New-SmbMapping` 只在**当前会话**有效，
> 重启后消失（`HKCU:\Network\N` 里没有条目）。持久化必须用 `net use /persistent:yes`。

### 方法三：PowerShell 脚本（一次性验证）

```powershell
# 清理旧映射
net use Z: /delete /y 2>$null
net use Y: /delete /y 2>$null

# 建新映射
New-SmbMapping -LocalPath "Z:" -RemotePath "\\oeanas.local\nas" `
    -UserName "nas" -Password "YOUR_SMB_PASSWORD" -ErrorAction Stop

Get-ChildItem Z:\
```

---

## 🚨 `System error 67` 的真正原因

我们在这上面浪费了很久。

### 现象

```cmd
net use \\10.9.8.77\nas /user:nas YOUR_SMB_PASSWORD
System error 67 has occurred.
The network name cannot be found.
```

但：
- `Test-NetConnection -Port 445` → **成功**
- 板子端 `tcpdump` → **一个包都抓不到**

### 根因：PowerShell 脚本里的反斜杠被吃了

我们用 bash heredoc 生成 `.ps1` 文件：

```bash
cat > maptest.ps1 <<'EOF'
$remote = '\\10.9.8.77\nas'
EOF
```

生成的脚本里 `\\` 变成了 `\`：

```powershell
$remote = '\10.9.8.77\nas'    # len=16，不是 17
```

PowerShell 收到非法 UNC 路径 → `System error 67`。

### 诊断

```bash
grep -n "remote = " maptest.ps1 | cat -A
# $remote = '\10.9.8.77\nas'$      ← 一个反斜杠
```

### 修复

**凡含反斜杠 / `$` / 非 ASCII 的 PowerShell 脚本，一律用文本编辑器或可靠的写文件工具创建，
不要用 bash heredoc。**

我们最后用专门的 write 工具写，验证长度：

```powershell
$remote = "\\10.9.8.77\nas"
Write-Output "remote=[$remote] len=$($remote.Length)"
# remote=[\\10.9.8.77\nas] len=17     ← 正确
```

---

## Windows 11 兼容性要点

Windows 11 24H2/25H2 收紧了 SMB 安全策略，默认：

| 策略 | 默认值 | 后果 |
|---|---|---|
| SMB1 | 禁用 | 老 NAS 连不上 |
| SMB 签名 | **强制要求** | 不签名的服务器连不上 |
| NTLMv1 | 禁用 | 只支持 NTLMv1 的连不上 |
| 来宾访问 | 禁用 | `guest ok` 共享无法浏览 |

我们的 `smb.conf` 已经全部适配（`SMB2_10`+ / `server signing = mandatory` /
`ntlmv2-only`）。

### 如果还是要打开来宾访问

Windows 组策略（需管理员）：

```
计算机配置 → 管理模板 → 网络 → Lanman 工作站
  → 启用不安全的来宾登录 = 已启用
```

或注册表：

```powershell
Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\LanmanWorkstation\Parameters" `
    -Name "AllowInsecureGuestAuth" -Value 1 -Type DWord
```

---

## 网络发现（资源管理器「网络」里看到 NAS）

板子侧要做两件事：

### 1. nmbd（NetBIOS 名称服务）

`smbd` + `nmbd` 都 enable：

```bash
systemctl enable --now smbd nmbd
```

验证：

```cmd
nbtstat -A 192.168.101.33
```

预期：

```
    OEA-NAS        <00>  UNIQUE      Registered
    OEA-NAS        <03>  UNIQUE      Registered
    OEA-NAS        <20>  UNIQUE      Registered
    WORKGROUP      <00>  GROUP       Registered
    WORKGROUP      <1D>  UNIQUE      Registered
    WORKGROUP      <1E>  GROUP       Registered
    __MSBROWSE__   <01>  GROUP       Registered
```

有这些条目 = NetBIOS 注册正常。

### 2. wsdd2（Web Services Discovery，Windows 10/11 用这个）

Windows 10 以后弃用 NetBIOS，改用 WSD。装 `wsdd2`：

```bash
apt-get install -y --no-install-recommends wsdd2
systemctl enable --now wsdd2
```

验证：

```bash
ss -lunp | grep -E ':(3702|5355)'
# UNCONN 0 0 0.0.0.0:3702  users:(("wsdd2",pid=2107))
# UNCONN 0 0 0.0.0.0:5355  users:(("wsdd2",pid=2107))
```

### 3. Windows 侧：网络配置文件必须是「专用」

**这是最终卡住我们的地方。**

Windows 会把未知网络判定为**「公用网络」**，在公用网络下：
- 网络发现：**关闭**
- 文件共享：**关闭**

检查：

```powershell
Get-NetConnectionProfile
# Name             : 未识别的网络
# InterfaceAlias   : 以太网
# NetworkCategory  : Public        ← 问题在这
```

修改（**需要管理员权限**）：

```powershell
Set-NetConnectionProfile -InterfaceIndex 20 -NetworkCategory Private
```

或图形界面：

```
设置 → 网络和 Internet → 以太网 → 网络配置文件 → 专用网络
```

我们当时 agent 没有管理员权限（`net session` → `ADMIN=no`），
`Set-NetConnectionProfile` 报 `not running PowerShell elevated`，
所以这一步**必须用户手动做**。

改完后资源管理器「网络」里就能看到 `OEA-NAS`。

---

## 故障排查速查表

| 现象 | 原因 | 修复 |
|---|---|---|
| `System error 67` | UNC 路径写错（反斜杠/拼写） | 见上文 |
| `System error 53` | 主机不可达 | 先 `Test-NetConnection -Port 445` |
| `System error 1272` | 凭据冲突 | `net use * /delete /y` 后重连 |
| `NT_STATUS_ACCESS_DENIED` | 目录权限不足 | `chmod 777 /srv/pool` |
| 能连上但看不到共享 | `browseable = no` | 检查 smb.conf |
| 写入文件后找不到 | `force user = nobody` 写到了别的目录 | 检查 `path =` |
| 中文文件名乱码 | 缺 `dos charset = CP936` | 加配置 |
| smbd 起不来 | smb.conf 语法错 | `testparm` |
| nmbd 超时 | 网卡还没 up | 加 `After=netup.service` |
| 资源管理器看不到 | Windows 公用网络 | 改成「专用」 |

### nmbd 启动超时的额外处理

我们遇到过 `nmbd.service: start operation timed out`，报：

```
Status: "nmbd: No local IPv4 non-IPv4 non-loopback interfaces available,
         waiting for interface ..."
```

因为它启动时网卡还没地址。加 override：

```ini
# /etc/systemd/system/nmbd.service.d/override.conf
[Unit]
After=netup.service network-online.target
Wants=netup.service network-online.target

[Service]
TimeoutStartSec=60
```

```bash
systemctl daemon-reload
systemctl restart nmbd
```

---

## 服务自启检查

```bash
systemctl is-enabled smbd nmbd wsdd2 avahi-daemon netup ssh
```

全部应该是 `enabled`。

最终状态：

```bash
$ systemctl is-system-running
running

$ systemctl --failed
0 loaded units listed.

$ ps -ef | grep -E 'smbd|nmbd|wsdd2' | grep -v grep
root  1234  1  smbd --foreground --no-process-group
root  1240 1234  smbd: notifyd
root  1241 1234  smbd: cleanupd
root  1250  1  nmbd --foreground --no-process-group
root  2107  1  /usr/sbin/wsdd2 -d
```

下一节 → [09-storage.md](09-storage.md)
