# 13 · 开机默认 performance（恒定满频，但不是超频）

做完 [`12-overclocking.md`](12-overclocking.md) 的调查后，用户决定不超频、保持出厂默认，
但补了一句：「改成默认 performance 一直满功耗运行吧」。

这条是**零风险**改动，和超频完全是两回事：

| | `performance` governor | 真超频 |
|---|---|---|
| 频率 | 恒定在**内核已注册 OPP 的最高档** `1104000 kHz` | 超过 1104 MHz（1200/1296） |
| 电压 | 框架按 OPP 自动给 `1275 mV`（实测 `1274000 uV`） | 要手动抬到 1350 mV 以上 |
| 改 DTB / PLL / regulator | **都不动** | 必须动 |
| 重启后 | 由 systemd 重新钉一遍；脚本失败则回到内核默认 | 改坏引导链可能变砖 |
| 温度（加散热片） | 实测 39–42 °C | — |

`scaling_available_governors` 里本来就有 `performance`：
`conservative ondemand userspace powersave interactive performance`。
原厂默认是 `interactive`（空闲降到 816 MHz / 1049 mV），改成 `performance`
只是不再降频，让 4 核一直跑在 1104 MHz。

> 顺带清理了一个坑：`/etc/default/cpufrequtils` 里写着
> `ENABLE="true"` / `GOVERNOR="schedutil"`，但 **cpufrequtils 包根本没装、
> 也没有任何 systemd unit 引用它**（`systemctl list-unit-files | grep -i cpufreq` 无输出）。
> 它是原厂遗留的死配置，不会抢 governor。我们仍把它改成
> `GOVERNOR="performance"`，免得后来人看配置时被误导。

---

## 13.1 交付的文件

都在 [`scripts/nasmon/`](../scripts/nasmon/)：

| 文件 | 装到板子 | 作用 |
|---|---|---|
| `cpu-performance.sh` | `/usr/local/sbin/cpu-performance.sh` (0755) | 遍历所有 `cpu*/cpufreq/scaling_governor` 写入 `performance` |
| `cpu-performance.service` | `/etc/systemd/system/cpu-performance.service` (0644) | `Type=oneshot` + `RemainAfterExit=yes`，开机自启 |

脚本设计要点（[`cpu-performance.sh`](../scripts/nasmon/cpu-performance.sh)）：

```sh
GOV=performance
ROOT=/sys/devices/system/cpu

# 1) 有界等待 cpufreq-dt 的 sysfs 出现（这个 unit 跑得早，驱动可能还在 probe）
i=0
while [ ! -f "$ROOT/cpu0/cpufreq/scaling_governor" ]; do
    i=$((i+1)); [ "$i" -ge 30 ] && { log "sysfs never appeared"; exit 1; }
    sleep 1
done

# 2) 写两遍：晚到的 CPU hotplug / 驱动 re-init 可能重置某一个 policy
apply; sleep 2; apply

# 3) 逐个回读校验，任何一个不是 performance 就 exit 1
```

`log()` 写 `/dev/kmsg`（journal 里能看到 `cpu-performance:` 前缀）。
**脚本失败也只是不生效，不会把系统搞坏**——因为 governor 本来就是内核默认值之一。

`cpu-performance.service` 全文：

```ini
[Unit]
Description=Lock CPU governor to performance (max registered OPP, no overclock)
Documentation=https://github.com/qiushanlin4/oea-nas/blob/main/docs/13-performance-governor.md

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/cpu-performance.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

---

## 13.2 部署

```bash
SRC=/srv/pool/dsh-workspace/oea-nas-docs/scripts/nasmon
install -m 0755 "$SRC/cpu-performance.sh"     /usr/local/sbin/cpu-performance.sh
install -m 0644 "$SRC/cpu-performance.service" /etc/systemd/system/cpu-performance.service
sed -i 's/^GOVERNOR=.*/GOVERNOR="performance"/' /etc/default/cpufrequtils   # 可选，清理死配置
systemctl daemon-reload
systemctl enable --now cpu-performance.service
```

`RemainAfterExit=yes` 让 `systemctl is-active` 在服务跑完后仍显示 `active`，
方便巡检；`systemctl restart cpu-performance.service` 可随时重新钉一遍。

---

## 13.3 验证（含重启持久化）

即时生效：

```
SERVICE_IS_ACTIVE=active
cpu0..cpu3/cpufreq/scaling_governor = performance   ×4
cpuinfo_cur_freq = 1104000                          ×4
vcc_arm = 1274000 uV    temp = 39090 (39.1 °C)
journal: Finished cpu-performance.service - Lock CPU governor to performance
```

**重启后**（`systemctl reboot`，约 30 秒后回来，`boot_id` 已变 → 确认真的重启过）：

| 检查 | 结果 |
|---|---|
| `cpu-performance.service` | `active` + `enabled` |
| 4 核 governor | 全部 `performance` |
| 4 核 `cpuinfo_cur_freq` | 全部 `1104000` |
| `vcc_arm` | `1274000 uV` |
| 温度 | `41818`（41.8 °C） |
| smbd / nmbd / ssh / nasmon / wsdd2 / avahi-daemon / netup | 全 `active` |
| `systemctl --failed` | 0 units |
| 面板 HTTP | 200 |

> 顺带说明：这次「重启」是**主动** `systemctl reboot`，和
> [`11-nasmon-panel.md`](11-nasmon-panel.md) 里那次「假死机」不同——
> 那次 `boot_id` 没变，这次变了。面板首页的 boot_id / 启动时间提示条就是用来一眼区分两者的。

---

## 13.4 想恢复省电

```bash
systemctl disable --now cpu-performance.service
for g in /sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_governor; do
    echo interactive > "$g"
done
```

不改任何磁盘内容、不需要重刷，重启即彻底回到原厂行为。
