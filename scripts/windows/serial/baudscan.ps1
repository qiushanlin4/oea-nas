param([int[]]$Bauds=@(1500000,115200,921600,460800), [int]$Seconds=6)
foreach($b in $Bauds){
  $sp = New-Object System.IO.Ports.SerialPort
  $sp.PortName="COM5"; $sp.BaudRate=$b; $sp.DataBits=8
  $sp.Parity=[System.IO.Ports.Parity]::None; $sp.StopBits=[System.IO.Ports.StopBits]::One
  $sp.Handshake=[System.IO.Ports.Handshake]::None
  $sp.DtrEnable=$false; $sp.RtsEnable=$false
  try{ $sp.Open() }catch{ Write-Output ("BAUD " + $b + " OPENFAIL: " + $_.Exception.Message); continue }
  $sb = New-Object System.Text.StringBuilder
  $sw=[Diagnostics.Stopwatch]::StartNew()
  $sent=$false
  while($sw.Elapsed.TotalSeconds -lt $Seconds){
    if(-not $sent -and $sw.Elapsed.TotalSeconds -gt 1){
      $sp.Write("`r`n")
      $sent=$true
    }
    $n=$sp.BytesToRead
    if($n -gt 0){
      $b2=New-Object byte[] $n
      [void]$sp.Read($b2,0,$n)
      [void]$sb.Append([Text.Encoding]::ASCII.GetString($b2))
    }
    Start-Sleep -Milliseconds 50
  }
  $sp.Close()
  $t=$sb.ToString()
  Write-Output ("BAUD " + $b + " LEN=" + $t.Length)
  if($t.Length -gt 0){ Write-Output $t }
}
