# 05 · 构建 1 GiB Armbian rootfs

## 目标

把一份完整的 Armbian rootfs 塞进 **p7（恰好 1 GiB = 1,073,741,824 字节）**，
并且：
- 保留符号链接（3189 个）
- 保留权限位（尤其是 setuid/setgid）
- 保留属主（uid/gid）
- 能被原厂 4.19.232 内核正常挂载、switch_root

---

## 为什么不能用官方镜像

| 项目 | 官方 Armbian 镜像 | 本板 p7 |
|---|---|---|
| 镜像总大小 | 3.7 GB（整盘） | 1 GiB |
| rootfs 解压后 | ~2 GB | 放不下 |
| fstab | 用磁盘 UUID | 需要 PARTUUID 或设备路径 |
| 串口 getty | 无 `ttyFIQ0` 配置 | 会失去救援通道 |
| 内核 | 6.18（无 fiq_debugger） | 必须用原厂 4.19.232 |

**结论：必须自己裁剪重建。**

---

## 原料

从 [Armbian](https://www.armbian.com/) 下载 RK3318 的镜像：

```
Armbian_26.11.0-trunk_Rk3318-box_noble_current_6.18.xx.img
```

我们用的是 `armbian-rk3318.img`（3,699,376,128 字节）。

### 提取 rootfs 分区

用 `fdisk -l` 或 `parted` 找到 Linux rootfs 分区的偏移。我们这份镜像：

- 偏移 `553,648,128`（= 528 MB）
- 长度 `3,144,679,424`

```bash
dd if=armbian-rk3318.img of=armroot.img \
   bs=1M skip=528 count=2999 status=progress
```

产物 `armroot.img`（3,144,679,424 字节），挂载或直接用 `debugfs` 读。

```bash
mkdir -p rootfs_out
# 方法一：有 root 权限时直接挂
sudo mount -o loop,ro armroot.img rootfs_out

# 方法二：无 root（Windows/Cygwin）用 debugfs rdump
debugfs -R "rdump / rootfs_out" armroot.img
```

我们用的是方法二（Cygwin + e2fsprogs），产物在 `oea/rootfs_out/`。

---

## 第一步：裁剪（`mk1g.sh`）

原始 rootfs 太大，先删掉这块板子用不上的东西：

```bash
#!/bin/bash
export PATH="/c/cygwin64/usr/bin:/c/cygwin64/usr/sbin:$PATH"
cd /path/to/oea
SRC=rootfs_out

# 包索引（可重新下载）
rm -rf $SRC/var/lib/apt/lists/*

# 内核头文件 / 源码（Armbian 自带，占几百 MB）
rm -rf $SRC/usr/src/linux-headers-*
rm -rf $SRC/usr/lib/modules/*/build $SRC/usr/lib/modules/*/source
rm -rf $SRC/usr/include

# 文档 / man / locale（嵌入式不需要）
rm -rf $SRC/usr/share/doc/*
rm -rf $SRC/usr/share/man/*
rm -rf $SRC/usr/share/locale/*
rm -rf $SRC/usr/share/i18n/*
rm -rf $SRC/usr/share/unicode/*
rm -rf $SRC/usr/share/zsh/*
rm -rf $SRC/usr/share/perl/*

# 固件：这块板子没有 WiFi/BT，只留可能用得上的
find $SRC/usr/lib/firmware -mindepth 1 -maxdepth 1 -type d \
     ! -name "brcm" ! -name "rtl*" ! -name "mediatek" -exec rm -rf {} + 2>/dev/null

echo "after trim: $(du -sb $SRC | awk '{printf "%.0f MB", $1/1048576}')"
```

裁剪后约 **815 MB**（含 21202 个文件），能塞进 1 GiB。

> ⚠️ **不要把 `/usr` 整个删掉**。usr-merge 之后 `/bin`、`/lib`、`/sbin` 都是指向
> `/usr/*` 的符号链接，删 `/usr` 会让系统彻底不能启动。

---

## 第二步：处理符号链接（关键坑）

`mke2fs -d <dir>` 有一个**致命限制**：它会**跳过符号链接**，不把它们写进镜像。

我们实测：`rootfs_out` 里有 **3189 个符号链接**，用 `mke2fs -d` 建完的镜像里
**一个都没有**。其中最关键的是这三个：

```
/bin  -> usr/bin
/lib  -> usr/lib
/sbin -> usr/sbin
```

没有它们，`/lib/aarch64-linux-gnu/security/pam_*.so` 全部找不到 → **PAM 栈 ABORT** →
登录时报 `PAM Failure, aborting: Critical error - immediate abort`。

这就是我们排查了很久的那个 bug。

### 解决方案：三阶段构建（`mkfs1g.sh`）

```bash
#!/bin/bash
export PATH="/c/cygwin64/usr/bin:/c/cygwin64/usr/sbin:$PATH"
cd /path/to/oea
OUT=newrootfs.img
STAGE=stage_out

rm -f $OUT
rm -rf $STAGE

echo "[1] recording symlinks..."
find rootfs_out -type l -printf '%p\t%l\n' 2>/dev/null > symlinks.tsv
echo "    $(wc -l < symlinks.tsv) symlinks recorded"

echo "[2] copying tree..."
mkdir -p $STAGE
cp -a rootfs_out/. $STAGE/ 2>/dev/null

echo "[3] removing symlinks from stage..."
find $STAGE -type l -delete 2>/dev/null
echo "    remaining symlinks: $(find $STAGE -type l 2>/dev/null | wc -l)"

echo "[4] creating ext4 (262144 blocks x 4096 = 1 GiB)..."
mke2fs -q -t ext4 -b 4096 -F -d $STAGE -L rootfs $OUT 262144 2>&1 | head -6

echo "[5] result:"
ls -la $OUT
```

- **步骤 1**：记录所有符号链接的 `路径 → 目标`
- **步骤 3**：把符号链接从 staging 目录删掉（否则 `mke2fs -d` 会报错或跳过）
- **步骤 4**：`262144` 块 × 4096 字节 = **1,073,741,824 字节 = 正好 1 GiB**

### 第三步：用 debugfs 重建符号链接

把 `symlinks.tsv` 转成 debugfs 命令（`sym_cmds.txt`，3189 行）：

```
symlink "/bin" "usr/bin"
symlink "/etc/alternatives/aclocal" "/usr/bin/aclocal-1.16"
symlink "/etc/alternatives/aclocal.1.gz" "/usr/share/man/man1/aclocal-1.16.1.gz"
...
```

生成脚本（任何能写文本的工具都行，这里是 bash + awk）：

```bash
awk -F'\t' '{ sub(/^rootfs_out/, ""); print "symlink \"" $1 "\" \"" $2 "\"" }' \
    symlinks.tsv > sym_cmds.txt
```

执行：

```bash
debugfs -w -f sym_cmds.txt newrootfs.img
```

### 第四步：恢复 uid/gid/mode

`mke2fs -d` 也不保留属主和权限位（尤其是 setuid）。我们的做法：

1. 用 `debugfs -R "ls -l -p <dir>"` 遍历原镜像的每个目录，输出格式是
   `/<mode>/<uid>/<gid>/<name>`（`-p` 是 machine-readable 模式）
2. 生成 71157 条 `sif` 命令

```bash
# 1. 列出所有目录
debugfs -R "ls -p /" armroot.img ...   # 递归枚举，脚本见 walkall.sh

# 2. 每个条目输出三行
sif "/lost+found" mode 0040700
sif "/lost+found" uid 0
sif "/lost+found" gid 0
sif "/bin.usr-is-merged" mode 0040755
...
```

```bash
debugfs -w -f meta_cmds.txt newrootfs.img
```

**验证 setuid**（最容易丢的一项）：

```bash
debugfs -R "stat /usr/bin/sudo" newrootfs.img | grep -i mode
# Mode:  04755    ← 必须是 4755
```

---

## 第五步：定制关键文件

### `/etc/fstab`

原厂内核 cmdline 是 `root=PARTUUID=614e0000-0000`，指向 p7。所以 fstab 里 `/` 那一行
必须用同样的 PARTUUID（**注意：这一版后来被推翻了，见 [09-storage.md](09-storage.md)，
因为 `/` 实际是 overlayfs，fstab 里不能有 `/` 条目**）：

```bash
debugfs -w -R "rm /etc/fstab" final2.img
debugfs -w -R "write fstab.new /etc/fstab" final2.img
debugfs -w -R "sif /etc/fstab mode 0100644" final2.img
```

### 串口 getty

必须加 `serial-getty@ttyFIQ0.service`，否则串口没有登录提示符：

```bash
debugfs -w -R "symlink /etc/systemd/system/getty.target.wants/serial-getty@ttyFIQ0.service /usr/lib/systemd/system/serial-getty@.service" final2.img
```

### hostname / 密码

- `/etc/hostname` → `armbian`（后来改成 `oeanas`）
- root 密码保留 Armbian 默认的 yescrypt 哈希（首次启动 firstrun 会让你改）

---

## 第六步：修 PAM（最重要的一个补丁）

### 问题

`/etc/pam.d/login` 第 82 行引用了 `pam_lastlog.so`，但**镜像里没有这个文件**：

```bash
$ ls rootfs_out/usr/lib/aarch64-linux-gnu/security/ | grep lastlog
（空）
$ find rootfs_out -name 'pam_lastlog*'
（空）
```

### 影响

PAM 在 `pam_start()` 阶段会 **dlopen 栈里引用的所有模块**（标了 `optional` 也不例外）。
任何一个失败 → 整条栈返回 `PAM_ABORT` → 登录时：

```
login: PAM Failure, aborting: Critical error - immediate abort
```

和密码**完全无关**，输什么都一样。

### 修复

删掉那一行：

```bash
grep -v 'pam_lastlog' rootfs_out/etc/pam.d/login > login.fixed
diff rootfs_out/etc/pam.d/login login.fixed
# 82d81
# < session    optional   pam_lastlog.so
```

写回镜像：

```bash
cat > fix2.txt <<'EOF'
cd /etc/pam.d
rm login
write work/login.fixed login
sif login mode 0100644
sif login uid 0
sif login gid 0
EOF

debugfs -w -f fix2.txt final2.img
```

> `pam_lastlog` 只负责在登录时打印 "Last login: ..." 信息，删掉无副作用。

### 同类问题自查

在任何裁剪过的 rootfs 上，都要检查这个：

```bash
# 列出 pam.d 里引用的所有模块
grep -rhoE 'pam_[a-z0-9_]+\.so' etc/pam.d/ | sort -u > refs.txt
# 逐个检查是否存在
while read m; do
  [ -f "usr/lib/aarch64-linux-gnu/security/$m" ] || echo "MISSING: $m"
done < refs.txt
```

我们跑了这个检查，**唯一的缺失就是 `pam_lastlog.so`**。

---

## 完整流水线

```bash
# 0. 准备
export PATH="/c/cygwin64/usr/bin:/c/cygwin64/usr/sbin:$PATH"

# 1. 提取 + 裁剪
dd if=armbian-rk3318.img of=armroot.img bs=1M skip=528 count=2999 status=progress
debugfs -R "rdump / rootfs_out" armroot.img
./mk1g.sh

# 2. 建壳（记录符号链接 → 拷贝 → 删链接 → mke2fs）
./mkfs1g.sh                       # → newrootfs.img (1 GiB)

# 3. 重建符号链接
debugfs -w -f sym_cmds.txt final2.img

# 4. 恢复权限/属主
debugfs -w -f meta_cmds.txt final2.img

# 5. 定制
debugfs -w -f fix2.txt final2.img       # pam / securetty / netup / getty
debugfs -w -f fstab_cmds.txt final2.img

# 6. 验收
e2fsck -fy final2.img
dumpe2fs -h final2.img | head -20
```

最终产物：

```
final2.img   1,073,741,824 B   ext4   fs UUID 5fcbedc6-e842-4d14-b6b6-db38bee44d6d
             21202 files        used ~815 MB
```

---

## debugfs 使用注意事项（血泪）

### 1. `write` 不接受绝对路径作为目标

```bash
# ❌ 错误：会创建一个叫 "/etc/pam.d/login" 的文件（名字里带斜杠）放在根目录
debugfs -w -R "write login.fixed /etc/pam.d/login" img

# ✅ 正确：先 cd，再用相对文件名
debugfs -w -R "cd /etc/pam.d" -R "rm login" ...
```

我们用 `-f` 批处理文件，正确写法：

```
cd /etc/pam.d
rm login
write work/login.fixed login
sif login mode 0100644
```

### 2. 写错了怎么发现

`e2fsck` 会告诉你：

```
Entry '/etc/pam.d/login' in / (2) has illegal characters in its name.  Fix? yes
```

一旦出现这个，**镜像已经脏了，建议从原始镜像重新 `cp` 一份重做**（我们就是这么做的）。

### 3. 一次 `-f` 只用一个 debugfs 会话

`debugfs -R` 每次都是新会话，`cd` 不会保持。要用 `cd` 就必须写进 `-f` 文件。

### 4. 镜像要改完再挂载

如果镜像已经被挂载（哪怕只读），`debugfs -w` 会拒绝或损坏。先 `umount`。

---

## Cygwin e2fsprogs 环境（Windows 无 root 的可行方案）

```bash
export PATH="/c/cygwin64/usr/bin:/c/cygwin64/usr/sbin:$PATH"
dumpe2fs -V
# dumpe2fs 1.44.5 (15-Dec-2018)
# Using EXT2FS Library version 1.44.5
```

可用工具：`mke2fs`、`debugfs`、`dumpe2fs`、`e2fsck`、`tune2fs`、`e2label`、
`mkfs.ext4`、`fsck.ext4`、`badblocks`。

**不可用**：`readelf`、`objdump`（`which` 找不到）。

**注意**：`debugfs dump` 的目标路径不能是 Cygwin 的 `/tmp`（会被解析到
`C:/Users/xxx/AppData/Local/Temp/`），**必须用工作目录下的相对路径**。

---

## 检查清单

镜像做完后，逐项确认：

```bash
# 1. 大小正好 1 GiB
ls -l final2.img        # 1073741824

# 2. 文件系统干净
e2fsck -fy final2.img   # 无 error

# 3. usr-merge 符号链接在
debugfs -R "stat /bin"  final2.img | grep -i "Fast link"
debugfs -R "stat /lib"  final2.img | grep -i "Fast link"
debugfs -R "stat /sbin" final2.img | grep -i "Fast link"

# 4. PAM 没有引用缺失模块
debugfs -R "cat /etc/pam.d/login" final2.img | grep -c lastlog   # 0

# 5. pam_unix.so 存在
debugfs -R "stat /usr/lib/aarch64-linux-gnu/security/pam_unix.so" final2.img

# 6. systemd 在
debugfs -R "stat /lib/systemd/systemd" final2.img

# 7. 串口 getty 链接在
debugfs -R "ls -l /etc/systemd/system/getty.target.wants" final2.img

# 8. root 账户正常
debugfs -R "cat /etc/passwd" final2.img | head -1
debugfs -R "cat /etc/shadow" final2.img | head -1
```

八项全过，这个镜像就能启动。
