# Run from a checkout, or download this file from the project's GitHub repository.
[CmdletBinding()]
param([switch]$Uninstall, [switch]$NoAgents,
      [string]$InstallRoot = $(if ($env:OIU_INSTALL_ROOT) {$env:OIU_INSTALL_ROOT} else {Join-Path $env:LOCALAPPDATA 'OpenIPhoneUse\runtime'}),
      [string]$SourceDir = $env:OIU_SOURCE_DIR,
      [string]$Ref = $(if ($env:OIU_REF) {$env:OIU_REF} else {'main'}))
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
function Invoke-Native([string]$Command, [string[]]$Arguments) {
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Command failed with exit code $LASTEXITCODE" }
}
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
if ($InstallRoot -eq [IO.Path]::GetPathRoot($InstallRoot) -or $InstallRoot -eq $HOME) {throw 'Unsafe installation directory'}
if ((Test-Path $InstallRoot) -and ((Get-Item $InstallRoot).Attributes -band [IO.FileAttributes]::ReparsePoint)) {throw 'Installation root must not be a reparse point'}
$Marker = Join-Path $InstallRoot '.open-iphone-use-managed'
$BinDir = Join-Path $env:LOCALAPPDATA 'OpenIPhoneUse\bin'
$Launcher = Join-Path $BinDir 'open-iphone-use.exe'
$Executable = Join-Path $InstallRoot 'venv\Scripts\open-iphone-use.exe'
if ($Uninstall) {
    if (!(Test-Path $Marker)) {Write-Output 'No managed installation found'; exit 0}
    Invoke-Native $Executable @('register-agents','--uninstall')
    if ((Test-Path $Launcher) -and (Test-Path (Join-Path $InstallRoot 'launcher.sha256')) -and ((Get-FileHash $Launcher).Hash -eq (Get-Content (Join-Path $InstallRoot 'launcher.sha256') -Raw).Trim())) {Remove-Item $Launcher}
    # Remove only the PATH element setup owns, leaving all other entries intact.
    $PathMarker = Join-Path $InstallRoot 'added-user-path'
    if (Test-Path $PathMarker) {
        $CurrentPath = [string][Environment]::GetEnvironmentVariable('Path','User')
        [Environment]::SetEnvironmentVariable('Path',(($CurrentPath -split ';' | Where-Object {$_ -ne $BinDir}) -join ';'),'User')
    }
    Remove-Item -LiteralPath $InstallRoot -Recurse -Force
    Write-Output 'Removed managed runtime/CLI and unchanged agent entries. Device state, certificates and backups remain in your home directory.'
    exit 0
}
if ((Test-Path $InstallRoot) -and !(Test-Path $Marker)) {throw 'Refusing to overwrite an unmanaged directory'}
if (Test-Path $Launcher) {
    $HashFile = Join-Path $InstallRoot 'launcher.sha256'
    if (!(Test-Path $HashFile) -or ((Get-FileHash $Launcher).Hash -ne (Get-Content $HashFile -Raw).Trim())) {throw "CLI launcher already occupied or modified: $Launcher"}
}
New-Item -ItemType Directory -Force -Path $InstallRoot,$BinDir | Out-Null
New-Item -ItemType File -Force -Path $Marker | Out-Null
$Temp = Join-Path ([IO.Path]::GetTempPath()) ('oiu-setup-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Temp | Out-Null
try {
    if (!$SourceDir -and $PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot 'server\pyproject.toml'))) {$SourceDir = $PSScriptRoot}
    if (!$SourceDir) {
        $Zip = Join-Path $Temp 'source.zip'
        Invoke-WebRequest -Uri "https://github.com/semsturgut/open-iphone-use/archive/$Ref.zip" -OutFile $Zip
        Expand-Archive -LiteralPath $Zip -DestinationPath (Join-Path $Temp 'source')
        $SourceDir = (Get-ChildItem (Join-Path $Temp 'source') -Directory | Select-Object -First 1).FullName
    }
    if (!(Test-Path (Join-Path $SourceDir 'server\pyproject.toml'))) {throw 'Invalid source checkout'}
    $Python = $null
    if (Get-Command py -ErrorAction SilentlyContinue) {
        # Windows PowerShell 5 can turn redirected native stderr into a terminating
        # error. A missing interpreter is an expected probe failure, not setup failure.
        try {
            $Candidate = & py -3.12 -c 'import sys; print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0 -and $Candidate) {$Python = ($Candidate -join "`n").Trim()}
        } catch { $Python = $null }
    }
    if (!$Python) {
        if (!(Get-Command winget -ErrorAction SilentlyContinue)) {throw 'Install CPython 3.12 from python.org (or install App Installer/winget), then re-run setup'}
        Invoke-Native 'winget' @('install','--id','Python.Python.3.12','--exact','--scope','user','--accept-package-agreements','--accept-source-agreements')
        $Candidates = @((Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),(Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\python3.12.exe'))
        $Python = $Candidates | Where-Object {Test-Path $_} | Select-Object -First 1
        if (!$Python) {throw 'Python installed; open a new PowerShell and re-run setup so its launcher is available'}
    }
    Invoke-Native $Python @('-m','venv',(Join-Path $InstallRoot 'venv'))
    $VenvPython = Join-Path $InstallRoot 'venv\Scripts\python.exe'
    Invoke-Native $VenvPython @('-m','pip','install','--upgrade','pip')
    # The native HID provider is PyWinRT's compiled WinRT bindings, installed by platform markers.
    Invoke-Native $VenvPython @('-m','pip','install','--upgrade',(Join-Path $SourceDir 'server'))
    $Skill = Join-Path $InstallRoot 'agent-skill.md'
    Remove-Item -LiteralPath (Join-Path $InstallRoot 'SKILL.md') -Force -ErrorAction SilentlyContinue
    Copy-Item (Join-Path $SourceDir 'macos\Resources\open-iphone-use-skill\SKILL.md') $Skill -Force
    if (!$NoAgents) {Invoke-Native $Executable @('register-agents','--skill',$Skill)}
    # pip's native console launcher embeds the venv Python path; copying it preserves
    # Unicode paths and avoids execution-policy changes or lossy .cmd wrappers.
    Copy-Item $Executable ($Launcher + '.new') -Force
    Move-Item ($Launcher + '.new') $Launcher -Force
    [IO.File]::WriteAllText((Join-Path $InstallRoot 'launcher.sha256'),(Get-FileHash $Launcher).Hash)
    $UserPath = [string][Environment]::GetEnvironmentVariable('Path','User')
    if ($BinDir -notin ($UserPath -split ';')) {
        [Environment]::SetEnvironmentVariable('Path',(($UserPath.TrimEnd(';')+';'+$BinDir).TrimStart(';')),'User')
        New-Item -ItemType File -Force -Path (Join-Path $InstallRoot 'added-user-path') | Out-Null
    }
    $env:Path = "$BinDir;$env:Path"
    Write-Output "Installed. Start now: & '$Launcher' serve"
    Write-Output 'A new PowerShell session can use: open-iphone-use serve'
    Write-Output 'Bluetooth permission, phone pairing, certificate trust, Windows Firewall access and Safari sharing remain owner approvals.'
} finally {Remove-Item -LiteralPath $Temp -Recurse -Force -ErrorAction SilentlyContinue}
