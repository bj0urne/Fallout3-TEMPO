# Builds TEMPO.dll (32-bit) with llvm-mingw and installs it into Data\FOSE\Plugins.
#   .\build.ps1            build + install (keeps an existing TEMPO.ini)
#   .\build.ps1 -NoInstall build only
param([switch]$NoInstall)
$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$cxx = Get-Command i686-w64-mingw32-clang++ -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
if (-not $cxx) {
    $cxx = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Directory -Filter 'MartinStorsjo.LLVM-MinGW*' |
        ForEach-Object { Get-ChildItem $_.FullName -Recurse -Filter 'i686-w64-mingw32-clang++.exe' } |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $cxx) { throw 'i686-w64-mingw32-clang++ not found (install MartinStorsjo.LLVM-MinGW.UCRT with winget)' }

$out = Join-Path $root 'build'
New-Item -ItemType Directory -Force $out | Out-Null
$dll = Join-Path $out 'TEMPO.dll'

& $cxx -std=c++17 -O2 -msse2 -mfpmath=sse -Wall -Wextra -shared -static -s `
    -o $dll (Join-Path $root 'src\main.cpp') -lwinmm
if ($LASTEXITCODE -ne 0) { throw "compile failed ($LASTEXITCODE)" }
Write-Host "built $dll"

if (-not $NoInstall) {
    $plugins = Join-Path $root '..\Data\FOSE\Plugins'
    New-Item -ItemType Directory -Force $plugins | Out-Null
    Copy-Item $dll $plugins -Force
    $ini = Join-Path $plugins 'TEMPO.ini'
    if (-not (Test-Path $ini)) { Copy-Item (Join-Path $root 'TEMPO.ini') $ini }
    Write-Host "installed to $((Resolve-Path $plugins).Path)"
}

