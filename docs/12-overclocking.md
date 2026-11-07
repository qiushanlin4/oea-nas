# 12 · 超频调查：能不能把 CPU / 内存也超上去？

> 结论先说：**这块板子在「不改引导链」的前提下无法稳定超频，DDR 更是完全不行。**
> 厂商/U-Boot 有意把它识别成降档的 RK3318 并砍掉了高频档。真要超频只能改
> 真正生效的那份 DTB 并重刷引导分区，代价是**可能永久失去唯一的 FIQ 串口救援通道、变砖**。
> 我做完完整实测后，用户决定**保持稳定、不超频**，只把 governor 钉在
> `performance`（见 [`13-performance-governor.md`](13-performance-governor.md)），
> 那已经在已注册 OPP 的最高档 1104 MHz，属于出厂允许范围，不是超频。

这一章把全部实测数据留下来，省得后来人再走一遍弯路。

---

## 12.1 表面信息 vs 真实信息

| 来源 | CPU 频率档 | DDR 频率档 |
|---|---|---|
| 原厂 `boot`(p4) / `kernel`(p5) 分区里解出的 DTB | 6 档：408 / 600 / 816 / 1008 / **1200 / 1296** MHz | 7 档：400 / 600 / 786 / **800 / 850 / 933 / 1066** MHz |
| 运行内核的 live DT（`/sys/firmware/fdt`） | 5 档：408 / 600 / 816 / 1008 / **1104** MHz | 7 档，但**只有 786 MHz 启用**，其余 6 档全带 `status = "disabled"` |
| 内核实际注册（`opp_summary` / cpufreq / devfreq） | 最高 **1104000 kHz @ 1275 mV** | 只有 **786000000 Hz** |

几个关键事实：

- `/sys/firmware/fdt` = **77952 B**，而从 boot/p5 分区 `second+0x400` 解出的
  `dtb.dtb` 只有 **77286 B**，两者**不相等** → 运行内核用的根本不是 boot 分区那份 DTB。
- 内核 `CONFIG_OF_OVERLAY is not set`，`/sys/kernel/config/` 下没有 `device-tree/` /
  `overlays/` → **没有运行时改设备树的通道**。
- 全分区扫 DTB magic `d00dfeed`：`uboot` 分区前 4 MB 有 4 处（都是
  `totalsize=6206` 的 SPL/TPL 小 DTB），`trust`/`oemmisc`/`boot`/`kernel`/`env`/
  `system`/`backup`/`instaboot`/`misc`/`cache` 前 8 MB 全部 0 命中
  （boot 的真 DTB 在 ~29.8 MB 处，不在前 8 MB）。**live DTB 的真实来源仍未定位**
  （可能在 U-Boot 内置 dtb / resource 分区 / TPL-ATF）。

### live 与 boot DTB 的全量 diff（决定性证据）

`dtc -I dtb -O dts` 两份反编译后 `diff` 共 121 行，全部是 U-Boot 启动时改写 live DT 的痕迹：

- live 多两条 `/memreserve/`、加 `serial-number = "84cd9182ecbf9de7"`；
- `compatible` 追加 `\0rockchip,rk3318\0rockchip,rk3318-evb`，`model` 追加
  `\0Rockchip RK3318 EVB avb` → **被识别成降档 RK3318**；
- **`cpu0-opp-table`：新增 `opp-1104000000`（电压沿用原 1.2 GHz 档的 1275 mV），
  同时删除 `opp-1200000000` 与 `opp-1296000000`**；
- live 删了 `rkvdec-opp-table` 的 500 MHz 档（但 `opp_summary` 里仍在）；
- 两个 ethernet 节点加 `local-mac-address = [4a 93 96 2a 81 9a]`；
- `usb@ff580000` 的 `dr_mode` 从 `"host"` 变 `"otg"`；
- `chosen` 加 `linux,initrd-start/end`，bootargs 换成含 `androidboot.*` 的完整串：
  `storagemedia=emmc androidboot.storagemedia=emmc androidboot.mode=normal fuse.programmed=1 otg_device=1 androidboot.verifiedbootstate=orange androidboot.serialno=84cd9182ecbf9de7 rw rootwait earlycon=uart8250,mmio32,0xff130000 console=ttyFIQ0 root=PARTUUID=614e0000-0000`。

**DDR 的 6 档 `status="disabled"` 是原厂 DTB 里本来就带上的，U-Boot 没动。**
U-Boot C 源码（`/n/dsh-workspace/oea/*.c`）里 grep 不到 1104/1296/rk3318/OPP 降档逻辑，
`board_fdt_fixup()` 在 `uboard.c:709`、`rk_board_early_fdt_fixup()` 在
`boot_rkimg.c:48`（:567 调用），降档动作在别处。

---

## 12.2 CPU：试过的每一条路

### ① sysfs 越界写 → 静默 clamp

```bash
echo 1600000 > /sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq
# write_rc=0、err 为空，但读回仍是 1104000
```

`scaling_available_frequencies = 408000 600000 816000 1008000 1104000`，
`cpuinfo_max_freq = 1104000`，`scaling_driver = cpufreq-dt`。框架只允许已注册档位。

### ② 烧机确认 governor 正常（不是被锁在低频）

空闲时是 816 MHz；起 4 个 `while :; do :; done` 后 4 核全部升到 `1104000`，
温度 48–49 °C。装了散热片后满负载实测 40–46 °C，`performance` 长时间 39–42 °C。
→ **散热不是瓶颈**。

### ③ 电压余量其实够

`/sys/class/regulator/regulator.7`：`name=vcc_arm`、`uV=1049000`、
`max=1400000`、`min=950000`。DTB 里 1.2 GHz 要 1275 mV、1.296 GHz 要 1350 mV，
**上限 1400 mV 完全够**。`vdd-center -> vcc_arm`。

热跳闸点：`90000`(passive) / `105000`(passive) / `115000`(critical)。

`opp_summary` 里 CPU 注册的完整表：

```
cpu0  408000000  950000 / 600000000  950000 / 816000000 1050000
      1008000000 1150000 / 1104000000 1275000 (max 1350000)
platform-dmc      786000000 1075000
platform-ff300000.gpu 200/300/400000000  950000/975000/1050000
platform-ff360000.rkvdec 100/200/500000000  975000/975000/1075000
```

### ④ `clk/armclk/clk_rate` 可写，但 DVFS 会立刻收回去

`mount -t debugfs none /sys/kernel/debug` 后：

```
armclk        rate=816000000  CLK_SET_RATE_PARENT|CLK_GET_RATE_NOCACHE|CLK_SET_RATE_NO_REPARENT
apll          rate=61440000
gpll          rate=491519999
sclk_ddrc     rate=786000000  CLK_SET_RATE_NO_REPARENT
clk_ddrupctl  rate=786000000  CLK_SET_RATE_PARENT|CLK_IGNORE_UNUSED|CLK_IS_BASIC
```

文件权限显示 `-r--r--r--`，但**内核挂了写回调**：

```bash
echo 1008000000 > /sys/kernel/debug/clk/armclk/clk_rate   # rc=0
cat cpuinfo_cur_freq                                       # 1008000  ← 真的变了
echo 816000000 > ...                                       # 恢复成功
```

于是我写了个 `oc-probe.sh`（日志写 NAS，用 `setsid ... &` 脱离 SSH，
看门狗复位后仍能读到断点），假设「先用 cpufreq 正规 DVFS 把电压抬到 1275 mV，
再跳 PLL 到 1.2 GHz 就不会欠压」：

| 阶段 | armclk | vcc_arm | cur_freq | 结果 |
|---|---|---|---|---|
| STEP1 userspace@1104 | 1104000000 | 1274000 uV | 1104000 | `BENCH stock_1104 = 8807 ms` |
| STEP2 写 1200000000 | 1200000000 | 1274000 | 1200000 | `write_rc=0`、boot_id 未变、`BENCH oc_1200 = 8416 ms` |
| STEP3 20 s 耐久 | **1104000000** | 1274000 | — | 4 轮 8393/8719/8840/8462 ms、`STEP3 survived` |

→ **负载一开始，cpufreq/OPP 框架就把强行拉高的 PLL 收回到 1104 MHz。**

再用 `oc-persist.sh` 加严格采样确认（这是定案数据）：

```
BENCH BASE_1104  TIME=12876ms   ARMCLK_SAMPLES 6 次全 1104000000
OC pre-load armclk=1200000000 cur=1200000
BENCH OC_1200    TIME=12821ms   ARMCLK_SAMPLES 6 次全 1104000000
OC post-load armclk=1104000000
```

**12821 ms vs 12876 ms —— 零性能提升。** 看起来写进去了，一跑负载就被收回。

### ⑤ 想绕开 DVFS：unbind cpufreq-dt → 掉压崩溃

`oc-unbind.sh` 的终极假设是
`echo cpufreq-dt > /sys/bus/platform/drivers/cpufreq-dt/unbind` 让框架不再抢 PLL。
日志（263 B，从 NAS 读到）断点：

```
PRE          armclk=1104000000 vcc=1274000
unbind_rc=0
POST_UNBIND  armclk=408000000  vcc=950000  cpufreq_dir_exists=no
```

**unbind 后电压掉到 950 mV、频率掉到 408 MHz**，随后写 1.2 GHz 必然欠压崩溃 →
看门狗复位。这也解释了更早那次直接跳频的复位：空闲 816 MHz / 1049 mV
直接跳 1.2 GHz，CPU 瞬间欠压。

### ⑥ 想先升压：regulator 只读

```bash
echo 1275000 > /sys/class/regulator/regulator.7/microvolts
# Permission denied / cur_freq_write_rc=1
```

`regulator.*/microvolts` 全部 `-r--r--r--`，**运行时改不了电压**。

### ⑦ 两次看门狗复位证明安全网

两次故意触发后探活：

```
ALIVE  /proc/uptime=85.50 284.96  boot_id=247418b1-…（变了）
armclk=1104000000  cpuinfo_cur_freq=1104000  smbd=active
```

第二次 `boot_id=c93b3c23-…`、UP=141.39、ARMCLK=1104000000、GOV=interactive。
复位后全绿：failed_units=0、POOL=76G free、MERGERFS=1、面板 HTTP=200。

> **这是一张很有价值的安全网**：任何运行时超频尝试崩了，断电/复位后
> **必定回到出厂默认 1104 MHz / 1049 mV**，不会留下半超频状态，也不会变砖。

---

## 12.3 内存（DDR）：连「试一下」都不行

```bash
cat /sys/class/devfreq/dmc/available_frequencies   # 786000000  （只有 1 档）
echo userspace > governor                          # rc=0
echo 933000000 > /sys/class/devfreq/dmc/cur_freq
# Permission denied / cur_freq_write_rc=1
cat cur_freq ; cat max_freq                        # 仍是 786000000
```

`governor=dmc_ondemand`、`sclk_ddrc=786000000`、`clk_ddrupctl=786000000`，
boot_id 未变。→ **DDR 运行时超频完全不可能，必须改 DTB 里那 6 个
`status="disabled"` 并让内核重新注册 OPP。**

---

## 12.4 真要超频的唯一路径（以及为什么不走）

1. 找到**真正生效的那份 DTB**（live fdt 77952 B，来源未定位，不是 /dev/boot、
   不是 /dev/kernel 的 second 段，可能是 U-Boot 内置 dtb / resource 分区 / TPL-ATF）；
2. 把 `cpu0-opp-table` 的 1200/1296 MHz 档加回来（电压 1275/1350 mV），
   把 `dmc-opp-table` 里 800/850/933/1066 MHz 的 `status="disabled"` 去掉；
3. 处理 U-Boot 的 `rk3318` 降档 fixup，否则它启动时又会把高频档删掉；
4. 重刷对应引导分区并验证。

代价：

- **动到原厂引导链，可能失去唯一的 FIQ 调试器 / ttyFIQ0 串口救援通道**——
  这正是整个项目一开始拼命保留的东西（见 `README.md`「为什么保留原厂内核」）；
- 没有 maskrom、没有 SD 卡、没有 USB 启动，刷坏了就是砖；
- DDR 超频若时序参数（`ddr3_speed_bin` / `ddr3_drv` / `ddr3_odt`）不匹配，
  会直接随机崩，比 CPU 欠压更难救。

**收益却极小**：这台 NAS 的瓶颈是**百兆网口（12.5 MB/s）+ Samba I/O**，
不是 CPU。从 1104 → 1200 MHz 是 8.7%，实测在 DVFS 框架下甚至是 0%。

boot 分区已经整块备份，随时可对比/回滚：

```
/n/dsh-workspace/nasmon/boot-backup/boot.img   67108864 B
sha256 66657baca3c9cb8956b6c2e0179ca21ff7476070d1730d43973e06702108b680
```

---

## 12.5 最终决定

用户装了散热片后说「可以激进一点」，但看完上面全部数据后选择：
**保持稳定，不超频，保持出厂默认。**

只保留了一个零风险的改动：把 CPU governor 开机钉成 `performance`
（恒定 1104 MHz / 1275 mV，仍在已注册 OPP 内，重启永远回到安全态），
详见 [`13-performance-governor.md`](13-performance-governor.md)。

### 附：探测脚本

`oc-probe.sh` / `oc-persist.sh` / `oc-unbind.sh` 都写在 NAS 的
`nasmon/` 目录里（日志也落 NAS），设计要点：

- 每步 `say()` + `sync`，日志落 NAS，看门狗复位后仍能读到断点；
- 用 `setsid bash ... </dev/null >/tmp/oc-*.out 2>&1 &` 脱离 SSH；
- 脚本经 SMB 落盘后必须 `sed -i "s/\r$//"` 去 CRLF、`bash -n` 再跑；
- 探活别用 PC 的 `ping -c`（本机报 `Access denied. Option -c requires administrative privileges.`），
  用 `timeout 12 ssh ... true`。

**这些脚本只做调查用，不要留在开机自启里。**
