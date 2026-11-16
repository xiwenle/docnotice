param(
  [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)
$startScript = Join-Path $ProjectRoot "deploy\windows\start_docnotice.bat"
if (-not (Test-Path -LiteralPath $startScript)) {
  throw "Start script not found: $startScript"
}
$taskCommand = '"' + $startScript + '"'
& schtasks.exe /Create /TN "docnotice" /TR $taskCommand /SC ONSTART /RU SYSTEM /RL HIGHEST /F
if ($LASTEXITCODE -ne 0) {
  throw "schtasks failed with exit code $LASTEXITCODE"
}
Write-Host "Scheduled task installed. Open taskschd.msc to configure restart-on-failure."