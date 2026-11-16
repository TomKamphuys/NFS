param([string]$Compiler = "iscc")

$ErrorActionPreference = "Stop"
$root = Join-Path (Get-Location) ("build\uninstall-check-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $root -Force | Out-Null
$fixtureId = "HALS-test-" + [guid]::NewGuid().ToString("N").Substring(0, 16)
$include = Join-Path $PSScriptRoot "uninstall_cleanup.iss"
$installed = Join-Path $root "installed"
$owned = @(
    "$root\local\HALS Near Field Scanner",
    "$root\roaming\HALS Near Field Scanner",
    "$root\home\.hals-near-field-scanner",
    "$root\temp\HALS_working_project",
    "$root\tmp\HALS_working_project"
    "$root\local\Temp\HALS_working_project"
)
$preserved = @(
    "$root\home\Documents\HALS_Projects\Saved project\recording.wav",
    "$root\home\.matplotlib\matplotlibrc",
    "$root\local\Other App\settings.ini"
)
foreach ($folder in $owned) {
    New-Item -ItemType Directory -Path "$folder\nested" -Force | Out-Null
    Set-Content -LiteralPath "$folder\nested\settings.ini" -Value "fixture"
}
foreach ($file in $preserved) {
    New-Item -ItemType Directory -Path (Split-Path $file) -Force | Out-Null
    Set-Content -LiteralPath $file -Value "preserve"
}
Set-Content -LiteralPath "$root\payload.txt" -Value "installed payload"
# Compile the exact production cleanup rules against isolated test roots.
$script = @"
#define CleanupLocalRoot "$root\local"
#define CleanupRoamingRoot "$root\roaming"
#define CleanupHomeRoot "$root\home"
#define CleanupTempRoot "$root\temp"
#define CleanupTmpRoot "$root\tmp"
[Setup]
AppId=$fixtureId
AppName=HALS Uninstall Verification
AppVersion=1.0
DefaultDirName=$installed
PrivilegesRequired=lowest
UninstallDisplayName=HALS Uninstall Verification
OutputDir=$root
OutputBaseFilename=fixture-setup
ArchitecturesInstallIn64BitMode=x64compatible
[Files]
Source: "$root\payload.txt"; DestDir: "{app}"
#include "$include"
"@
Set-Content -LiteralPath "$root\fixture.iss" -Value $script
& $Compiler "$root\fixture.iss" *> "$root\compile.log"
if ($LASTEXITCODE -ne 0) { throw "Uninstall fixture compilation failed; see $root\compile.log" }
$setup = Start-Process -FilePath "$root\fixture-setup.exe" -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') -WindowStyle Hidden -Wait -PassThru
if ($setup.ExitCode -ne 0) { throw "Fixture installation failed: $($setup.ExitCode)" }
$registryPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\${fixtureId}_is1"
if (-not (Test-Path -LiteralPath $registryPath)) { throw "Fixture uninstall registration was not created" }
if (-not (Test-Path -LiteralPath "$installed\payload.txt")) { throw "Fixture payload was not installed" }
$uninstaller = Join-Path $installed 'unins000.exe'
# All recursive removal by this test's uninstaller is confined to its unique
# build directory. Never point its cleanup macros at real user data.
$resolvedRoot = (Resolve-Path -LiteralPath $root).Path
foreach ($target in ($owned + @($installed))) {
    $resolvedTarget = [IO.Path]::GetFullPath($target)
    if (-not $resolvedTarget.StartsWith($resolvedRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unexpected cleanup target: $resolvedTarget"
    }
}
$uninstall = Start-Process -FilePath $uninstaller -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', ('/LOG="' + $root + '\uninstall.log"')) -WindowStyle Hidden -Wait -PassThru
if ($uninstall.ExitCode -ne 0) { throw "Fixture uninstall failed: $($uninstall.ExitCode)" }
# The uninstaller's temporary child can finish removing itself after its parent.
for ($attempt = 0; $attempt -lt 50 -and (Test-Path -LiteralPath $installed); $attempt++) {
    Start-Sleep -Milliseconds 100
}
foreach ($target in ($owned + @($installed, $registryPath))) {
    if (Test-Path -LiteralPath $target) { throw "Uninstall left behind: $target" }
}
foreach ($file in $preserved) {
    if (-not (Test-Path -LiteralPath $file)) { throw "Uninstall removed saved/shared data: $file" }
    if ((Get-Content -LiteralPath $file -Raw).Trim() -ne 'preserve') { throw "Uninstall changed saved/shared data: $file" }
}
Write-Output "Uninstall verified: installed files, registration, settings, caches and temporary data removed; saved projects and shared data preserved."
Write-Output "Verification logs: $root"
