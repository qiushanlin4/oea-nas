#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nasmon.py - OEA NAS 网页监控 / 控制端
仅使用 Python 3 标准库，单文件，无第三方依赖。
运行: python3 /usr/local/bin/nasmon.py
端口: 9090 (可用环境变量 NASMON_PORT 覆盖)
认证: /etc/nasmon.conf 里的 user + pass_sha256；no_auth=1 时免密（仅限局域网）
"""
import os
import sys
import re
import json
import time
import hmac
import hashlib
import base64
import socket
import threading
import subprocess
from urllib.parse import urlparse, parse_qs, quote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONF = "/etc/nasmon.conf"
SECRET_FILE = "/etc/nasmon.secret"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/usr/local/lib/nasmon")
try:
    import filemgr
except Exception as _e:  # pragma: no cover
    filemgr = None
    sys.stderr.write("filemgr import failed: %r\n" % (_e,))

SHARE = "/srv/pool"
PORT = int(os.environ.get("NASMON_PORT", "9090"))
HISTORY_MAX = 180          # 180 * 2s = 6 分钟
SAMPLE_INTERVAL = 2.0
TOKEN_TTL = 7 * 24 * 3600

LOCK = threading.Lock()
LATEST = None
HISTORY = []
_prev_cpu = None
_prev_net = None
_prev_net_ts = None


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
def read(path, default=""):
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except Exception:
        return default


def sh(cmd, timeout=8):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True,
                           text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def load_conf():
    c = {"user": "admin", "pass_sha256": "", "no_auth": "0"}
    try:
        for line in open(CONF, "r"):
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                c[k.strip()] = v.strip()
    except Exception:
        pass
    return c


def load_secret():
    try:
        return open(SECRET_FILE, "rb").read()
    except Exception:
        s = os.urandom(32)
        try:
            with open(SECRET_FILE, "wb") as f:
                f.write(s)
            os.chmod(SECRET_FILE, 0o600)
        except Exception:
            pass
        return s


CONF_DATA = load_conf()
NO_AUTH = CONF_DATA.get("no_auth", "0") == "1"
SECRET = load_secret()


def make_token(user):
    exp = int(time.time()) + TOKEN_TTL
    payload = "%s|%d" % (user, exp)
    sig = hmac.new(SECRET, payload.encode(), hashlib.sha256).hexdigest()
    raw = ("%s|%s" % (payload, sig)).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def verify_token(tok):
    if not tok:
        return False
    try:
        pad = "=" * (-len(tok) % 4)
        raw = base64.urlsafe_b64decode((tok + pad).encode()).decode()
        user, exp, sig = raw.rsplit("|", 2)
        if int(exp) < int(time.time()):
            return False
        payload = "%s|%s" % (user, exp)
        want = hmac.new(SECRET, payload.encode(), hashlib.sha256).hexdigest()
        return user == CONF_DATA.get("user", "admin") and hmac.compare_digest(sig, want)
    except Exception:
        return False


# --------------------------------------------------------------------------
# 指标采集
# --------------------------------------------------------------------------
def cpu_usage():
    global _prev_cpu
    cur = {}
    try:
        with open("/proc/stat", "r") as f:
            for line in f:
                if not line.startswith("cpu"):
                    continue
                p = line.split()
                nums = [int(x) for x in p[1:]]
                if len(nums) < 4:
                    continue
                idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
                cur[p[0]] = (sum(nums), idle)
    except Exception:
        return {"total": 0.0, "cores": []}
    if _prev_cpu is None:
        _prev_cpu = cur
        return {"total": 0.0, "cores": [[k, 0.0] for k in sorted(cur) if k != "cpu"]}
    total, cores = 0.0, []
    for k, (t, i) in cur.items():
        if k not in _prev_cpu:
            continue
        pt, pi = _prev_cpu[k]
        dt, di = t - pt, i - pi
        u = 100.0 * (1.0 - di / dt) if dt > 0 else 0.0
        u = round(max(0.0, min(100.0, u)), 1)
        if k == "cpu":
            total = u
        else:
            cores.append([k, u])
    _prev_cpu = cur
    return {"total": total, "cores": sorted(cores)}


def mem_info():
    d = {}
    try:
        for line in open("/proc/meminfo"):
            k, v = line.split(":", 1)
            d[k.strip()] = int(v.split()[0]) * 1024
    except Exception:
        pass
    total = d.get("MemTotal", 0)
    avail = d.get("MemAvailable", d.get("MemFree", 0))
    used = total - avail
    swap_t = d.get("SwapTotal", 0)
    swap_f = d.get("SwapFree", 0)
    return {
        "total": total, "used": used, "available": avail,
        "percent": round(100.0 * used / total, 1) if total else 0,
        "buffers": d.get("Buffers", 0), "cached": d.get("Cached", 0),
        "swap_total": swap_t, "swap_used": swap_t - swap_f,
    }


def temps():
    out = []
    base = "/sys/class/thermal"
    try:
        for z in sorted(os.listdir(base)):
            if not z.startswith("thermal_zone"):
                continue
            p = os.path.join(base, z)
            t = read(os.path.join(p, "temp"))
            if not t:
                continue
            typ = read(os.path.join(p, "type"), z)
            try:
                out.append({"name": typ, "c": round(int(t) / 1000.0, 1)})
            except Exception:
                pass
    except Exception:
        pass
    return out


def cpu_freq():
    v = read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")
    if v and v.isdigit():
        return int(v) // 1000
    return None


def disks():
    paths = [
        ("/", "系统 (overlay)"),
        ("/srv/pool", "共享池 (合并)"),
        ("/mnt/d1", "数据盘 108G"),
        ("/mnt/d2", "回收盘 3.4G"),
        ("/mnt/d3", "回收盘 1G"),
        ("/var/log", "日志盘"),
    ]
    out = []
    for p, label in paths:
        try:
            st = os.statvfs(p)
            total = st.f_blocks * st.f_frsize
            free = st.f_bavail * st.f_frsize
            used = total - free
            out.append({
                "path": p, "label": label, "total": total, "used": used,
                "free": free,
                "percent": round(100.0 * used / total, 1) if total else 0,
            })
        except Exception:
            pass
    return out


IF_SKIP = ("lo", "sit0", "tunl0")


def netdev():
    global _prev_net, _prev_net_ts
    now = time.time()
    cur = {}
    base = "/sys/class/net"
    try:
        for name in sorted(os.listdir(base)):
            if name in IF_SKIP or name.startswith(("ip6_", "ip_vti", "veth", "docker", "br-")):
                continue
            rx = read(os.path.join(base, name, "statistics/rx_bytes"), "0")
            tx = read(os.path.join(base, name, "statistics/tx_bytes"), "0")
            state = read(os.path.join(base, name, "operstate"), "unknown")
            speed = read(os.path.join(base, name, "speed"), "")
            try:
                cur[name] = {"rx": int(rx), "tx": int(tx),
                             "state": state, "speed": speed}
            except Exception:
                pass
    except Exception:
        pass

    # IPv4 地址（一条命令搞定）
    addrs = {}
    try:
        for line in sh("ip -4 -br addr show", 5).splitlines():
            parts = line.split()
            if len(parts) >= 3:
                addrs.setdefault(parts[0], []).append(parts[2])
    except Exception:
        pass

    rates = {}
    if _prev_net is not None and _prev_net_ts is not None:
        dt = now - _prev_net_ts
        if dt > 0:
            for name, v in cur.items():
                if name in _prev_net:
                    rates[name] = {
                        "rx": max(0, int((v["rx"] - _prev_net[name]["rx"]) / dt)),
                        "tx": max(0, int((v["tx"] - _prev_net[name]["tx"]) / dt)),
                    }
    _prev_net, _prev_net_ts = cur, now

    items = []
    for name, v in cur.items():
        r = rates.get(name, {"rx": 0, "tx": 0})
        items.append({
            "name": name, "rx": v["rx"], "tx": v["tx"],
            "rx_rate": r["rx"], "tx_rate": r["tx"],
            "state": v["state"], "speed": v["speed"],
            "addrs": addrs.get(name, []),
        })
    return items


SERVICES = ["smbd", "nmbd", "wsdd2", "avahi-daemon", "ssh",
            "netup", "watchdog", "systemd-resolved"]


def services():
    out = {}
    raw = sh("systemctl is-active " + " ".join(SERVICES), 8)
    lines = [x.strip() for x in raw.splitlines()]
    for i, s in enumerate(SERVICES):
        out[s] = lines[i] if i < len(lines) else "unknown"
    return out


def smb_clients():
    raw = sh("smbstatus -b 2>/dev/null", 6)
    items = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith(("Samba version", "PID", "---", "Service")):
            continue
        # brief 格式: PID  Username  Group  Machine  Protocol Version  Encryption  Signing
        parts = line.split()
        if len(parts) >= 4 and parts[0].isdigit():
            items.append({"pid": parts[0], "user": parts[1],
                          "machine": parts[3], "proto": parts[4] if len(parts) > 4 else ""})
    return items


def top_procs():
    raw = sh("ps -eo pid,comm,pcpu,pmem --sort=-pcpu | head -n 7", 6)
    items = []
    for line in raw.splitlines()[1:]:
        p = line.split(None, 3)
        if len(p) >= 4:
            items.append({"pid": p[0], "name": p[1],
                          "cpu": float(p[2]), "mem": float(p[3])})
    return items


def collect():
    up = read("/proc/uptime", "0 0").split()
    up_s = float(up[0]) if up else 0.0
    load = read("/proc/loadavg", "0 0 0 0/0 0").split()
    try:
        procs_running = int(load[3].split("/")[0])
    except Exception:
        procs_running = 0
    nproc = 0
    try:
        nproc = sum(1 for l in open("/proc/cpuinfo") if l.startswith("processor"))
    except Exception:
        pass
    return {
        "ts": time.time(),
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "hostname": socket.gethostname(),
        "kernel": read("/proc/sys/kernel/osrelease", ""),
        "os": sh(". /etc/os-release 2>/dev/null; echo $PRETTY_NAME", 5),
        "uptime": up_s,
        "boot_time": time.strftime("%Y-%m-%d %H:%M:%S",
                                   time.localtime(time.time() - up_s)),
        "boot_id": read("/proc/sys/kernel/random/boot_id", ""),
        "load": [float(x) for x in load[:3]],
        "nproc": nproc,
        "procs_running": procs_running,
        "cpu": cpu_usage(),
        "mem": mem_info(),
        "temps": temps(),
        "cpu_mhz": cpu_freq(),
        "disks": disks(),
        "net": netdev(),
        "services": services(),
        "smb": smb_clients(),
        "top": top_procs(),
    }


def sampler():
    global LATEST, HISTORY
    while True:
        try:
            snap = collect()
            with LOCK:
                LATEST = snap
                HISTORY.append({
                    "t": snap["ts"], "cpu": snap["cpu"]["total"],
                    "mem": snap["mem"]["percent"],
                    "temp": snap["temps"][0]["c"] if snap["temps"] else 0,
                    "rx": sum(n["rx_rate"] for n in snap["net"]),
                    "tx": sum(n["tx_rate"] for n in snap["net"]),
                    "load": snap["load"][0],
                })
                if len(HISTORY) > HISTORY_MAX:
                    HISTORY = HISTORY[-HISTORY_MAX:]
        except Exception as e:
            with open("/tmp/nasmon.err", "a") as f:
                f.write(time.strftime("%F %T") + " sampler: " + repr(e) + "\n")
        time.sleep(SAMPLE_INTERVAL)


# --------------------------------------------------------------------------
# 控制动作（白名单）
# --------------------------------------------------------------------------
RESTARTABLE = {"smbd", "nmbd", "wsdd2", "avahi-daemon", "ssh", "netup"}


def do_action(body):
    act = str(body.get("action", ""))
    arg = str(body.get("arg", ""))
    confirm = body.get("confirm", False)
    if act == "restart":
        if arg not in RESTARTABLE:
            return {"ok": False, "msg": "不允许重启该服务"}
        r = subprocess.run(["systemctl", "restart", arg],
                           capture_output=True, text=True, timeout=30)
        return {"ok": r.returncode == 0,
                "msg": ("已重启 " + arg) if r.returncode == 0 else r.stderr.strip()}
    if act == "sync":
        subprocess.run(["sync"], timeout=30)
        return {"ok": True, "msg": "已执行 sync，缓存已落盘"}
    if act == "dropcache":
        try:
            with open("/proc/sys/vm/drop_caches", "w") as f:
                f.write("3")
            return {"ok": True, "msg": "已释放页缓存 / dentries / inodes"}
        except Exception as e:
            return {"ok": False, "msg": str(e)}
    if act == "reboot":
        if not confirm:
            return {"ok": False, "msg": "需要二次确认"}
        subprocess.Popen(["systemctl", "reboot"])
        return {"ok": True, "msg": "重启命令已发出，页面将在稍后断开"}
    return {"ok": False, "msg": "未知动作"}


def do_write(body):
    name = str(body.get("name", ""))
    content = str(body.get("content", ""))
    name = os.path.basename(name).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name):
        return {"ok": False, "msg": "文件名不合法（仅允许字母数字 . _ -，且不以点开头）"}
    if len(content.encode("utf-8")) > 512 * 1024:
        return {"ok": False, "msg": "内容超过 512 KB"}
    path = os.path.join(SHARE, name)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"ok": True, "path": path,
                "size": os.path.getsize(path), "msg": "写入成功"}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


def list_files():
    out = []
    try:
        for name in sorted(os.listdir(SHARE)):
            p = os.path.join(SHARE, name)
            if os.path.isfile(p) and not name.startswith("."):
                st = os.stat(p)
                out.append({"name": name, "size": st.st_size,
                            "mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                                   time.localtime(st.st_mtime))})
    except Exception:
        pass
    return out[-40:]


def recent_logs(n=25):
    raw = sh("journalctl -p warning -n %d --no-pager -o short-iso 2>/dev/null" % int(n), 12)
    return raw.splitlines()[-int(n):]


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OEA NAS 监控面板</title>
<style>
:root{--bg:#0b1016;--card:#141c26;--card2:#182430;--line:#243241;--txt:#e6edf3;--dim:#8b9bab;--ok:#3fb950;--warn:#d29922;--bad:#f85149;--accent:#58a6ff;--accent2:#a371f7}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}
a{color:var(--accent)}
.wrap{max-width:1500px;margin:0 auto;padding:18px}
header{display:flex;flex-wrap:wrap;align-items:center;gap:14px;padding:14px 18px;background:linear-gradient(90deg,#122030,#0f1822);border:1px solid var(--line);border-radius:12px;margin-bottom:16px}
header h1{font-size:19px;margin:0;letter-spacing:.5px}
header .host{color:var(--accent);font-weight:600}
.badge{padding:3px 10px;border-radius:20px;font-size:12px;background:#1b2b3a;border:1px solid var(--line);color:var(--dim)}
.badge.live{background:#12351f;border-color:#265c38;color:var(--ok)}
.spacer{flex:1}
.btn{cursor:pointer;background:#1b2836;color:var(--txt);border:1px solid var(--line);border-radius:8px;padding:7px 13px;font-size:13px;transition:.15s}
.btn:hover{border-color:var(--accent);background:#20344a}
.btn.danger{border-color:#5a2a2a;color:#ff9c95}
.btn.danger:hover{background:#3a1c1c;border-color:var(--bad)}
.btn:disabled{opacity:.45;cursor:not-allowed}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;min-width:0}
.card h2{font-size:13px;margin:0 0 14px;color:var(--dim);font-weight:600;letter-spacing:.4px;text-transform:uppercase;display:flex;justify-content:space-between;align-items:center}
.big{font-size:34px;font-weight:700;letter-spacing:-1px;line-height:1.1}
.unit{font-size:14px;color:var(--dim);font-weight:400;margin-left:4px}
.row{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px dashed #1e2a36;font-size:13px}
.row:last-child{border-bottom:0}
.row .k{color:var(--dim)}
.bar{height:7px;background:#1d2935;border-radius:5px;overflow:hidden;margin-top:6px}
.bar>i{display:block;height:100%;background:linear-gradient(90deg,var(--accent),#2f81f7);border-radius:5px;transition:width .5s}
.bar.mem>i{background:linear-gradient(90deg,var(--accent2),#8b5cf6)}
.bar.disk>i{background:linear-gradient(90deg,var(--ok),#238636)}
canvas{width:100%;height:46px;display:block;margin-top:8px}
.tag{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:600}
.tag.on{background:#12351f;color:var(--ok)}.tag.off{background:#3a1c1c;color:#ff9c95}.tag.other{background:#332a12;color:var(--warn)}
.core{display:flex;align-items:center;gap:8px;margin:5px 0;font-size:12px}
.core span{width:34px;color:var(--dim)}
.core .bar{flex:1;margin:0}
.core b{width:42px;text-align:right;font-weight:600}
.note{font-size:12px;color:var(--dim);margin-top:10px;line-height:1.6}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
.hl{background:#10233a;border:1px solid #1f4060;border-radius:8px;padding:10px 12px;font-size:12.5px;color:#c9dcf0;margin-bottom:12px}
input,textarea,select{background:#0e1620;color:var(--txt);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font:inherit;width:100%;outline:none}
input:focus,textarea:focus{border-color:var(--accent)}
textarea{min-height:90px;resize:vertical;font-family:ui-monospace,Consolas,monospace;font-size:12px}
label{display:block;font-size:12px;color:var(--dim);margin:9px 0 4px}
.ctl{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.msg{font-size:12px;margin-top:10px;min-height:18px;color:var(--dim)}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{text-align:left;padding:5px 6px;border-bottom:1px solid #1e2a36}
th{color:var(--dim);font-weight:600}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.log{font-family:ui-monospace,Consolas,monospace;font-size:11px;color:#9fb2c5;max-height:190px;overflow:auto;white-space:pre-wrap;word-break:break-all;line-height:1.45}
#login{position:fixed;inset:0;background:rgba(5,9,13,.93);display:flex;align-items:center;justify-content:center;z-index:50}
#login .box{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:28px;width:340px;box-shadow:0 20px 60px #000}
#login h2{margin:0 0 6px;font-size:18px}
#login p{color:var(--dim);font-size:12px;margin:0 0 16px}
.hide{display:none!important}
.foot{text-align:center;color:#55677a;font-size:11px;padding:22px 0 8px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:700px){.two{grid-template-columns:1fr}}
</style>
</head>
<body>
<div id="login">
  <div class="box">
    <h2>OEA NAS 监控面板</h2>
    <p>请输入访问口令（首次登录后 7 天内免输）</p>
    <label>用户名</label><input id="lu" value="admin" autocomplete="username">
    <label>口令</label><input id="lp" type="password" autocomplete="current-password" placeholder="访问口令">
    <div class="ctl"><button class="btn" id="lbtn">登 录</button></div>
    <div class="msg" id="lmsg"></div>
  </div>
</div>

<div class="wrap">
  <header>
    <h1>🖥️ <span class="host" id="h-host">OEA NAS</span> · 监控面板</h1>
    <span class="badge live" id="h-live">● 连接中</span>
    <span class="badge" id="h-time">--</span>
    <span class="badge" id="h-uptime">--</span>
    <span class="spacer"></span>
    <button class="btn" id="btn-refresh">立即刷新</button>
    <button class="btn" id="btn-logout">退出登录</button>
  </header>

  <div class="hl" id="bootbar">启动信息加载中…</div>

  <div class="grid">
    <div class="card">
      <h2>CPU 占用 <span id="c-cpu-note" class="badge">--</span></h2>
      <div class="big" id="c-cpu">--<span class="unit">%</span></div>
      <canvas id="sp-cpu"></canvas>
      <div id="c-cores" style="margin-top:10px"></div>
      <div class="note" id="c-cpu-extra"></div>
    </div>

    <div class="card">
      <h2>内存 <span id="c-mem-note" class="badge">--</span></h2>
      <div class="big" id="c-mem">--<span class="unit">%</span></div>
      <div class="bar mem"><i id="b-mem" style="width:0%"></i></div>
      <canvas id="sp-mem"></canvas>
      <div id="c-mem-rows"></div>
    </div>

    <div class="card">
      <h2>温度 / 负载</h2>
      <div class="big" id="c-temp">--<span class="unit">°C</span></div>
      <canvas id="sp-temp"></canvas>
      <div id="c-temp-rows"></div>
    </div>

    <div class="card">
      <h2>网络吞吐 <span id="c-net-note" class="badge">--</span></h2>
      <div class="big" id="c-net">--<span class="unit">KB/s ↓</span></div>
      <canvas id="sp-net"></canvas>
      <div id="c-net-rows"></div>
    </div>

    <div class="card">
      <h2>存储空间</h2>
      <div id="c-disks"></div>
    </div>

    <div class="card">
      <h2>服务状态</h2>
      <div id="c-svc"></div>
      <div class="note">点击"控制操作"可重启 Samba / 发现服务。</div>
    </div>

    <div class="card">
      <h2>SMB 在线客户端 <span id="c-smb-n" class="badge">0</span></h2>
      <div id="c-smb"></div>
      <div class="note">这里能直接看出是不是「谁断开了」，而不是 NAS 重启了。</div>
    </div>

    <div class="card">
      <h2>占用最高的进程</h2>
      <table><thead><tr><th>PID</th><th>进程</th><th class="num">CPU%</th><th class="num">MEM%</th></tr></thead>
      <tbody id="c-top"></tbody></table>
    </div>

    <div class="card">
      <h2>📁 文件管理</h2>
      <div class="note" style="margin:0 0 8px">
        根目录 <b>/srv/pool</b>，即 <code>\\oeanas.local\nas</code>。
        支持浏览子目录、上传、下载、新建文件夹、重命名、删除、编辑文本。
      </div>
      <div id="fm-crumb" style="margin:0 0 8px;font-size:13px;word-break:break-all"></div>
      <div class="ctl" style="margin:0 0 8px">
        <button class="btn" id="fm-up">⬆ 上一级</button>
        <button class="btn" id="fm-refresh">⟳ 刷新</button>
        <button class="btn" id="fm-mkdir">＋ 新建文件夹</button>
        <label class="btn" for="fm-file" style="cursor:pointer;display:inline-block">⬆ 上传文件</label>
        <input type="file" id="fm-file" multiple style="display:none">
      </div>
      <div id="fm-msg" style="margin:0 0 8px;font-size:13px"></div>
      <div style="max-height:520px;overflow:auto">
        <table><thead><tr>
          <th>名称</th><th class="num">大小</th><th>修改时间</th><th>操作</th>
        </tr></thead><tbody id="fm-list"></tbody></table>
      </div>
      <div class="note" id="fm-stat" style="margin-top:8px"></div>
    </div>

    <div class="card">
      <h2>⚙️ 控制操作</h2>
      <div class="ctl">
        <button class="btn" data-act="restart" data-arg="smbd">重启 smbd</button>
        <button class="btn" data-act="restart" data-arg="nmbd">重启 nmbd</button>
        <button class="btn" data-act="restart" data-arg="wsdd2">重启 wsdd2</button>
        <button class="btn" data-act="sync">sync 落盘</button>
        <button class="btn" data-act="dropcache">释放缓存</button>
        <button class="btn danger" data-act="reboot">重启 NAS</button>
      </div>
      <div class="msg" id="a-msg"></div>
      <div class="note">重启 NAS 会二次确认；执行后约 70 秒才能恢复。</div>
    </div>

    <div class="card" style="grid-column:1/-1">
      <h2>最近系统告警日志 <span class="badge" id="c-log-n">--</span></h2>
      <div class="log" id="c-log">加载中…</div>
    </div>
  </div>

  <div class="foot">OEA NAS Monitor · Python 标准库单文件服务 · 每 3 秒自动刷新 · 数据仅来自本机 /proc 与 /sys</div>
</div>

<script>
var NOAUTH = __NOAUTH__;
var TOKEN = localStorage.getItem('nasmon_token') || '';
var H = null, HIST = [], timer = null, logTimer = null;

function fmtBytes(n){
  if(n === null || n === undefined) return '--';
  var u=['B','KB','MB','GB','TB'], i=0;
  while(n>=1024 && i<u.length-1){n/=1024;i++;}
  return (i===0? n : n.toFixed(i>=3?2:1)) + ' ' + u[i];
}
function fmtRate(n){ return fmtBytes(n) + '/s'; }
function fmtUp(s){
  s = Math.floor(s);
  var d=Math.floor(s/86400), h=Math.floor((s%86400)/3600), m=Math.floor((s%3600)/60);
  return (d? d+'天 ':'') + (h? h+'小时 ':'') + m + '分';
}
function el(id){ return document.getElementById(id); }
function esc(s){ return String(s).replace(/[&<>"]/g, function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];}); }

function api(path, opts){
  opts = opts || {};
  var h = {'X-Auth-Token': TOKEN};
  if(opts.body){ h['Content-Type']='application/json'; }
  return fetch(path, {method: opts.method||'GET', headers:h,
                      body: opts.body? JSON.stringify(opts.body):undefined})
    .then(function(r){
      if(r.status===401){ showLogin(true); throw new Error('unauthorized'); }
      return r.json();
    });
}

function showLogin(on){
  if(NOAUTH) on=false;
  el('login').classList.toggle('hide', !on);
  if(on){ el('lmsg').textContent = TOKEN? '登录已过期，请重新输入口令' : ''; }
}
function doLogin(){
  var u=el('lu').value, p=el('lp').value;
  el('lmsg').textContent='验证中…';
  fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({user:u,pass:p})})
   .then(function(r){return r.json();})
   .then(function(j){
      if(j.ok){ TOKEN=j.token; localStorage.setItem('nasmon_token',TOKEN);
        showLogin(false); el('lp').value=''; el('lmsg').textContent=''; boot(); }
      else { el('lmsg').textContent = j.msg || '口令错误'; }
   }).catch(function(e){ el('lmsg').textContent='请求失败: '+e.message; });
}

function spark(id, data, color, max){
  var c=el(id); if(!c) return;
  var w=c.clientWidth||300, h=46, dpr=window.devicePixelRatio||1;
  c.width=w*dpr; c.height=h*dpr;
  var g=c.getContext('2d'); g.scale(dpr,dpr);
  g.clearRect(0,0,w,h);
  g.strokeStyle='#1e2a36'; g.lineWidth=1;
  for(var i=1;i<4;i++){ var y=h*i/4; g.beginPath();g.moveTo(0,y);g.lineTo(w,y);g.stroke(); }
  if(!data || data.length<2) return;
  var mx = max || Math.max.apply(null,data);
  if(!isFinite(mx)||mx<=0) mx=1;
  mx = mx*1.15;
  var pts=data.map(function(v,i){ return [i*(w/(data.length-1)), h-2-(v/mx)*(h-6)]; });
  var grad=g.createLinearGradient(0,0,0,h);
  grad.addColorStop(0,color+'55'); grad.addColorStop(1,color+'00');
  g.beginPath(); g.moveTo(pts[0][0],h);
  pts.forEach(function(p){g.lineTo(p[0],p[1]);});
  g.lineTo(w,h); g.closePath(); g.fillStyle=grad; g.fill();
  g.beginPath(); pts.forEach(function(p,i){ i?g.lineTo(p[0],p[1]):g.moveTo(p[0],p[1]); });
  g.strokeStyle=color; g.lineWidth=2; g.lineJoin='round'; g.stroke();
}

function render(){
  if(!H) return;
  el('h-host').textContent = H.hostname || 'OEA NAS';
  el('h-time').textContent = H.time;
  el('h-uptime').textContent = '已运行 ' + fmtUp(H.uptime);
  el('h-live').textContent = '● 在线';

  el('bootbar').innerHTML =
    '<b>本次内核启动：</b>' + esc(H.boot_time) +
    ' &nbsp;|&nbsp; <b>boot_id：</b><code>' + esc(H.boot_id.slice(0,18)) + '…</code>' +
    ' &nbsp;|&nbsp; <b>已连续运行：</b><span class="ok">' + fmtUp(H.uptime) + '</span>' +
    '<br>只要「本次内核启动」时间和 boot_id 不变，系统就<b>没有重启</b>（SMB 断连 ≠ 重启）。内核 ' + esc(H.kernel);

  // CPU
  var cpu=H.cpu||{total:0,cores:[]}, m=H.mem||{};
  el('c-cpu').innerHTML = cpu.total.toFixed(1) + '<span class="unit">%</span>';
  el('c-cpu-note').textContent = (H.nproc||'?') + ' 核 · ' + (H.cpu_mhz? H.cpu_mhz+' MHz':'主频未知');
  el('c-cores').innerHTML = (cpu.cores||[]).map(function(c){
    return '<div class="core"><span>'+esc(c[0])+'</span><div class="bar"><i style="width:'+c[1]+'%"></i></div><b>'+c[1].toFixed(0)+'%</b></div>';
  }).join('');
  var l=H.load||[0,0,0];
  el('c-cpu-extra').innerHTML = '负载 1/5/15 分：<b>'+l[0].toFixed(2)+' / '+l[1].toFixed(2)+' / '+l[2].toFixed(2)+
    '</b> &nbsp;·&nbsp; 运行中进程 '+H.procs_running+' &nbsp;·&nbsp; '+esc(H.os||'');

  // MEM
  el('c-mem').innerHTML = (m.percent||0).toFixed(1) + '<span class="unit">%</span>';
  el('b-mem').style.width = (m.percent||0)+'%';
  el('c-mem-note').textContent = fmtBytes(m.used)+' / '+fmtBytes(m.total);
  el('c-mem-rows').innerHTML =
    '<div class="row"><span class="k">可用</span><b class="ok">'+fmtBytes(m.available)+'</b></div>'+
    '<div class="row"><span class="k">已用</span><b>'+fmtBytes(m.used)+'</b></div>'+
    '<div class="row"><span class="k">缓存 / 缓冲</span><b>'+fmtBytes(m.cached)+' / '+fmtBytes(m.buffers)+'</b></div>'+
    '<div class="row"><span class="k">Swap</span><b>'+fmtBytes(m.swap_used)+' / '+fmtBytes(m.swap_total)+'</b></div>';

  // TEMP
  var tp=(H.temps&&H.temps[0])?H.temps[0].c:0;
  el('c-temp').innerHTML = tp.toFixed(1) + '<span class="unit">°C</span>';
  el('c-temp-rows').innerHTML = (H.temps||[]).map(function(t){
    return '<div class="row"><span class="k">'+esc(t.name)+'</span><b>'+t.c.toFixed(1)+' °C</b></div>';
  }).join('') + '<div class="row"><span class="k">负载 1 分钟</span><b>'+l[0].toFixed(2)+'</b></div>';

  // NET
  var nets=H.net||[], rx=0, tx=0;
  nets.forEach(function(n){rx+=n.rx_rate;tx+=n.tx_rate;});
  el('c-net').innerHTML = fmtBytes(rx) + '<span class="unit">/s ↓</span>';
  el('c-net-note').textContent = '↑ '+fmtRate(tx);
  el('c-net-rows').innerHTML = nets.map(function(n){
    return '<div class="row"><span class="k">'+esc(n.name)+' '+
      '<span class="tag '+(n.state==='up'?'on':'other')+'">'+esc(n.state)+'</span> '+
      esc((n.addrs||[]).join(', ')||'无 IP')+'</span>'+
      '<b>↓'+fmtRate(n.rx_rate)+' ↑'+fmtRate(n.tx_rate)+'</b></div>'+
      '<div class="row"><span class="k">累计</span><b>↓'+fmtBytes(n.rx)+' ↑'+fmtBytes(n.tx)+'</b></div>';
  }).join('') || '<div class="note">无网卡</div>';

  // DISKS
  el('c-disks').innerHTML = (H.disks||[]).map(function(d){
    var cls = d.percent>90?'bad':(d.percent>75?'warn':'ok');
    return '<div style="margin-bottom:11px"><div class="row" style="border:0;padding:2px 0">'+
      '<span class="k">'+esc(d.label)+' <code>'+esc(d.path)+'</code></span>'+
      '<b class="'+cls+'">'+d.percent.toFixed(1)+'% · 剩 '+fmtBytes(d.free)+'</b></div>'+
      '<div class="bar disk"><i style="width:'+d.percent+'%"></i></div>'+
      '<div class="note" style="margin-top:3px">'+fmtBytes(d.used)+' / '+fmtBytes(d.total)+'</div></div>';
  }).join('');

  // SERVICES
  el('c-svc').innerHTML = Object.keys(H.services||{}).map(function(k){
    var v=H.services[k], cls=v==='active'?'on':(v==='inactive'?'other':'off');
    return '<div class="row"><span class="k">'+esc(k)+'</span><span class="tag '+cls+'">'+esc(v)+'</span></div>';
  }).join('');

  // SMB
  var sm=H.smb||[];
  el('c-smb-n').textContent = sm.length + ' 个';
  el('c-smb').innerHTML = sm.length? '<table><thead><tr><th>用户</th><th>来源主机</th><th>PID</th><th>协议</th></tr></thead><tbody>'+
    sm.map(function(c){return '<tr><td>'+esc(c.user)+'</td><td>'+esc(c.machine)+'</td><td>'+esc(c.pid)+'</td><td>'+esc(c.proto)+'</td></tr>';}).join('')+
    '</tbody></table>' : '<div class="note">当前没有 SMB 客户端连接。</div>';

  // TOP
  el('c-top').innerHTML = (H.top||[]).map(function(p){
    return '<tr><td>'+esc(p.pid)+'</td><td>'+esc(p.name)+'</td><td class="num">'+p.cpu.toFixed(1)+'</td><td class="num">'+p.mem.toFixed(1)+'</td></tr>';
  }).join('');

  // sparklines
  var arr=function(k){return HIST.map(function(x){return x[k];});};
  spark('sp-cpu', arr('cpu'), '#58a6ff', 100);
  spark('sp-mem', arr('mem'), '#a371f7', 100);
  spark('sp-temp', arr('temp'), '#d29922', 0);
  spark('sp-net', HIST.map(function(x){return x.rx+x.tx;}), '#3fb950', 0);
}

function refresh(){
  api('/api/metrics').then(function(j){ H=j.data; })
    .catch(function(){})
    .then(function(){ return api('/api/history').catch(function(){return null;}); })
    .then(function(j){ if(j){HIST=j.data||[];} render(); })
    .catch(function(){});
}
function refreshLogs(){
  api('/api/logs').then(function(j){
    el('c-log').textContent = (j.data||[]).join('\n') || '(无告警)';
    el('c-log-n').textContent = (j.data||[]).length + ' 条';
  }).catch(function(){});
}
var FM_PATH = '';
function fmtBytes(n){
  if(n===null||n===undefined) return '-';
  var u=['B','KB','MB','GB','TB'], i=0, x=n;
  while(x>=1024 && i<u.length-1){ x/=1024; i++; }
  return (i===0? x : x.toFixed(x<10?2:1)) + ' ' + u[i];
}
function fmCrumb(p){
  var parts = p? p.split('/') : [];
  var h = '<a href="#" data-p="">根目录</a>';
  var acc = '';
  for(var i=0;i<parts.length;i++){
    acc = acc? acc+'/'+parts[i] : parts[i];
    h += ' / <a href="#" data-p="'+esc(acc)+'">'+esc(parts[i])+'</a>';
  }
  el('fm-crumb').innerHTML = h;
  var as = el('fm-crumb').querySelectorAll('a');
  for(var k=0;k<as.length;k++){
    as[k].onclick = function(ev){ ev.preventDefault();
      fmLoad(this.getAttribute('data-p')||''); };
  }
}
function fmLoad(p){
  api('/api/ls?path='+encodeURIComponent(p||'')).then(function(j){
    if(!j.ok){ el('fm-msg').innerHTML='<span class="bad">✗ '+esc(j.msg||'读取失败')+'</span>'; return; }
    FM_PATH = j.path || '';
    fmCrumb(FM_PATH);
    var rows = (j.entries||[]).map(function(e){
      var isDir = e.type==='dir';
      var icon = isDir? '📁' : (e.type==='link'? '🔗' : '📄');
      var nm = '<a href="#" data-dir="'+(isDir?1:0)+'" data-p="'+esc(e.path)+'">'+icon+' '+esc(e.name)+'</a>';
      var ops = [];
      if(isDir) ops.push('<a href="#" data-op="open" data-p="'+esc(e.path)+'">打开</a>');
      else ops.push('<a href="/api/download?path='+encodeURIComponent(e.path)+'">下载</a>');
      if(!isDir && e.size < 512*1024) ops.push('<a href="#" data-op="edit" data-p="'+esc(e.path)+'">编辑</a>');
      ops.push('<a href="#" data-op="ren" data-p="'+esc(e.path)+'">重命名</a>');
      ops.push('<a href="#" data-op="del" data-p="'+esc(e.path)+'" data-dir="'+(isDir?1:0)+'">删除</a>');
      return '<tr><td>'+nm+'</td><td class="num">'+(isDir? '—' : fmtBytes(e.size))+
             '</td><td>'+esc(e.mtime)+'</td><td style="font-size:12px">'+ops.join(' · ')+'</td></tr>';
    }).join('');
    el('fm-list').innerHTML = rows || '<tr><td colspan="4" class="note">空目录</td></tr>';
    var fs = j.fs||{};
    el('fm-stat').textContent = '共 '+(j.entries||[]).length+' 项'+
      (fs.total? ' · 池容量 '+fmtBytes(fs.used)+' / '+fmtBytes(fs.total)+'（可用 '+fmtBytes(fs.free)+'）' : '');
    // 绑定
    var links = el('fm-list').querySelectorAll('a[data-p]');
    for(var k=0;k<links.length;k++){
      links[k].onclick = function(ev){
        var op = this.getAttribute('data-op');
        var p  = this.getAttribute('data-p');
        var isDir = this.getAttribute('data-dir')==='1';
        if(!op){ ev.preventDefault(); if(isDir) fmLoad(p); return; }
        ev.preventDefault();
        if(op==='open'){ fmLoad(p); }
        else if(op==='del'){ fmDelete(p, isDir); }
        else if(op==='ren'){ fmRename(p); }
        else if(op==='edit'){ fmEdit(p); }
      };
    }
  }).catch(function(e){ el('fm-msg').innerHTML='<span class="bad">✗ '+esc(e.message)+'</span>'; });
}
function fmMkdir(){
  var n = prompt('新建文件夹名称：'); if(!n) return;
  api('/api/mkdir',{method:'POST',body:{path:FM_PATH,name:n}}).then(function(j){
    el('fm-msg').innerHTML = j.ok? '<span class="ok">✓ '+esc(j.msg)+'</span>' : '<span class="bad">✗ '+esc(j.msg)+'</span>';
    if(j.ok) fmLoad(FM_PATH);
  });
}
function fmDelete(p, isDir){
  var msg = isDir? ('确定删除目录 "'+p+'" 及其全部内容？此操作不可恢复！') : ('确定删除文件 "'+p+'"？');
  if(!confirm(msg)) return;
  api('/api/delete',{method:'POST',body:{path:p,recursive:isDir}}).then(function(j){
    if(!j.ok && j.need_confirm){
      if(confirm('目录非空，确认递归删除全部内容？')) {
        api('/api/delete',{method:'POST',body:{path:p,recursive:true}}).then(function(k){
          el('fm-msg').innerHTML = k.ok? '<span class="ok">✓ '+esc(k.msg)+'</span>' : '<span class="bad">✗ '+esc(k.msg)+'</span>';
          fmLoad(FM_PATH);
        });
      }
      return;
    }
    el('fm-msg').innerHTML = j.ok? '<span class="ok">✓ '+esc(j.msg)+'</span>' : '<span class="bad">✗ '+esc(j.msg)+'</span>';
    if(j.ok) fmLoad(FM_PATH);
  });
}
function fmRename(p){
  var cur = p.split('/').pop();
  var n = prompt('新名称：', cur); if(!n || n===cur) return;
  api('/api/rename',{method:'POST',body:{path:p,name:n}}).then(function(j){
    el('fm-msg').innerHTML = j.ok? '<span class="ok">✓ '+esc(j.msg)+'</span>' : '<span class="bad">✗ '+esc(j.msg)+'</span>';
    if(j.ok) fmLoad(FM_PATH);
  });
}
function fmEdit(p){
  api('/api/read',{method:'POST',body:{path:p}}).then(function(j){
    if(!j.ok){ el('fm-msg').innerHTML='<span class="bad">✗ '+esc(j.msg)+'</span>'; return; }
    var box = document.createElement('div');
    box.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:999;display:flex;align-items:center;justify-content:center';
    box.innerHTML='<div style="background:#1b1e24;border:1px solid #333a45;border-radius:10px;padding:16px;width:min(900px,92vw)">'+
      '<div style="margin-bottom:8px;font-size:13px">编辑 <b>'+esc(p)+'</b></div>'+
      '<textarea id="fm-ed" style="width:100%;height:min(60vh,520px);background:#0f1115;color:#e6e6e6;border:1px solid #333a45;border-radius:6px;padding:8px;font-family:ui-monospace,Consolas,monospace;font-size:12px"></textarea>'+
      '<div style="margin-top:10px;text-align:right"><button class="btn" id="fm-cancel">取消</button> <button class="btn" id="fm-save">保存</button></div></div>';
    document.body.appendChild(box);
    var ta = box.querySelector('#fm-ed');
    ta.value = j.content;
    box.querySelector('#fm-cancel').onclick = function(){ box.remove(); };
    box.querySelector('#fm-save').onclick = function(){
      api('/api/save',{method:'POST',body:{path:p,content:ta.value}}).then(function(k){
        box.remove();
        el('fm-msg').innerHTML = k.ok? '<span class="ok">✓ '+esc(k.msg)+'</span>' : '<span class="bad">✗ '+esc(k.msg)+'</span>';
        fmLoad(FM_PATH);
      });
    };
  });
}
function fmUpload(files){
  if(!files || !files.length) return;
  var i = 0, total = files.length;
  function next(){
    if(i >= total){ el('fm-msg').innerHTML='<span class="ok">✓ 全部上传完成 ('+total+' 个文件)</span>'; fmLoad(FM_PATH); return; }
    var f = files[i++];
    el('fm-msg').textContent = '上传中 ('+i+'/'+total+')：'+f.name+' ('+fmtBytes(f.size)+')';
    var url = '/api/put?path='+encodeURIComponent(FM_PATH)+'&name='+encodeURIComponent(f.name);
    var xhr = new XMLHttpRequest();
    xhr.open('POST', url, true);
    xhr.setRequestHeader('Content-Type','application/octet-stream');
    if(TOKEN) xhr.setRequestHeader('X-Auth-Token', TOKEN);
    xhr.upload.onprogress = function(ev){
      if(ev.lengthComputable){
        var pct = Math.round(ev.loaded*100/ev.total);
        el('fm-msg').textContent = '上传中 ('+i+'/'+total+')：'+f.name+' '+pct+'% ('+fmtBytes(ev.loaded)+'/'+fmtBytes(ev.total)+')';
      }
    };
    xhr.onload = function(){
      var j = {};
      try { j = JSON.parse(xhr.responseText||'{}'); } catch(e){}
      if(!j.ok){ el('fm-msg').innerHTML='<span class="bad">✗ '+esc(f.name)+': '+esc(j.msg||('HTTP '+xhr.status))+'</span>'; }
      next();
    };
    xhr.onerror = function(){ el('fm-msg').innerHTML='<span class="bad">✗ '+esc(f.name)+': 网络错误</span>'; next(); };
    xhr.send(f);
  }
  next();
}
function action(btn){
  var act=btn.getAttribute('data-act'), arg=btn.getAttribute('data-arg')||'';
  var body={action:act,arg:arg};
  if(act==='reboot'){
    if(!confirm('确定要重启 NAS 吗？\n\n约 70 秒后恢复，期间 SMB/SSH 都会断开。\n（如果 boot_id 变了，才是真正重启）')) return;
    body.confirm=true;
  }
  btn.disabled=true; var old=btn.textContent; btn.textContent='执行中…';
  api('/api/action',{method:'POST',body:body}).then(function(j){
    el('a-msg').innerHTML = j.ok? '<span class="ok">✓ '+esc(j.msg)+'</span>' : '<span class="bad">✗ '+esc(j.msg)+'</span>';
  }).catch(function(e){ el('a-msg').textContent='失败: '+e.message; })
  .then(function(){ btn.disabled=false; btn.textContent=old; });
}

el('fm-up').onclick=function(){ if(!FM_PATH){ return; } var up = FM_PATH.split('/').slice(0,-1).join('/'); fmLoad(up); };
el('fm-refresh').onclick=function(){ fmLoad(FM_PATH); };
el('fm-mkdir').onclick=fmMkdir;
el('fm-file').onchange=function(){ fmUpload(this.files); this.value=''; };

function boot(){
  refresh(); refreshLogs(); fmLoad(FM_PATH);
  if(timer) clearInterval(timer);
  if(logTimer) clearInterval(logTimer);
  timer=setInterval(refresh,3000);
  logTimer=setInterval(refreshLogs,30000);
}

el('lbtn').onclick=doLogin;
el('lp').addEventListener('keydown',function(e){if(e.key==='Enter')doLogin();});
el('btn-refresh').onclick=function(){refreshLogs();fmLoad(FM_PATH);refresh();};
el('btn-logout').onclick=function(){localStorage.removeItem('nasmon_token');TOKEN='';showLogin(true);if(timer)clearInterval(timer);if(logTimer)clearInterval(logTimer);};
el('w-btn').onclick=writeFile;
el('w-list').onclick=refreshFiles;
Array.prototype.forEach.call(document.querySelectorAll('[data-act]'),function(b){b.onclick=function(){action(b);};});

// 无密码模式（no_auth=1）：跳过登录页、隐藏退出按钮
if(NOAUTH){ showLogin(false); el('btn-logout').style.display='none'; }
// 先探测一次，401 就弹登录
api('/api/metrics').then(function(j){ H=j.data; boot(); })
  .catch(function(){ showLogin(true); });
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "nasmon/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # 不写访问日志，避免污染 journal

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if n <= 0 or n > 256 * 1024 * 1024:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def _put_stream(self):
        """流式接收原始字节并落盘：POST /api/put?path=<dir>&name=<file>
        Content-Type: application/octet-stream, body = 原始文件内容。
        """
        q = parse_qs(urlparse(self.path).query)
        rel = q.get("path", [""])[0]
        name = q.get("name", [""])[0]
        if not filemgr:
            self._send(500, {"ok": False, "msg": "filemgr 未加载"})
            return
        try:
            parent = filemgr.resolve(rel)
            name = filemgr._check_name(name)
        except Exception as e:
            self._send(400, {"ok": False, "msg": str(e)})
            return
        if not os.path.isdir(parent):
            self._send(400, {"ok": False, "msg": "父目录不存在"})
            return
        try:
            total = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            total = 0
        if total <= 0:
            self._send(400, {"ok": False, "msg": "缺少 Content-Length"})
            return
        if total > filemgr.MAX_UPLOAD:
            self._send(413, {"ok": False,
                             "msg": "超过 %d MB 上限" % (filemgr.MAX_UPLOAD // 1048576)})
            return
        target = os.path.join(parent, name)
        got = 0
        try:
            with open(target, "wb") as f:
                remain = total
                while remain > 0:
                    chunk = self.rfile.read(min(262144, remain))
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    remain -= len(chunk)
            os.chmod(target, 0o666)
        except Exception as e:
            self._send(500, {"ok": False, "msg": "写入失败: %s" % e})
            return
        if got != total:
            try:
                os.remove(target)
            except OSError:
                pass
            self._send(500, {"ok": False,
                             "msg": "传输不完整 %d/%d" % (got, total)})
            return
        self._send(200, {"ok": True, "path": filemgr._relpath(target),
                         "size": got, "msg": "上传成功"})

    def _auth(self):
        if NO_AUTH:
            return True
        return verify_token(self.headers.get("X-Auth-Token", ""))

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/" or p == "/index.html":
            html = HTML.replace("__NOAUTH__", "true" if NO_AUTH else "false")
            self._send(200, html, "text/html; charset=utf-8")
            return
        if not self._auth():
            self._send(401, {"ok": False, "msg": "unauthorized"})
            return
        if p == "/api/metrics":
            with LOCK:
                snap = LATEST or collect()
            self._send(200, {"ok": True, "data": snap})
        elif p == "/api/history":
            with LOCK:
                h = list(HISTORY)
            self._send(200, {"ok": True, "data": h})
        elif p == "/api/logs":
            self._send(200, {"ok": True, "data": recent_logs(30)})
        elif p == "/api/files":
            self._send(200, {"ok": True, "data": list_files()})
        elif p == "/api/ls":
            q = parse_qs(urlparse(self.path).query)
            rel = (q.get("path", [""])[0] or "")
            show_hidden = q.get("hidden", ["0"])[0] == "1"
            self._send(200, filemgr.list_dir(rel, show_hidden) if filemgr
                       else {"ok": False, "msg": "filemgr 未加载"})
        elif p == "/api/download":
            q = parse_qs(urlparse(self.path).query)
            rel = (q.get("path", [""])[0] or "")
            try:
                fp, size = filemgr.download(rel)
            except Exception as e:
                self._send(404, {"ok": False, "msg": str(e)})
                return
            fn = os.path.basename(fp)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition",
                             "attachment; filename*=UTF-8''%s" % quote(fn))
            self.end_headers()
            try:
                with open(fp, "rb") as f:
                    while True:
                        chunk = f.read(262144)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
            except Exception:
                pass
        else:
            self._send(404, {"ok": False, "msg": "not found"})

    def do_POST(self):
        p = urlparse(self.path).path
        # /api/put 走原始字节流，绝不能先调 _body()（它会把整个 body 读进内存）
        if p == "/api/put":
            if not self._auth():
                self._send(401, {"ok": False, "msg": "unauthorized"})
                return
            self._put_stream()
            return
        body = self._body()
        if p == "/api/login":
            if NO_AUTH:
                self._send(200, {"ok": True, "token": "", "msg": "no_auth"})
                return
            u = str(body.get("user", ""))
            pw = str(body.get("pass", ""))
            h = hashlib.sha256(pw.encode("utf-8")).hexdigest()
            if u == CONF_DATA.get("user", "admin") and \
               hmac.compare_digest(h, CONF_DATA.get("pass_sha256", "")):
                self._send(200, {"ok": True, "token": make_token(u)})
            else:
                self._send(200, {"ok": False, "msg": "用户名或口令错误"})
            return
        if not self._auth():
            self._send(401, {"ok": False, "msg": "unauthorized"})
            return
        if p == "/api/action":
            self._send(200, do_action(body))
        elif p == "/api/write":
            self._send(200, do_write(body))
        elif p == "/api/mkdir":
            self._send(200, filemgr.mkdir(body.get("path", ""),
                                          body.get("name", "")))
        elif p == "/api/delete":
            self._send(200, filemgr.delete(body.get("path", ""),
                                           bool(body.get("recursive"))))
        elif p == "/api/rename":
            self._send(200, filemgr.rename(body.get("path", ""),
                                           body.get("name", "")))
        elif p == "/api/read":
            self._send(200, filemgr.read_text(body.get("path", "")))
        elif p == "/api/save":
            self._send(200, filemgr.save_text(body.get("path", ""),
                                              body.get("content", "")))
        elif p == "/api/upload":
            self._send(200, filemgr.upload(body.get("path", ""),
                                           body.get("name", ""),
                                           body.get("data", "")))

        else:
            self._send(404, {"ok": False, "msg": "not found"})


def main():
    global LATEST
    LATEST = collect()
    t = threading.Thread(target=sampler, daemon=True)
    t.start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    print("nasmon listening on 0.0.0.0:%d" % PORT, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
