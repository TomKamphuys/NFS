param(
    [string]$Executable = "dist\HALS Near Field Scanner\HALS Near Field Scanner.exe",
    [string]$RuntimeDirectory = "build\startup-check"
)

$ErrorActionPreference = "Stop"
$executablePath = (Resolve-Path -LiteralPath $Executable).Path
New-Item -ItemType Directory -Path $RuntimeDirectory -Force | Out-Null
$runtimePath = (Resolve-Path -LiteralPath $RuntimeDirectory).Path
$previousLocalAppData = $env:LOCALAPPDATA
try {
    # --help imports the GUI and scanner modules, then exits before constructing
    # the application or connecting to hardware. Keep config seeding isolated.
    $env:LOCALAPPDATA = $runtimePath
    $process = Start-Process -FilePath $executablePath -ArgumentList "--help" -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(45000)) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        throw "Frozen startup check timed out; check for an import error dialog."
    }
    if ($process.ExitCode -ne 0) {
        throw "Frozen startup check failed with exit code $($process.ExitCode)."
    }
    Write-Output "Frozen GUI and scanner imports passed."
}
finally {
    $env:LOCALAPPDATA = $previousLocalAppData
}
