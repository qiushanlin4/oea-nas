# 11 · 网页面板 nasmon（监控 + 写入 + 控制）

刷完机、装完 Samba 之后，我给这块板子加了一个**单文件**的网页控制台：
浏览器打开 `http://<板子IP>:9090/` 就能看 CPU / 内存 / 温度 / 主频 / 网络速率 /
各块磁盘用量 / 服务状态 / SMB 在线客户端，还能往共享盘写文件、重启白名单内的服务、
释放缓存、以及（二次确认后）重启整机。

设计目标：**不引入任何第三方依赖**，只用板子自带的 Python 3.12 标准库。
`http.server` + `hmac` + `hashlib` + `subprocess` + `threading` 就够了，
`pip install` 一个包都不用，适配 1 GB 内存的小盒子。

> 源码：[`scripts/nasmon/nasmon.py`](../scripts/nasmon/nasmon.py)（单文件，内含前端
> HTML/CSS/JS，无任何外部资源、无 CDN）。

---

## 11.1 它解决什么问题

某次我「看到 NAS 突然死机重启了」，于是想有个地方能随时确认它到底是不是真重启了。
排查后发现**内核根本没重启**——是一次 SMB 连接瞬断造成的假象。证据链：

| 检查 | 结果 |
|---|---|
| `uptime` | 连续 10h40m，没有归零 |
| `journalctl --list-boots` | 只有 1 次 boot |
| `/proc/sys/kernel/random/boot_id` | 始终不变 |
| `dmesg \| grep -iE 'oom\|panic\|watchdog reset\|thermal\|hung\|lockup'` | 零命中 |
| 内存 / 温度 / load | 950 MB 总、756 MB available、40 °C、load 0.14 |

日志里那个 `Reached target shutdown.target` 其实是 **SSH user session 退出**，不是系统关机。
所以面板特意在前端放了一条提示条：**「只要『本次内核启动』时间和 boot_id 不变，系统就没有重启」**，
`/api/metrics` 里也一直返回 `boot_time` / `boot_id` / `uptime`，一眼就能定性。

### 顺带发现的两个真实隐患（不是死机原因，但值得知道）

1. 内核狂刷 `vcc_arm: ramp_delay not set`——CPU 调频时的无害警告，只是污染日志。
2. **三个看门狗实例并存**：内核 `[watchdogd]`、initramfs 的 `/sbin/watchdog -t 3 /dev/watchdog0`、
   Armbian 的 `/usr/sbin/watchdog`。后者日志里有
   `watchdog[640]: cannot open /dev/watchdog (errno = 16 = 'Device or resource busy')`。
   3 秒硬件狗若没被踢上会硬复位——这一点在后面的超频实验里真的触发了两次，见
   [`12-overclocking.md`](12-overclocking.md)。好消息是复位后**必定回到出厂默认频率**，
   相当于一张天然的安全网。

---

## 11.2 功能一览

| 类别 | 内容 |
|---|---|
| 采样 | 后台线程每 2 秒采一次，内存里保留最近 180 个点（约 6 分钟曲线） |
| CPU | 总占用 + 逐核占用（读 `/proc/stat` 差分）、主频、4 核数 |
| 内存 | `/proc/meminfo` 总量/可用 |
| 温度 | `/sys/class/thermal/thermal_zone*/temp`，soc-thermal |
| 磁盘 | 6 个挂载点 `statvfs`：`/`、`/mnt/d1`、`/mnt/d2`、`/mnt/d3`、`/var/log`、`/srv/pool` |
| 网络 | `/sys/class/net/*/statistics/{rx,tx}_bytes` 差分算速率 + 累计 + `ip -4 -br addr` |
| 服务 | `systemctl is-active` 查 8 个关键服务 |
| SMB | `smbstatus -b` 列出在线客户端 |
| 进程 | `ps -eo pid,comm,pcpu,pmem --sort=-pcpu` top 7 |
| 身份 | hostname / kernel / uptime / boot_time / **boot_id** |
| 前端 | 登录遮罩（可选关闭）、3 秒自动刷新、CPU/内存/温度/网络 sparkline 曲线、写入表单、文件列表、告警日志 |

**控制动作（全部走白名单，见 `do_action()`）：**

| action | 参数 | 行为 |
|---|---|---|
| `restart` | `arg` ∈ `{smbd, nmbd, wsdd2, avahi-daemon, ssh, netup}` | `systemctl restart <arg>` |
| `sync` | — | 执行一次 `sync` |
| `dropcache` | — | 写 `3` 到 `/proc/sys/vm/drop_caches` |
| `reboot` | 必须带 `confirm: true` | `systemctl reboot`（二次确认） |

白名单外的服务（例如 `sshd`）会返回 `{"ok": false, "msg": "不允许重启该服务"}`；
裸服务名当 action（例如 `{"action":"smbd"}`）会返回 `{"ok": false, "msg": "未知动作"}`——
**动作名是 `restart` 加 `arg`，不是服务名本身**。

---

## 11.3 API

默认所有接口（除 `GET /`）都要带 `X-Auth-Token` 头；开启免密后全部放行（见 11.4）。

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/` | 前端页面（无需 token） |
| `POST` | `/api/login` | `{"user":"...","pass":"..."}` → `{"ok":true,"token":"..."}` |
| `GET` | `/api/metrics` | `{"ok":true,"data":{...}}` —— **指标在 `data` 子对象里** |
| `GET` | `/api/history` | 最近 180 个采样点，供曲线用 |
| `GET` | `/api/logs` | `journalctl -p warning -n` 最近告警 |
| `GET` | `/api/files` | 列出 `/srv/pool` 根目录文件 |
| `POST` | `/api/write` | `{"name":"...","content":"..."}` 写文件到共享盘 |
| `POST` | `/api/action` | 上表的控制动作 |

### 认证实现

- `/etc/nasmon.conf`（`chmod 600`）里存 `user=admin`、`pass_sha256=<口令的 SHA256>`，
  以及可选的 `no_auth=1`。
  **仓库里不含任何明文口令**；口令由部署者自己生成后写进去。
- `/etc/nasmon.secret` 是随机 32 字节，用来签 HMAC-SHA256 token（base64 urlsafe），
  TTL 7 天。token 里带用户名 + 过期时间戳，服务端用 `hmac.compare_digest` 校验。
- 登录时用 `hashlib.sha256(pass.encode()).hexdigest()` 与配置比对，不是明文比对。

写文件接口有硬校验：文件名必须匹配 `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`
（**不能以点或下划线开头**，防止写隐藏文件），内容限制 512 KB，只能落到 `/srv/pool`。
实测 `{"name":"_panel_test.txt"}` 会被拒
`文件名不合法（仅允许字母数字 . _ -，且不以点开头）`，改成 `panel_test.txt` 即成功。

---

## 11.4 部署 + 免密模式（no_auth=1）

板子上确认环境（本机实测值）：Python **3.12.3** `/usr/bin/python3`，标准库齐全，
9090 端口空闲，无 ufw，systemd 255。

```bash
# 1) 生成你自己的口令哈希（在板子上跑，别把明文写进任何文件）
printf '你的口令' | sha256sum
# 得到一串 hex，填进 /etc/nasmon.conf：
#   user=admin
#   pass_sha256=<那串 hex>
# chmod 600 /etc/nasmon.conf

# 2) 装主程序
install -m 0755 scripts/nasmon/nasmon.py /usr/local/bin/nasmon.py
python3 -m py_compile /usr/local/bin/nasmon.py

# 3) 建 systemd unit（见下方全文）
install -m 0644 nasmon.service /etc/systemd/system/nasmon.service
systemctl daemon-reload
systemctl enable --now nasmon.service
```

`nasmon.service` 全文：

```ini
[Unit]
Description=OEA NAS web monitor & control panel
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 -u /usr/local/bin/nasmon.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

端口可用 `NASMON_PORT` 环境变量覆盖（默认 9090）。

### 家里局域网专用：改成无密码

如果面板只在可信的家庭局域网里用，可以在 `/etc/nasmon.conf` 里加一行：

```
no_auth=1
```

然后 `systemctl restart nasmon`。效果：

- 服务端 `_auth()` 直接 `return True`，所有 API 不需要 `X-Auth-Token`；
- `/api/login` 直接返回 `{"ok": true, "token": "", "msg": "no_auth"}`（保留接口兼容）；
- 页面里注入 `var NOAUTH = true;`，前端自动跳过登录遮罩、隐藏「退出登录」按钮；
- 把 `no_auth=1` 删掉（或改成 `0`）再重启即恢复口令模式。

> ⚠️ **安全边界**：`no_auth=1` 等于把「重启服务 / 写共享盘 / 重启整机」这些能力
> 对任何能访问 9090 端口的人开放。只用在隔离的家庭局域网；
> **不要**把 9090 端口映射到公网，也不要在 `no_auth=1` 时套公网反向代理。
> 需要公网访问就用 `no_auth=0` + 反向代理 + TLS。

实现方式是把 HTML 里的 `__NOAUTH__` 占位符在 `_send()` 时替换成 `true`/`false`，
前端 `showLogin()` 开头判断 `if(NOAUTH) on=false;`，启动时再加一句
`if(NOAUTH){ showLogin(false); el('btn-logout').style.display='none'; }`。
源码里没有任何明文口令。

---

## 11.5 验证（实测记录）

板端：

```bash
curl -s -o /dev/null -w '%{http_code} %{size_download}\n' http://127.0.0.1:9090/
# 200 20555
```

PC 端跨网段：

```bash
curl --noproxy '*' -s -o /dev/null -w 'PANEL_HTTP=%{http_code} SIZE=%{size_download}\n' \
  http://192.168.101.33:9090/
# PANEL_HTTP=200 SIZE=20555
```

登录取 token（**本机 PC 没有 python3**，别用 `python3 -c '...json.load...'`，
会拿到空 token 导致后面全部 `unauthorized`；用 sed 抽字符串）：

```bash
TOKEN=$(curl --noproxy '*' -s -X POST http://192.168.101.33:9090/api/login \
  -H 'Content-Type: application/json' \
  -d '{"user":"admin","pass":"你的口令"}' \
  | sed -n 's/.*"token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
echo "token_len=${#TOKEN}"   # 108
```

逐接口实测结果（口令模式下）：

| 请求 | 返回 |
|---|---|
| `POST /api/write {"name":"panel_test.txt","content":"hello from panel"}` | `{"ok": true, "path": "/srv/pool/panel_test.txt", "size": 16, "msg": "写入成功"}` |
| `GET /api/metrics`（取 `data`） | keys 含 `cpu, cpu_mhz, mem, temps, disks, net, services, smb, top, uptime, boot_id...` |
| `POST /api/action {"action":"restart","arg":"smbd"}` | `{"ok": true, "msg": "已重启 smbd"}` |
| `POST /api/action {"action":"dropcache"}` | `{"ok": true, "msg": "已释放页缓存 / dentries / inodes"}` |
| `POST /api/action {"action":"sync"}` | `{"ok": true, "msg": "已执行 sync，缓存已落盘"}` |
| `POST /api/action {"action":"reboot"}` | `{"ok": false, "msg": "需要二次确认"}` |
| `POST /api/action {"action":"restart","arg":"sshd"}` | `{"ok": false, "msg": "不允许重启该服务"}` |

免密模式（`no_auth=1`）下 PC 跨网实测：

| 请求 | 返回 |
|---|---|
| `GET /` （不带任何 header） | `200`，20741 B；页面里 `var NOAUTH = true;`、无 `__NOAUTH__` 残留 |
| `GET /api/metrics` （不带 token） | `{"ok": true, "data": {...}}` |
| `GET /api/files` （不带 token） | `{"ok": true, "data": [...]}` |
| `POST /api/write` （不带 token） | `{"ok": true, "path": "/srv/pool/...", ...}` |

---

## 11.6 二次开发注意

- 前端是内嵌在 `HTML` 常量里的单页，改完直接 `systemctl restart nasmon` 生效。
- 在 SMB 共享（NAS）上编辑已存在的 `nasmon.py` 时，`edit`/`write` 类工具会踩 ACL 报错
  （`SetFileSecurityW EACCES`），要用 `ops_bash` + `sed` / `python3` 原地改；
  `.sh` 脚本落盘后记得 `sed -i "s/\r$//"` 去 CRLF。
- `sampler()` 里任何异常会写到 `/tmp/nasmon.err`，不会拖垮 HTTP 线程。
- `collect()` 全部走 `subprocess` 调系统命令，新增指标只要加一个函数并在 `collect()` 里塞字段，
  前端 `render()` 里读 `d.data.<字段>` 即可。
- 用 `HTTP/1.1` + `ThreadingHTTPServer`；口令模式本身也只有 HMAC token，没有速率限制
  和防爆破，公网部署务必再套一层反向代理 + TLS。
