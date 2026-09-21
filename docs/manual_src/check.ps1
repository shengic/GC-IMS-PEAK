param([Parameter(Mandatory=$true)][string]$Path)
# 用 Word 實際排版後回報頁數與空白頁——估的不算數。
# 一定要**具名**：docs/ 裡有兩份手冊，舊版的 Select-Object -First 1 會抓錯人。
# Version: 1.0 — by Albert Sheng（手冊建置，2026-09-21）
$ErrorActionPreference = "Stop"
$f = Get-Item -LiteralPath $Path
# COM 會接到「已經在跑的」那個 Word，所以 $w.Quit() 關掉的是整個應用程式，
# 連使用者自己開著的文件一起收掉（踩過）。先記下我來之前有沒有人在用。
$wasRunning = [bool](Get-Process WINWORD -ErrorAction SilentlyContinue)
$w = New-Object -ComObject Word.Application
$w.Visible = $false
$doc = $w.Documents.Open($f.FullName)
$doc.Repaginate()
$total = $doc.ComputeStatistics(2)
$p1 = 0; $p2 = 0
$seen = @{}
foreach ($par in $doc.Paragraphs) {
  $t = $par.Range.Text.Trim()
  if ($t.Length -gt 1) { $seen[[int]$par.Range.Information(3)] = 1 }
  if ($t -eq ([char]0x529F+[char]0x80FD+[char]0x8207+[char]0x4ECB+[char]0x9762+[char]0x8A73+[char]0x89E3) -and $p1 -eq 0) { $p1 = $par.Range.Information(3) }
  if ($t -eq ([char]0x64CD+[char]0x4F5C+[char]0x6559+[char]0x5B78) -and $p2 -eq 0) { $p2 = $par.Range.Information(3) }
}
$blank = @(); for ($p=1; $p -le $total; $p++) { if (-not $seen.ContainsKey($p)) { $blank += $p } }
Write-Output "TOTAL=$total P1=$p1 P2=$p2 BLANK=$($blank -join ',')"
$doc.Close(0)
# 本來就有人在用就只關我開的那一份，不要 Quit。
if (-not $wasRunning) { $w.Quit() }
[System.Runtime.InteropServices.Marshal]::ReleaseComObject($doc) | Out-Null
[System.Runtime.InteropServices.Marshal]::ReleaseComObject($w) | Out-Null
