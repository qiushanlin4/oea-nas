# 10 · 故障排查

本文档按症状组织，全部来自实际踩过的坑。

---

## 一、板子完全没反应

### 症状
插电后串口无输出、网口灯不亮。

### 排查顺序

| 步骤 | 检查 | 说明 |
|---|---|---|
| 1 | 电源规格 | 12V/1.5A 以上，**不要用 5V** |
| 2 | 串口接线 | 见 [01-hardware.md](01-hardware.md) 的孔位图 |
| 3 | 波特率 | `baudscan.ps1` 扫（1500000 / 115200 / 921600 / 460800） |
| 4 | CH340 驱动 | `Get-PnpDevice` 找 `VID_1A86&PID_7523` |
| 5 | 换个 USB 口 | 有些 hub 供电不足 |

### 串口无输出但板子可能在跑

用 `ping` 判断：

```powershell
ping 10.9.8.77      # 直连时的静态地址
ping 192.168.101.33 # 路由器 DHCP 时（可能变）
```

如果 ping 通，说明系统正常，只是串口没接好。

---

## 二、FIQ debugger 进不去

### 症状
发 `fiq` 后串口没有 `debug>` 提示符。

### 原因与对策

| 原因 | 判断 | 对策 |
|---|---|---|
| 内核没跑（在 U-Boot） | 串口有 U-Boot 输出但没内核 log | U-Boot 里 FIQ 不可用，等 3 秒 autoboot |
| 内核 panic | 串口刷屏 | `fiq` 可能在 panic 后才响应，多试几次 |
| 波特率不对 | 乱码 | `baudscan.ps1` |
| 串口被别的程序占用 | `The semaphore timeout period has expired` | 关掉其他串口工具 |
| 内核没有 fiq_debugger | — | 换了非原厂内核就会这样（**这就是我们保留原厂内核的原因**） |

### 关键：不要换内核

Armbian 6.18 内核里 **grep 不到** `fiq_debugger` / `ttyFIQ`：

```bash
$ grep -r 'fiq_debugger' /usr/lib/modules/6.18.*/ 2>/dev/null
（空）
```

换了内核 = **永久失去串口救援通道**。而原厂 boot 分区又读不出来
（fastboot 没有读能力），所以**无法备份**。

**结论：必须保留原厂 4.19.232 内核。**

---

## 三、能启动但登录不了

### 症状 A：`PAM Failure, aborting: Critical error - immediate abort`

**这是本项目最经典的坑。**

```
armbian login: root
Password:
login: PAM Failure, aborting: Critical error - immediate abort
```

输任何密码都是这个（包括正确密码）。

#### 根因

`/etc/pam.d/login` 第 82 行：

```
session    optional   pam_lastlog.so
```

但镜像里 **没有 `pam_lastlog.so`**。

PAM 在 `pam_start()` 阶段会 dlopen 栈里引用的**所有**模块（`optional` 也不例外），
任何一个失败 → 整条栈返回 `PAM_ABORT`。

#### 验证

```bash
# 检查所有引用
grep -rhoE 'pam_[a-z0-9_]+\.so' /etc/pam.d/ | sort -u | while read m; do
    [ -f "/usr/lib/aarch64-linux-gnu/security/$m" ] || echo "MISSING: $m"
done
# MISSING: pam_lastlog.so
```

#### 修复

```bash
sed -i '/pam_lastlog/d' /etc/pam.d/login
```

或者从别的系统拷一个 `pam_lastlog.so` 过来（`libpam-modules` 包里有，
`apt-get install --reinstall libpam-modules`）。

> 如果连 shell 都进不去，只能用 [05-rootfs.md](05-rootfs.md) 的方法在镜像里改，
> 或者用 [03-uart-fiq.md](03-uart-fiq.md) 的阻断镜像法进 buildroot shell。

### 症状 B：`Login incorrect`

密码错了，或者串口行编辑有问题。

#### 排查

```bash
# 验证密码哈希（Python 3）
python3 -c "import crypt; h='$(grep ^root: /etc/shadow | cut -d: -f2)'; print(crypt.crypt('你的密码', h) == h)"
# True / False
```

如果 `True` 但登录还是失败 → **是串口问题，不是密码问题**。

#### 串口行编辑为什么不可靠

1500000 波特率下，字符间需要精确的时序。我们的实测：

- `keys.ps1` 发送时提示符与输入错位
- `Password:` 后收到的字符串不是纯密码（比如 `1234` 变成 `id`）
- 密码行被回车冲掉

**解决方案：用 SSH。**

```bash
ssh root@10.9.8.77
```

### 症状 C：SSH `Permission denied (publickey,password)`

pubkey 认证失败，然后密码认证被拒。

#### 快速解决：装公钥

```bash
# PC 端
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 -C "dsh-oea"
# 公钥：ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA... dsh-oea

# 板端（串口）
mkdir -p /root/.ssh
echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA... dsh-oea' > /root/.ssh/authorized_keys
chmod 700 /root/.ssh
chmod 600 /root/.ssh/authorized_keys
```

#### 用密码登录（脚本里）

`sshpass` / `expect` 都没装。可行方案是 `SSH_ASKPASS`：

```bash
cat > /tmp/askpass.sh <<'EOF'
#!/bin/sh
echo '你的密码'
EOF
chmod +x /tmp/askpass.sh

SSH_ASKPASS=/tmp/askpass.sh SSH_ASKPASS_REQUIRE=force DISPLAY=:0 \
    setsid ssh root@10.9.8.77 'whoami'
```

> `SSH_ASKPASS_REQUIRE=force` 很关键 —— 不加的话 ssh 会优先用 tty。

---

## 四、重启后掉进维护模式

### 症状

```
Give root password for maintenance
(or press Control-D to continue)
```

```bash
$ systemctl is-system-running
maintenance
```

### 排查

```bash
systemctl --failed
journalctl -b -p err
```

### 常见原因 1：fstab 里有 `/` 条目

```
systemd-remount-fs[331]: mount: /: can't find PARTUUID=614e0000-0000.
```

**`/` 是 overlayfs，不是块设备。fstab 里不能有 `/` 条目。**

修复：删掉，见 [09-storage.md](09-storage.md)。

### 常见原因 2：fstab 用 UUID 挂 eMMC 分区

```
Timed out waiting for device dev-disk-by\x2duuid-360e5227...
```

**udev 不给 p8–p13 建 `/dev/disk/by-uuid/` 链。**

修复：改用设备路径 `/dev/xyagent` / `/dev/data` / `/dev/backup`。

### 常见原因 3：分区挂载失败阻塞

所有非 `/` 的条目都要加 `nofail`：

```fstab
/dev/xyagent /mnt/d1 ext4 defaults,noatime,nofail,x-systemd.device-timeout=10 0 2
```

`x-systemd.device-timeout=10` 让失败只等 10 秒而不是 90 秒。

### 应急恢复

在维护模式 shell 里：

```bash
mount -o remount,rw /
vi /etc/fstab        # 或 nano

# 或者直接覆盖
cat > /etc/fstab <<'EOF'
tmpfs /tmp tmpfs defaults,nosuid 0 0
/dev/xyagent /mnt/d1 ext4 defaults,noatime,nofail,x-systemd.device-timeout=10 0 2
EOF

systemctl reboot
```

---

## 五、网络不通

### 症状 A：SSH 连不上，但板子明明在跑

#### 第一步：FIQ 看 kmsg

```bash
.\ser.ps1 -Cmd "fiq"
.\ser.ps1 -Seconds 12 -Cmd "kmsg"
```

搜：

```
renamed from eth0    → 网卡改名了
Link is Up           → 有这行 = 物理链路 OK
```

**如果完全没有 `Link is Up`** → 没有任何程序 up 网卡。
见 [07-network.md](07-network.md) 的三重缺失分析。

#### 第二步：全子网扫描

```powershell
1..254 | ForEach-Object -Parallel {
    $ip = "192.168.101.$_"
    if (Test-Connection $ip -Count 1 -Quiet -TimeoutSeconds 1) {
        $ok = (Test-NetConnection $ip -Port 445 -WarningAction SilentlyContinue).TcpTestSucceeded
        if ($ok) { Write-Output "$ip HAS SMB" }
    }
} -ThrottleLimit 64
```

**只认开 445 的主机**（板子一定开着 smbd）。

> ⚠️ 别被路由器代理 ARP 骗了：多个 IP 映射到同一个 MAC 就是网关代答。

### 症状 B：板子失联，扫描也找不到

#### 最可能的原因：网线换了但 dhcpcd 被杀

如果 `netup.sh` 里有 `pkill -f dhcpcd`，DHCP 客户端会死掉，
网线从 PC 换到路由器后**永远不会重新 DHCP**。

**恢复方法**：

1. 网线插回 PC（直连）
2. `ping 10.9.8.77` 应该通（静态地址还在）
3. SSH 进去，换成 v8 的 `netup.sh`
4. 插回路由器

如果 ping 也不通 → 断电重启（**网线保持在路由器上**），
启动时 netup.sh 会重新 DHCP。

### 症状 C：能 ping 通 IP，但 `oeanas.local` 解析不了

```powershell
ping oeanas.local
# Ping request could not find host oeanas.local
```

#### 排查

```bash
# 板子侧
systemctl status avahi-daemon
getent hosts oeanas.local
```

如果板子自己解析不了 → avahi 没跑或 hostname 不对。

```bash
hostnamectl set-hostname oeanas
systemctl restart avahi-daemon
```

如果板子能解析但 PC 不能 → PC 缺 mDNS 支持（Windows 10/11 通常自带；
早期 Win10 需要装 Bonjour）。

**替代方案**：用 IP 直连，或者用 NetBIOS：

```cmd
ping OEA-NAS
net view \\OEA-NAS
```

---

## 六、磁盘空间问题

### 症状：`No space left on device`

```
Error writing to file - write (28: No space left on device)
```

#### 检查

```bash
df -h
du -sh /var/* | sort -h
```

`/` 只有 488 MB（overlay upper = p9 instaboot 512 MB）。

#### 常见元凶

| 目录 | 典型大小 | 处理 |
|---|---|---|
| `/var/lib/apt/lists` | 329 MB | bind 到数据盘 |
| `/var/cache/apt` | 129 MB | bind 到数据盘 |
| `/var/log` | 无限增长 | 挂到 p11 |
| `/tmp` | tmpfs 475 MB | 默认就是 tmpfs |

#### 应急清理

```bash
rm -rf /var/lib/apt/lists/*
rm -rf /var/cache/apt/archives/*.deb
apt-get clean
journalctl --vacuum-size=10M
```

我们实测这样能从 `478M 100%` 回到 `11M 3%`。

### 症状：Windows 显示的可用空间比预期少

见 [09-storage.md](09-storage.md) 的 ext4 预留章节。

```bash
tune2fs -m 1 /dev/xyagent
```

---

## 七、Samba 问题

见 [08-samba.md](08-samba.md) 的故障排查速查表。这里补充几个更隐蔽的：

### 症状：能连上，能列目录，但写文件 `NT_STATUS_ACCESS_DENIED`

```bash
# 检查目录权限
ls -ld /srv/pool
# drwxr-xr-x 2 root root   ← 755，nobody 写不了

chmod 777 /srv/pool
```

### 症状：写进去的文件找不到

`force user = nobody` 意味着文件属主是 `nobody`。
如果在另一个终端以 root 身份看，是能看到的。但如果 `path =` 指错了目录...

```bash
# 确认实际路径
grep -A3 '\[nas\]' /etc/samba/smb.conf | grep path
ls -la /srv/pool
```

### 症状：Windows 提示「你不能访问此共享文件夹，因为你组织的安全策略阻止未经身份验证的来宾访问」

这是 Windows 的来宾访问限制。

**方案 A**（推荐）：用账号连接

```cmd
net use N: \\oeanas.local\nas /user:nas YOUR_SMB_PASSWORD /persistent:yes
```

**方案 B**：Windows 组策略允许来宾

```
计算机配置 → 管理模板 → 网络 → Lanman 工作站
  → 启用不安全的来宾登录 = 已启用
```

### 症状：smbd 启动失败

```bash
systemctl status smbd
journalctl -u smbd -n 50
testparm
```

常见：`bind interfaces only = yes` + 网卡名不存在。

**改成 `bind interfaces only = no`。**

### 症状：nmbd 启动超时

```
Status: "nmbd: No local IPv4 non-IPv4 non-loopback interfaces available"
```

见 [08-samba.md](08-samba.md) 的 override 配置。

---

## 八、系统莫名其妙重启

### 我们遇到过

`uptime` 显示 `up 0 min`，`journalctl --list-boots` 只剩一次 boot，
`journalctl -b -1` 为空 —— **没有 panic 日志，看不出原因**。

### 可能原因

| 原因 | 判断 |
|---|---|
| 电源不足 | 12V 适配器功率不够，换大的 |
| 看门狗 | `/init` 启动了 `/sbin/watchdog -t 3 /dev/watchdog0` |
| 过热 | `cat /sys/class/thermal/thermal_zone0/temp` |
| eMMC 错误 | `dmesg | grep -i mmc` |

### 看门狗

原厂 `/init` 里有：

```sh
/sbin/watchdog -t 3 /dev/watchdog0
```

3 秒超时！如果系统卡住超过 3 秒不喂狗，就会硬重启。

进 Armbian 后如果没有 watchdog 守护进程喂狗，理论上会被重启。
但我们实测没有频繁重启（可能 `/init` 退出后 watchdog 也被关了）。

如果怀疑是看门狗：

```bash
# 检查
ls -l /dev/watchdog*
systemctl list-units | grep -i watchdog

# 临时禁用（重启后恢复）
echo V > /dev/watchdog0
```

---

## 九、时间不对

### 症状：`apt` 报 `Release file is not valid yet (invalid for another 5d 12h)`

板子**没有 RTC 电池**：

```bash
$ hwclock -r
hwclock: Cannot access the Hardware Clock via any known method.
$ timedatectl
RTC time: n/a
```

### 机制：fake-hwclock

Debian/Ubuntu 用 `fake-hwclock` 在关机时保存时间，开机时恢复：

```bash
systemctl status fake-hwclock-load.service
systemctl status fake-hwclock-save.service
systemctl status fake-hwclock-save.timer

cat /etc/fake-hwclock.data
# 2026-11-07 00:36:25      ← 注意存的是 UTC
```

> ⚠️ `fake-hwclock.service` 本身是 **masked** 的（兼容占位），
> 真正干活的是 `fake-hwclock-load.service` + `fake-hwclock-save.service`。

### 手动同步

```bash
# PC 端取 UTC 时间
# PowerShell: (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss')

# 板端
date -u -s '2026-11-07 00:36:25'
hwclock -w 2>/dev/null || /usr/sbin/fake-hwclock save
```

### 长期方案：chrony / systemd-timesyncd

```bash
apt-get install -y chrony
systemctl enable --now chrony
chronyc sources
```

只要有网络，开机几秒内就能同步。

---

## 十、性能相关

### 百兆网口

这块板子是 **100 Mbps**（`Link is Up - 100Mbps/Full`），理论上限 **12.5 MB/s**。

我们实测 SMB 写入约 **11 MB/s**（接近跑满）。

### 内存只有 1 GB

```bash
$ free -m
              total        used        free      shared  buff/cache   available
Mem:            950         137         623           1         189         769
Swap:             0           0           0
```

建议：

- **不要装桌面环境**
- `load printers = no`（关打印子系统）
- 用 `zram`（Armbian 默认已配）：

```bash
$ zramctl
NAME       ALGORITHM DISKSIZE DATA COMPR
/dev/zram0 lz4           237M   4K   64B
```

### eMMC 速度

```bash
$ dd if=/dev/xyagent of=/dev/null bs=1M count=64
67108864 bytes (67 MB) copied, 0.55 s, 116 MB/s
```

读写约 **116 MB/s**，比百兆网口快 10 倍，不是瓶颈。

---

## 十一、收集证据的模板

当你要在 GitHub 提 issue 时，附上这些：

```bash
# 系统信息
uname -a
cat /etc/os-release
hostnamectl

# 启动状态
systemctl is-system-running
systemctl --failed
systemctl list-units --state=failed

# 网络
ip -br addr
ip route
cat /etc/resolv.conf
systemctl status netup

# 存储
df -h
lsblk
mount | grep -E 'pool|mnt'
cat /etc/fstab

# 服务
systemctl status smbd nmbd wsdd2 avahi-daemon

# 日志（最近 100 行错误）
journalctl -b -p err -n 100 --no-pager

# 内核日志
dmesg | tail -100
```

如果是启动阶段的问题，**FIQ 的 `kmsg` 输出比 journalctl 更有价值**
（因为它不依赖 systemd 正常工作）。

---

## 十二、终极恢复手段

如果一切都坏了，只剩串口：

```bash
# 1. 进 FIQ
.\ser.ps1 -Cmd "fiq"

# 2. 切 fastboot
.\ser.ps1 -Seconds 15 -Cmd "reset bootloader"

# 3. 写阻断镜像（32 MB 空 ext4，没有 /sbin/init）
.\fbc.exe flash rootfs stub32.img

# 4. 重启
.\fbc.exe reboot

# 5. 串口登录 buildroot（空密码）
# buildroot login: root

# 6. 配网络
ifconfig eth0 10.9.8.77 netmask 255.255.255.0 up

# 7. 重新下载完整镜像
(wget -O - http://10.9.8.10:8899/final4.img | dd of=/dev/system bs=1M conv=fsync) &

# 8. 清 overlay upper（必须！）
umount -l /mnt; umount -l /low
rm -rf /high/otc_system /high/otc_workdir
mkdir -p /high/otc_system /high/otc_workdir

# 9. 重启
reboot -f
```

**这条链路救了我们无数次。**
