# Opens dist/basara4-windows.zip as a player gets it, on a Windows without MSYS2: no game or player
# file ships, bin/basara4-core.exe finds every DLL it links, the frozen launcher imports what the
# preparation scripts need and its window stays open (.github/workflows/windows-zip.yml).
# Usage: powershell -File smoke-test.ps1 path\to\basara4-windows.zip
param([Parameter(Mandatory)][string]$Package)
$ErrorActionPreference = 'Stop'
# A player's PATH: a DLL found only in MSYS2 or another toolchain must not hide a missing one.
$env:PATH = "$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\WindowsPowerShell\v1.0"
$work = Join-Path ([IO.Path]::GetTempPath()) ("basara4-smoke-" + [guid]::NewGuid())
Expand-Archive -LiteralPath $Package -DestinationPath $work
$tree = Join-Path $work 'basara4-windows'
$failed = @()

# Forbidden files that must not ship: game files and player-created files.
$forbidden = 'eboot.bin', 'sce_module', 'sce_sys', 'savedata', '*.log',
    'settings.json', 'game_icon.ico', 'mods.json', 'patches.json', 'fsr4_shaders', 'nvngx_dlss*.dll', '*.spv', '*.pkg',
    '*.sprx', '*.prx'
$found = Get-ChildItem -LiteralPath $tree -Recurse -Force |
    Where-Object { $name = $_.Name; $forbidden | Where-Object { $name -like $_ } }
if ($found) { $failed += "files that must not ship: $($found.FullName -join ', ')" }

# A missing DLL stops the probe before main (0xC0000135).
$probeExe = Join-Path $tree 'bin\basara4-core.exe'
if (-not (Test-Path $probeExe)) {
    $probeExe = Join-Path $tree 'basara4-core.exe'
}
$probe = Start-Process -FilePath $probeExe -ArgumentList '--cpu-features' `
    -NoNewWindow -PassThru -RedirectStandardOutput (Join-Path $work 'probe.log')
$null = $probe.Handle  # without it ExitCode stays empty after WaitForExit(timeout)
if (-not $probe.WaitForExit(60000)) {
    Stop-Process -Id $probe.Id -Force
    $probe.WaitForExit()
    $failed += 'basara4-core.exe --cpu-features did not finish in 60 s'
} elseif ($probe.ExitCode -ne 0) {
    $failed += ('basara4-core.exe --cpu-features exited 0x{0:X}' -f $probe.ExitCode)
}
Get-Content (Join-Path $work 'probe.log')

$launcher = Join-Path $tree 'Sengoku Basara 4.exe'
$scriptsDir = Join-Path $tree 'scripts'
if (-not (Test-Path $scriptsDir)) {
    New-Item -ItemType Directory -Path $scriptsDir -Force | Out-Null
}
$check = Join-Path $scriptsDir 'smoke_imports.py'
# An exception left to the windowed launcher opens an error dialog that waits for a click: the
# check reports it and exits instead, and a launcher still running after a minute is stopped.
Set-Content -LiteralPath $check -Value @'
import sys, traceback
try:
    import PIL.Image, iced_x86, numpy, etcpak
except Exception:
    traceback.print_exc()
    sys.exit(1)
print('imports: PIL', PIL.__version__, 'iced_x86', 'numpy', numpy.__version__, 'etcpak')
'@
$script = Start-Process -FilePath $launcher -ArgumentList '--run-script', 'smoke_imports.py' -PassThru `
    -RedirectStandardOutput (Join-Path $work 'script.log') -RedirectStandardError (Join-Path $work 'script.err')
$null = $script.Handle
if (-not $script.WaitForExit(60000)) {
    Stop-Process -Id $script.Id -Force
    $script.WaitForExit()
    $failed += 'Sengoku Basara 4.exe --run-script did not finish in 60 s'
} elseif ($script.ExitCode -ne 0) {
    $failed += "Sengoku Basara 4.exe --run-script could not import dependencies ($($script.ExitCode))"
}
Get-Content (Join-Path $work 'script.log'), (Join-Path $work 'script.err')

$window = Start-Process -FilePath $launcher -PassThru
Start-Sleep -Seconds 15
if ($window.HasExited) {
    $failed += "the launcher exited ($($window.ExitCode)) instead of staying open"
} else {
    Stop-Process -Id $window.Id -Force
    $window.WaitForExit()
}

Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
if ($failed) {
    $failed | ForEach-Object { Write-Host "FAIL: $_" }
    exit 1
}
Write-Host 'OK: the package opens without MSYS2'
