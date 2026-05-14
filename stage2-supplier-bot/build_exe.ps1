param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

function Resolve-PythonExe {
    $localPython = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python" -Recurse -Filter python.exe -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notlike "*\\Lib\\venv\\scripts\\nt\\python.exe" } |
        Select-Object -First 1 -ExpandProperty FullName
    if ($localPython) {
        return $localPython
    }

    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd -and $pythonCmd.Source -notlike "*WindowsApps*") {
        return $pythonCmd.Source
    }

    return $null
}

Push-Location $projectRoot
try {
    $pythonExe = Resolve-PythonExe
    if (-not $pythonExe) {
        throw "A usable Python 3 interpreter was not found. Install Python 3.12+ first."
    }

    & $pythonExe -c "import PyInstaller, requests" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Missing required Python packages. Install them with '$pythonExe -m pip install -r requirements-build.txt'."
    }

    $pyInstallerArgs = @("-m", "PyInstaller", "--noconfirm")

    if ($Clean) {
        $pyInstallerArgs += "--clean"
    }

    $pyInstallerArgs += "hcspec_bot.spec"

    & $pythonExe $pyInstallerArgs
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed."
    }
    Write-Host ""
    Write-Host "EXE created at dist\\hcspec-bot\\hcspec-bot.exe"
}
finally {
    Pop-Location
}
