# 板端脚本

## netup.sh —— 网络启动脚本

装到 `/usr/local/sbin/netup.sh`，由 `netup.service` 在启动时调用。

### 它解决什么问题

原厂 initramfs 启动后：

- Ubuntu Server 默认的 `10-globally-managed-devices.conf` **把有线网卡排除在
  NetworkManager 管理之外**
- netplan 配置里**没有 `ethernets:` 段**
- `/etc/network/interfaces` **是空的**

结果：**没有任何机制把网卡 up 起来**，板子不可达。

### 设计要点

| 特性 | 为什么 |
|---|---|
| 只选一个物理网卡 | 跳过 `lo` / `wl*` / `tun*` / `docker*` / `veth*` / `br-*` |
| 等 carrier 最多 25 秒 | 路由器端口协商需要时间 |
| **永远装 `10.9.8.77/24`** | 直连 PC 的救援通道永不丢失 |
| dhcpcd **常驻不杀** | 网线插到路由器后自动拿地址，不用重启 |
| DNS 兜底 | `systemd-resolved` 已关，`resolv.conf` 必须是真实文件 |

### ⚠️ 最关键的教训

**第一版（v6）在 DHCP 失败后执行 `pkill -f dhcpcd`。**

后果：板子启动时网线插在 PC 上（没有 DHCP 服务器）→ dhcpcd 超时被杀 →
**后来把网线改插路由器，板子永远停在 `10.9.8.77`，不会重新发起 DHCP**。

我们因此失联过一次，最后靠物理拔插网线 + 全子网扫描才找回来。

**v8 改成常驻**，`dhcpcd` 自己会监听 link 状态变化。

### 踩过的坑：追加到 `exit 0` 之后

我们曾经用 `cat >> netup.sh` 追加 DNS 修复块，结果**追加到了 `exit 0` 之后**，
永远不执行。

**部署前必须检查**：

```bash
sh -n /usr/local/sbin/netup.sh          # 语法
grep -n 'exit 0\|DNSEOF' /usr/local/sbin/netup.sh   # 顺序
```

---

## netup.service

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

要点：

- `DefaultDependencies=no` + `Before=network.target` → 比网络服务先跑
- `Type=oneshot` + `RemainAfterExit=yes` → 跑完标记为 active（不重复执行）
- `TimeoutStartSec=60` → 给 carrier 等待（25s）+ DHCP（20s）留足余量

---

## install.sh —— 一键应用配置

```bash
sh install.sh
```

做这些事：

1. 关掉 `NetworkManager` 和 `systemd-resolved`
2. 安装 `netup.sh` + `netup.service` 并启用
3. 主机名改成 `oeanas`，装并启用 `avahi-daemon` / `wsdd2`
4. 重写 `/etc/resolv.conf` 为真实文件
5. 启用 `smbd` / `nmbd`（如果已装）
6. 打印状态和失败单元

**不会**碰 `/etc/fstab`，也不会建 mergerfs 池 —— 那两个需要按
[docs/09-storage.md](../../docs/09-storage.md) 手工做（因为要确认分区内容）。

---

## 部署方式

### 网络通的时候

```bash
# PC
scp netup.sh netup.service install.sh root@192.168.101.33:/tmp/
ssh root@192.168.101.33 'sh /tmp/install.sh'
```

### 网络不通的时候（用 HTTP）

```bash
# PC 端起服务器
powershell -File ..\pc\httpsrv.ps1 -Port 8899 -Root .

# 板子（buildroot shell）
wget -q -O /tmp/netup.sh http://10.9.8.10:8899/netup.sh
wget -q -O /tmp/netup.service http://10.9.8.10:8899/netup.service
install -m 0755 /tmp/netup.sh /usr/local/sbin/netup.sh
install -m 0644 /tmp/netup.service /etc/systemd/system/netup.service
systemctl daemon-reload
systemctl enable netup
```

### 用 SSH stdin 传（网络半通时）

```bash
cat netup.sh | ssh root@10.9.8.77 "cat > /tmp/netup.sh && sh -n /tmp/netup.sh && install -m 0755 /tmp/netup.sh /usr/local/sbin/netup.sh"
```

这个方式我们实际用过 —— 当时 PC 的以太网已经断开、`wget` 到 `8899` 失败，
但 SSH 还能连上 `10.9.8.77`。

> ⚠️ **不要用串口传脚本**。1500000 波特率下超过 200 字符就会丢字符，
> base64 长行实测直接乱码。
