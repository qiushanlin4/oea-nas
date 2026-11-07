param([string]$Port="COM5",[int]$Baud=1500000,[int]$Seconds=8,[string]$Seq="",[double]$Gap=2.0)
# Seq: commands separated by | ; send next only after output settles or gap elapsed
$sp = New-Object System.IO.Ports.SerialPort
$sp.PortName=$Port; $sp.BaudRate=$Baud; $sp.DataBits=8
$sp.Parity=[System.IO.Ports.Parity]::None; $sp.StopBits=[System.IO.Ports.StopBits]::One
$sp.Handshake=[System.IO.Ports.Handshake]::None
$sp.DtrEnable=$false; $sp.RtsEnable=$false
try{ $sp.Open() }catch{ Write-Output ("OPENFAIL: " + $_.Exception.Message); exit 1 }
Write-Output "OPEN-OK"
$items = @()
if($Seq -ne ""){ $items = $Seq -split '\|' }
$sb = New-Object System.Text.StringBuilder
$sw=[Diagnostics.Stopwatch]::StartNew()
$ki=0
$nextAt=1.2
$lastByte=0.0
while($sw.Elapsed.TotalSeconds -lt $Seconds){
  $el=$sw.Elapsed.TotalSeconds
  if($ki -lt $items.Count -and $el -ge $nextAt -and ($el - $lastByte) -ge 0.7){
    $sp.Write($items[$ki] + "`r")
    $ki++
    $nextAt = $el + $Gap
  }
  $n=$sp.BytesToRead
  if($n -gt 0){
    $b=New-Object byte[] $n
    [void]$sp.Read($b,0,$n)
    [void]$sb.Append([Text.Encoding]::ASCII.GetString($b))
    $lastByte = $sw.Elapsed.TotalSeconds
  }
  Start-Sleep -Milliseconds 50
}
$sp.Close()
$t=$sb.ToString()
Write-Output ("LEN=" + $t.Length)
Write-Output "----"
Write-Output $t
