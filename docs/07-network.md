# 07 · 网络：从零到能上网

## 问题：系统起来了，但网卡是死的

第一次成功启动 Armbian 后，SSH 连不上、ping 不通。用 FIQ 的 `kmsg` 看内核日志：

```
[    5.568298] rk_gmac-dwmac ff550000.ethernet end1: renamed from eth0
```

两个信息：
1. 网卡被 systemd 的可预测命名规则改成了 **`end1`**（不是 `eth0`）
2. **整个日志里没有一行 `Link is Up`** —— 没有任何程序把网卡 admin-up

### 三重缺失

排查后发现是一个「三重缺失」的完美风暴：

| 位置 | 内容 | 问题 |
|---|---|---|
| `/usr/lib/NetworkManager/conf.d/10-globally-managed-devices.conf` | `unmanaged-devices=*,except:type:wifi,except:type:gsm,except:type:cdma` | **Ubuntu Server 默认把有线网卡排除在 NM 管理之外** |
| `/etc/netplan/00-default-use-network-manager.yaml` | 只有 `network: version: 2` + `renderer: NetworkManager` | 没有 `ethernets:` 段 |
| `/etc/network/interfaces` | 空文件 | 没有 ifupdown 配置 |
| `/etc/NetworkManager/system-connections/` | 空目录 | 没有连接配置 |

**结论：没有任何机制会 up 网卡。**

---

## 方案一（失败）：修 NetworkManager

我们一开始尝试「正确」的路子：

```bash
# 1. 让 NM 管理有线网卡
cat > /etc/NetworkManager/conf.d/99-allow-ethernet.conf <<'EOF'
[keyfile]
unmanaged-devices=*,except:type:wifi,except:type:gsm,except:type:cdma,except:type:ethernet
EOF

# 2. 建连接
nmcli con add type ethernet ifname eth0 con-name eth0 ipv4.method auto
```

### 为什么失败

1. **`except:type:ethernet` 的语义是反的** —— 它表示「除了有线，其他都 unmanaged」，
   也就是**把有线纳入了管理**（我们本意是排除）。
2. NM 自动生成了 `eth0.nmconnection`（`method=auto` + `address1=10.9.8.77/24`），
   但直连 PC 没有 DHCP 服务器 → 拿不到地址 → NM 把地址清空。
3. **更糟**：NM 和 `dhcpcd` 抢网卡。`ps -ef | grep dhcpcd` 看到 **5 个 dhcpcd 进程**
   （主进程 + privileged proxy / network proxy / control proxy / BPF BOOTP）。
   两边互相把对方的地址删掉，最后 `ip -br addr` 显示 `eth0 UP` 但**无 IP**。

我们最后：

```bash
systemctl disable --now NetworkManager
rm -f /etc/NetworkManager/system-connections/eth0.nmconnection
rm -f /etc/NetworkManager/conf.d/99-allow-ethernet.conf
pkill -f dhcpcd
```

**彻底放弃 NetworkManager。**

---

## 方案二（成功）：自己的 netup.sh + systemd oneshot

### 设计目标

1. 只选**一个**物理网卡（不碰 lo / 虚拟网卡）
2. 等 carrier（路由器端口协商可能要几秒）
3. **永远装一个救援地址 `10.9.8.77/24`** —— 直连 PC 的通道永不丢失
4. dhcpcd **常驻不杀** —— 网线插到路由器后自动拿地址，不用重启
5. DNS 兜底（`systemd-resolved` 关掉，用普通文件）

### `/usr/local/sbin/netup.sh`

```sh
#!/bin/sh
# /usr/local/sbin/netup.sh -- bring up the single wired NIC (v8)
#
# Why this exists:
#   The factory initramfs boots us into an overlay root.  Ubuntu Server's
#   NetworkManager is configured (10-globally-managed-devices.conf) to ignore
#   *wired* devices, netplan has no ethernets section, and /etc/network/interfaces
#   is empty.  Result: nothing ever brings eth0 up and the box is unreachable.

STATIC_IP="10.9.8.77"
STATIC_PREFIX="24"
IFACE=""

# --- pick exactly one wired NIC -------------------------------------------
for d in /sys/class/net/*; do
    n=${d##*/}
    [ "$n" = "lo" ] && continue
    [ -e "$d/device" ] || continue
    case "$n" in
        wl*|wlan*|tun*|sit*|ip6*|ip_vti*|docker*|veth*|br-*|virbr*) continue ;;
    esac
    IFACE="$n"
done

[ -z "$IFACE" ] && exit 0

/bin/ip link set "$IFACE" up 2>/dev/null

# --- wait for carrier (max 25s) -------------------------------------------
i=0
while [ $i -lt 25 ]; do
    [ "$(cat /sys/class/net/$IFACE/carrier 2>/dev/null)" = "1" ] && break
    sleep 1
    i=$((i + 1))
done

# --- always install the rescue address first ------------------------------
/bin/ip addr add "${STATIC_IP}/${STATIC_PREFIX}" dev "$IFACE" 2>/dev/null

# --- DHCP daemon (survives cable moves) -----------------------------------
if command -v dhcpcd >/dev/null 2>&1; then
    if ! pgrep -f "dhcpcd.*${IFACE}" >/dev/null 2>&1; then
        dhcpcd --nohook resolv.conf,hostname,ntpconf --timeout 20 "$IFACE" \
            >/dev/null 2>&1
    fi
fi

# --- keep DNS working (resolv.conf is a plain file; systemd-resolved is off)
if [ ! -s /etc/resolv.conf ] || ! grep -q "^nameserver" /etc/resolv.conf 2>/dev/null; then
    cat > /etc/resolv.conf <<'DNSEOF'
nameserver 192.168.101.1
nameserver 223.5.5.5
nameserver 119.29.29.29
options edns0 trust-ad timeout:2 attempts:2
DNSEOF
    chmod 644 /etc/resolv.conf
fi

sleep 4
/bin/ip -br addr > /dev/console 2>&1 || true
exit 0
```

### `/etc/systemd/system/netup.service`

```ini
[Unit]
Description=Bring up ethernet interfaces (DHCP with static fallback)
DefaultDependencies=no
After=systemd-udevd.service
Before=network.target network-online.target
Wants=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
TimeoutStartSec=60
ExecStart=/usr/local/sbin/netup.sh

[Install]
WantedBy=multi-user.target
WantedBy=network-online.target
```

安装：

```bash
install -m 0755 netup.sh /usr/local/sbin/netup.sh
install -m 0644 netup.service /etc/systemd/system/netup.service
systemctl daemon-reload
systemctl enable netup
systemctl start netup
```

### 关键设计说明

#### 为什么救援地址要「永远」装

我们后来发现，`10.9.8.77/24` 和路由器分配的 `192.168.101.33/24` **可以并存**：

```
$ ip -4 -br addr
eth0  UP  10.9.8.77/24 192.168.101.33/24
```

这意味着：
- 网线插路由器 → 两个地址都在，WiFi 设备访问 `192.168.101.33`，PC 直连访问 `10.9.8.77`
- 网线插 PC → 静态地址仍在，救援通道不丢

**这是本项目最实用的一招。**

#### 为什么 dhcpcd 不能杀

我们第一版（v6）在 DHCP 失败后执行 `pkill -f dhcpcd`。

后果：板子启动时网线插在 PC 上（没有 DHCP 服务器）→ dhcpcd 超时被杀 →
**后来把网线改插路由器，板子永远停在 `10.9.8.77`，不会重新发起 DHCP**。

我们因此失联过一次，最后靠物理拔插网线 + `deepscan.ps1` 全子网扫描才找回来。

v8 改成**常驻**：`dhcpcd` 自己会监听 link 状态变化，网线一插到路由器就自动 DISCOVER。

#### ⚠️ 脚本必须 `sh -n` 检查

我们踩过一个坑：用 `cat >> netup.sh` 追加 DNS 修复块时，**追加到了 `exit 0` 之后**，
永远不会执行。

```bash
sh -n /usr/local/sbin/netup.sh       # 语法检查
grep -n 'exit 0\|DNSEOF' netup.sh    # 确认顺序
```

---

## DNS 修复

### 问题

初始 `/etc/resolv.conf` 是符号链接：

```
/etc/resolv.conf -> /run/systemd/resolve/stub-resolv.conf
```

但 `systemd-resolved` 没配上游：

```
$ resolvectl status
Link 2 (eth0)
    Current Scopes: none
    ...
$ getent hosts baidu.com
（RC=2，解析失败）
```

### 修复

**关掉 systemd-resolved，用普通文件**：

```bash
systemctl disable --now systemd-resolved
rm -f /etc/resolv.conf
cat > /etc/resolv.conf <<'EOF'
nameserver 192.168.101.1
nameserver 223.5.5.5
nameserver 119.29.29.29
options edns0 trust-ad timeout:2 attempts:2
EOF
chmod 644 /etc/resolv.conf
```

验证：

```bash
getent hosts baidu.com
# 110.242.74.102  baidu.com
ping -c 2 223.5.5.5
# 0% packet loss, 14.7-20.3 ms
```

> 注意：`systemd-resolved` 会不断把 `/etc/resolv.conf` 改回符号链接。
> 必须先 `disable --now`，再删链接。

---

## 直连 PC 与接路由器两种拓扑

### 拓扑 A：直连（无路由器，救援用）

```
PC (10.9.8.10/24) ←—— 网线 ——→ 板子 (10.9.8.77/24 静态)
```

PC 侧配置（Windows）：

```
控制面板 → 网络连接 → 以太网 → 属性 → IPv4
IP:      10.9.8.10
掩码:    255.255.255.0
网关:    （空）
```

板子侧：`netup.sh` 自动装 `10.9.8.77/24`。

### 拓扑 B：接路由器（正常使用）

```
                      ┌── 手机 (WiFi)
路由器 (DHCP server) ──┼── 笔记本 (WiFi)
   192.168.101.1      └── 板子 (有线, DHCP → 192.168.101.33)
```

板子侧：`netup.sh` 跑 dhcpcd，拿到 `192.168.101.33`，同时保留 `10.9.8.77`。

### 切换拓扑不需要重启

因为 dhcpcd 常驻，网线从 PC 换到路由器，几秒内自动适应。

**但要注意**：如果 PC 的以太网还停在 `10.9.8.10/24`，就看不到路由器网段。
PC 要么用 WiFi（`192.168.101.3`），要么把以太网改成 DHCP。

---

## 主机名与 mDNS（免记 IP）

装了 avahi 之后，可以用 `oeanas.local` 访问，不用记 IP：

```bash
apt-get install -y --no-install-recommends avahi-daemon libnss-mdns

hostnamectl set-hostname oeanas
sed -i 's/127.0.1.1.*/127.0.1.1\toeanas/' /etc/hosts

systemctl enable --now avahi-daemon
systemctl restart avahi-daemon
```

验证：

```bash
# 板子自己
getent hosts oeanas.local
# 10.9.8.77   oeanas.local

# PC
ping oeanas.local
```

Windows 10/11 原生支持 mDNS（Bonjour 或系统自带）。

> 如果 PC 装不了 mDNS，还可以用 NetBIOS：`nbtstat -A 10.9.8.77`
> 应该看到 `OEA-NAS <00> UNIQUE Registered`。

---

## 网络排查工具箱

### 1. FIQ `kmsg`（最强）

```bash
.\ser.ps1 -Cmd "fiq"
.\ser.ps1 -Seconds 12 -Cmd "kmsg"
```

搜这几行：

| 关键字 | 含义 |
|---|---|
| `renamed from eth0` | 网卡被改名了 |
| `Link is Up` | **有这行 = 物理链路通了** |
| `No such device` | 网卡名写错了 |
| `dhcpcd` | DHCP 客户端状态 |

### 2. 全子网扫描（`deepscan.ps1`）

板子失联时，扫整个子网找开 22/445 的主机：

```powershell
$subnet = "192.168.101"
1..254 | ForEach-Object -Parallel {
    $ip = "$using:subnet.$_"
    if (Test-Connection $ip -Count 1 -Quiet -TimeoutSeconds 1) {
        $ports = 22, 139, 445 | Where-Object {
            (Test-NetConnection $ip -Port $_ -WarningAction SilentlyContinue).TcpTestSucceeded
        }
        Write-Output "$ip alive ports=$($ports -join ',')"
    }
} -ThrottleLimit 64
```

> **⚠️ 警惕 AP 代理 ARP**：我们扫描时看到 `192.168.101.85` 和 `.86` 都「在线」，
> 但 `arp -a` 显示它们映射到**同一个 MAC**（路由器自己）。
> 这是路由器的代理 ARP，`ping` 打到的是路由器，不是真实主机。
> **判据：真板子一定开 22 和 445。**

### 3. mDNS 查询

```powershell
# 手写 DNS 报文发到 224.0.0.251:5353
# 查 _smb._tcp.local 或 oeanas.local
```

如果返回 0 条 → 板子不在这个网段（或 avahi 没跑）。

---

## 完整网络配置清单

```bash
# 1. 关掉 NetworkManager（避免抢网卡）
systemctl disable --now NetworkManager

# 2. 关掉 systemd-resolved（用普通 resolv.conf）
systemctl disable --now systemd-resolved

# 3. 装 netup.sh
install -m 0755 netup.sh /usr/local/sbin/netup.sh
install -m 0644 netup.service /etc/systemd/system/netup.service
systemctl daemon-reload
systemctl enable --now netup

# 4. 主机名 + mDNS
hostnamectl set-hostname oeanas
apt-get install -y --no-install-recommends avahi-daemon libnss-mdns
systemctl enable --now avahi-daemon

# 5. DNS
rm -f /etc/resolv.conf
printf 'nameserver 192.168.101.1\nnameserver 223.5.5.5\nnameserver 119.29.29.29\n' \
    > /etc/resolv.conf

# 6. 验证
systemctl is-system-running       # running
systemctl --failed                # 0 loaded units
ip -4 -br addr                    # eth0 UP 10.9.8.77/24 192.168.101.33/24
getent hosts baidu.com            # 有 IP
ping -c 2 223.5.5.5               # 0% loss
```

下一节 → [08-samba.md](08-samba.md)
