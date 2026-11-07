# rootfs/ —— 从 0 造一个 1 GiB 的 Armbian 根文件系统

p7（`/dev/system`）只有 **1 GiB**，而 Armbian 的 rk3318 rootfs 解压后大约
**3 GB**。这个目录里的脚本完成「提取 → 裁剪 → 建 ext4 → 补回符号链接 →
补回权限 → 打补丁」的全过程。

## 顺序

```bash
export WORK=~/oea
cd $WORK

# 0. 拿一个 Armbian rk3318 镜像
#    https://www.armbian.com/  ->  Rk3318  ->  选 current / bookworm 或 noble

# 1. 从整盘镜像里抠出 rootfs 分区
WD=$PWD $OLDPWD/rootfs/ddroot.sh          # 产物 armroot.img (3.1 GB)

# 2. 导出文件树
debugfs -R "rdump / rootfs_out" armroot.img

# 3. 裁剪到 1 GiB 以内
rootfs/mk1g.sh                            # 815 MB 左右

# 4. 建壳 + 记录符号链接
rootfs/mkfs1g.sh                          # 产物 newrootfs.img (1 GiB) + sym_cmds.txt

# 5. 把 3189 个符号链接塞回去
debugfs -w -f sym_cmds.txt newrootfs.img

# 6. 抓原始权限，再塞回去
rootfs/walkall.sh                         # -> lsout.txt
rootfs/mkmeta.sh                          # -> meta_cmds.txt (~71157 行)
debugfs -w -f meta_cmds.txt newrootfs.img

# 7. 打补丁（PAM / securetty / serial getty / netup）
IMG=newrootfs.img rootfs/apply-fixes.sh

# 8. 体检
e2fsck -fy newrootfs.img
```

## 为什么必须做第 4–6 步

`mke2fs -d` 有两个坑，**两个都踩过**：

### 坑 1：符号链接全部丢失

`mke2fs -d <dir>` 会复制文件内容，但**不重建符号链接**。一次就丢 3189 个。

其中三个是致命的：

```
/bin  -> usr/bin
/lib  -> usr/lib
/sbin -> usr/sbin
```

丢了这三个，`/lib/aarch64-linux-gnu/security/pam_unix.so` 就找不到，
PAM 直接 `Critical error - immediate abort`，**系统能启动但永远登录不了**。

### 坑 2：uid/gid/mode 被归一化

`mke2fs -d` 把所有文件都写成当前用户的属主，`sudo` 的 `04755` 位也没了。
所以要先用 `walkall.sh` 把原始元数据 dump 出来，再用 `sif` 打回去。

## debugfs 的三个硬规则

1. **`write` 的第二个参数不能用绝对路径。**
   `write foo /etc/pam.d/login` 会在**当前目录**创建一个名字里带斜杠的非法文件。
   正确做法：先 `cd /etc/pam.d`，再 `write foo login`。

2. **`rm` 也要在 `cd` 之后用相对名。**

3. **`symlink` 反而接受绝对路径**，而且用相对名时会以 debugfs 的当前目录为基准，
   容易出现「链接内容对、但位置错」的情况。

## 环境

- Cygwin 的 e2fsprogs 1.44.5 实测可用
- 必须 `export PATH="/c/cygwin64/usr/bin:/c/cygwin64/usr/sbin:$PATH"`
- **Cygwin 没有 `readelf` / `objdump`**，别指望查 .so 依赖
- debugfs `dump` 的目标必须写**工作目录下的相对路径**，不能写 Cygwin 的 `/tmp`

## 验收清单

```bash
# 文件系统本身
e2fsck -fy newrootfs.img                  # 必须 0 errors

# 三个致命链接
debugfs -R "stat /bin"   newrootfs.img | grep symlink
debugfs -R "stat /lib"   newrootfs.img | grep symlink
debugfs -R "stat /sbin"  newrootfs.img | grep symlink

# PAM 那个缺失模块的引用必须没了
debugfs -R "cat /etc/pam.d/login" newrootfs.img | grep -c lastlog     # -> 0

# PAM 模块确实在
debugfs -R "stat /usr/lib/aarch64-linux-gnu/security/pam_unix.so" newrootfs.img

# setuid 位
debugfs -R "stat /usr/bin/sudo" newrootfs.img | grep -o 'Mode: *[0-9]*'  # 04755

# systemd 是完整的
debugfs -R "stat /lib/systemd/systemd" newrootfs.img

# 串口 getty 已就位
debugfs -R "ls /etc/systemd/system/getty.target.wants" newrootfs.img
```

## 补丁说明（patches/）

| 文件 | 目标路径 | 为什么 |
|---|---|---|
| `login.patched` | `/etc/pam.d/login` | 删掉对缺失的 `pam_lastlog.so` 的引用 |
| `login.orig` | — | 原版，方便 diff 对照 |
| `securetty` | `/etc/securetty` | 加上 `ttyFIQ0` |
| `serial-override.conf` | `/etc/systemd/system/serial-getty@.service.d/override.conf` | 串口直接给 root shell，绕过整个 login/PAM 栈 |
| `netup.service` | `/etc/systemd/system/netup.service` | 开机拉起网卡 |
| `netup.sh` | `/usr/local/sbin/netup.sh` | 网卡 up + DHCP + 静态兜底 |

`apply-fixes.sh` 会把它们写进镜像并建好两个 wants 链接。
