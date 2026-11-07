# deepscan.ps1 -- find a board that you know is powered on but whose IP you lost.
#
#   .\deepscan.ps1                       # scan 192.168.101.0/24
#   .\deepscan.ps1 -Subnet 10.9.8       # scan the direct-link subnet
#   .\deepscan.ps1 -Subnet 192.168.1 -Ports 22,445
#
# Why this exists
#   After we moved the board from a direct PC link to the router, nothing found
#   it: mDNS answered nothing, NetBIOS answered nothing, and the router's web UI
#   is fully RSA-encrypted (its /api/* device-list endpoints all 404).
#   A parallel ping sweep with a port check was the only thing that worked.
#
# Reading the result
#   A host that answers ping but has 22 and 445 CLOSED is probably not the board.
#   Watch out for proxy ARP: routers answer ARP on behalf of sleeping WiFi
#   clients, so several "distinct" IPs can share one MAC.  A real board shows
#   its own MAC and 22/139/445 open.

[CmdletBinding()]
param(
    [string]$Subnet = "192.168.101",
    [int[]]$Ports  = @(22, 139, 445, 8080),
    [int]$TimeoutMs = 400
)

$ErrorActionPreference = "Continue"

Write-Host "scanning $Subnet.1-254 ..." -ForegroundColor Cyan

$live = New-Object System.Collections.ArrayList

# --- ping sweep --------------------------------------------------------------
$jobs = 1..254 | ForEach-Object {
    $ip = "$Subnet.$_"
    [pscustomobject]@{ IP = $ip; Ping = (New-Object System.Net.NetworkInformation.Ping) }
}

foreach ($j in $jobs) {
    try {
        $task = $j.Ping.SendPingAsync($j.IP, $TimeoutMs)
        if ($task.Wait($TimeoutMs + 200) -and $task.Result.Status -eq 'Success') {
            [void]$live.Add([pscustomobject]@{
                IP   = $j.IP
                Ms   = [int]$task.Result.RoundtripTime
                TTL  = $task.Result.Options.Ttl
                Ports = @()
            })
        }
    } catch { }
    $j.Ping.Dispose()
}

Write-Host "alive: $($live.Count)" -ForegroundColor Green

# --- port scan on the survivors ---------------------------------------------
foreach ($h in $live) {
    $open = @()
    foreach ($p in $Ports) {
        $c = New-Object System.Net.Sockets.TcpClient
        try {
            $t = $c.ConnectAsync($h.IP, $p)
            if ($t.Wait(600) -and $c.Connected) { $open += $p }
        } catch { }
        finally { $c.Close() }
    }
    $h.Ports = $open
}

# --- report ------------------------------------------------------------------
$live | Sort-Object { $_.IP.Split('.')[-1] -as [int] } | Format-Table -AutoSize IP, Ms, TTL, @{
    Name = 'Ports'; Expression = { if ($_.Ports) { $_.Ports -join ',' } else { '-' } }
}

Write-Host ""
Write-Host "looking for the NAS (needs 22 and/or 445):" -ForegroundColor Cyan
$nas = $live | Where-Object { $_.Ports -contains 22 -or $_.Ports -contains 445 }
if ($nas) {
    $nas | ForEach-Object {
        Write-Host ("  {0}  ports: {1}" -f $_.IP, ($_.Ports -join ',')) -ForegroundColor Green
    }
    Write-Host ""
    Write-Host "try:  ssh root@$($nas[0].IP)" -ForegroundColor Yellow
} else {
    Write-Host "  none found." -ForegroundColor Red
    Write-Host ""
    Write-Host "If you are sure the board is on, the usual causes are:" -ForegroundColor Yellow
    Write-Host "  * it never got a DHCP lease (netup.sh must be the v8 'resident dhcpcd' one)"
    Write-Host "  * it is still on 10.9.8.77 from a previous direct-PC boot"
    Write-Host "  * the cable is in the router but the router port is down"
}

# --- ARP table, to spot proxy-ARP weirdness ---------------------------------
Write-Host ""
Write-Host "ARP entries on $Subnet.x (identical MACs = router proxy ARP):" -ForegroundColor Cyan
(arp -a) | Select-String $Subnet | ForEach-Object { $_.Line.Trim() }
