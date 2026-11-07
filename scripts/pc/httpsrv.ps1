param([int]$Port = 8899, [string]$Root = "C:\Users\cheng2007\Documents\deepseek-harness\default-workspace\oea\http")

$Root = (Resolve-Path $Root).Path
$listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Any, $Port)
$listener.Start()
Write-Output ("LISTENING on " + $Port + " root=" + $Root)

while ($true) {
    $client = $listener.AcceptTcpClient()
    $client.NoDelay = $true
    try {
        $ns = $client.GetStream()
        $ns.ReadTimeout = 15000

        # read request headers
        $sb = New-Object System.Text.StringBuilder
        $one = New-Object byte[] 1
        while ($true) {
            $n = $ns.Read($one, 0, 1)
            if ($n -le 0) { break }
            [void]$sb.Append([char]$one[0])
            if ($sb.Length -ge 4) {
                $t = $sb.ToString()
                if ($t.Substring($t.Length-4,4) -eq "`r`n`r`n") { break }
            }
            if ($sb.Length -gt 65536) { break }
        }
        $head = $sb.ToString()
        $lines = $head -split "`r`n"
        $reqLine = $lines[0] -split " "
        $path = $reqLine[1]
        if ($path -like "http://*") {
            $i = $path.IndexOf('/', 7)
            if ($i -gt 0) { $path = $path.Substring($i) }
        }
        $q = $path.IndexOf('?')
        if ($q -ge 0) { $path = $path.Substring(0, $q) }
        $path = [System.Uri]::UnescapeDataString($path)
        if ($path -eq "/") { $path = "/index.txt" }
        $fp = Join-Path $Root $path.TrimStart('/')
        $fp = [System.IO.Path]::GetFullPath($fp)

        Write-Output ((Get-Date).ToString("HH:mm:ss") + " " + $reqLine[0] + " " + $path)

        if (-not (Test-Path -LiteralPath $fp -PathType Leaf)) {
            $b = [System.Text.Encoding]::ASCII.GetBytes("HTTP/1.0 404 Not Found`r`nContent-Length: 0`r`nConnection: close`r`n`r`n")
            $ns.Write($b, 0, $b.Length)
            $ns.Flush()
        } else {
            $len = (New-Object System.IO.FileInfo($fp)).Length
            $start = [int64]0
            $end = $len - 1
            $isRange = $false
            foreach ($h in $lines) {
                if ($h -match '^(?i)Range:\s*bytes=(\d*)-(\d*)\s*$') {
                    if ($matches[1] -ne "") { $start = [int64]$matches[1]; $isRange = $true }
                    if ($matches[2] -ne "") { $end = [int64]$matches[2]; $isRange = $true }
                }
            }
            if ($start -lt 0) { $start = 0 }
            if ($end -ge $len) { $end = $len - 1 }
            $sendLen = $end - $start + 1
            if ($sendLen -lt 0) { $sendLen = 0 }

            if ($isRange) { $st = "HTTP/1.0 206 Partial Content" } else { $st = "HTTP/1.0 200 OK" }
            $hd = $st + "`r`nContent-Type: application/octet-stream`r`nAccept-Ranges: bytes`r`nContent-Length: " + $sendLen + "`r`n"
            if ($isRange) { $hd += "Content-Range: bytes " + $start + "-" + $end + "/" + $len + "`r`n" }
            $hd += "Connection: close`r`n`r`n"
            $hb = [System.Text.Encoding]::ASCII.GetBytes($hd)
            $ns.Write($hb, 0, $hb.Length)

            $fs = [System.IO.File]::Open($fp, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
            try {
                [void]$fs.Seek($start, [System.IO.SeekOrigin]::Begin)
                $buf = New-Object byte[] 1048576
                $left = $sendLen
                while ($left -gt 0) {
                    $want = [int][Math]::Min([int64]$buf.Length, $left)
                    $got = $fs.Read($buf, 0, $want)
                    if ($got -le 0) { break }
                    $ns.Write($buf, 0, $got)
                    $left -= $got
                }
                $ns.Flush()
            } finally { $fs.Close() }
        }
    } catch {
        Write-Output ("ERR " + $_.Exception.Message)
    } finally {
        try { $client.Close() } catch {}
    }
}
