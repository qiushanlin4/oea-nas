# PC 端工具

## httpsrv.ps1 —— 极简 HTTP 服务器

当板子进入 buildroot 救援 shell 时，需要从 PC 把镜像传过去。
`scp` / `sftp` 都没有，只有一个 busybox `wget`。

所以我们在 PC 上起一个 HTTP 服务器。

### 为什么不用 IIS / Python / nginx

- **IIS**：需要管理员权限，要配站点
- **Python `-m http.server`**：我们机器上没有 Python
- **nginx**：要装

`httpsrv.ps1` 用 .NET 自带的 `TcpListener`，**不需要编译、不需要管理员、不需要 URL ACL**。

### 用法

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\httpsrv.ps1 `
    -Port 8899 -Root "C:\path\to\images"
```

输出：

```
LISTENING on 8899 root=C:\path\to\images
10:15:03 GET /index.txt
10:15:31 GET /rootfs.img
```

### 特性

- 手写 HTTP/1.0 解析（读请求行 + 头，直到 `\r\n\r\n`）
- 支持 `Range: bytes=` → `206 Partial Content`（板子 wget 断点续传用）
- 1 MB 缓冲流式发送，不把整个文件读进内存
- 404 处理
- 单线程串行（够用，一次只传一个文件）

### 验证

```bash
# PC 本机
curl --noproxy '*' -s http://127.0.0.1:8899/index.txt

# 板子（buildroot shell 里）
wget -O /tmp/t.txt http://10.9.8.10:8899/index.txt
cat /tmp/t.txt
```

> ⚠️ **`--noproxy '*'` 必须加**。我们的机器上有个坏掉的 `HTTP_PROXY` 环境变量，
> 不带这个参数 curl 会走代理然后收到 502。

### 吞吐

实测 **19 MB/s**（跑满百兆网口），1 GiB 镜像约 55–70 秒。

---

## proxy.ps1 —— 极简 HTTP 代理

板子刚装好时没有外网路由（只有 `10.9.8.0/24`），但需要 `apt-get update`。
PC 有外网，所以让板子走 PC 的代理。

### 为什么是 C# 而不是纯 PowerShell

我们先写了纯 PowerShell 版（`httpproxy.ps1`），**能收到请求但不回包** ——
`NetworkStream` 管道在 PS 里行为诡异。

改用 `Add-Type -TypeDefinition <C#源码> -Language CSharp`：

- 用系统自带的 `csc.exe` 编译，**不需要 .NET SDK**
- 不受 `dotnet build` 损坏影响（我们的 `dotnet build` 曾经全线报 `MSB3883: 未預期的例外狀況`）

### 用法

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\proxy.ps1
# LISTENING 10.9.8.10:3128
```

板子端：

```bash
cat > /etc/apt/apt.conf.d/95proxy <<'EOF'
Acquire::http::Proxy "http://10.9.8.10:3128";
Acquire::https::Proxy "http://10.9.8.10:3128";
EOF

curl -x http://10.9.8.10:3128 http://mirrors.tuna.tsinghua.edu.cn/
# HTTP/1.1 301 Moved Permanently, t=0.126s
```

### 什么时候不需要

**板子能通过路由器上网后就不需要了。** 我们是这么关掉的：

```bash
mv /etc/apt/apt.conf.d/95proxy /etc/apt/apt.conf.d/95proxy.disabled
```

然后 `apt-get update` 直连成功。

### 实现要点

- `MiniProxy` 类，`Run(bindIp, port)` 主循环
- `Handle(TcpClient)` 处理单个连接
- 支持 `CONNECT` 隧道（HTTPS）和普通 HTTP 转发
- `ReadHead(NetworkStream)` 逐字节读到 `\r\n\r\n`
- `Pump(NetworkStream a, NetworkStream b)` 双向 64 KB 缓冲
