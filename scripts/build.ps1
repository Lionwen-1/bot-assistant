param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $projectRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    & $Python -m venv (Join-Path $projectRoot ".venv")
    if ($LASTEXITCODE -ne 0) { throw "无法创建 Python 虚拟环境" }
}
& $venvPython -m pip install -r (Join-Path $projectRoot "requirements-build.txt")
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }
& $venvPython -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw "测试未通过" }
& $venvPython -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name BotAssistant `
    --icon (Join-Path $projectRoot "assets\bot-assistant.ico") `
    --add-data "$(Join-Path $projectRoot 'assets');assets" `
    --add-data "$(Join-Path $projectRoot 'server\manager.py');server" `
    --add-data "$(Join-Path $projectRoot 'server\thinking_plugin');server\thinking_plugin" `
    --distpath (Join-Path $projectRoot "release") `
    --workpath (Join-Path $projectRoot "build") `
    --specpath (Join-Path $projectRoot "build") `
    (Join-Path $projectRoot "run.pyw")
if ($LASTEXITCODE -ne 0) { throw "EXE 构建失败" }
$exe = Join-Path $projectRoot "release\BotAssistant.exe"
$releaseIcon = Join-Path $projectRoot "release\bot-assistant.ico"
Copy-Item -LiteralPath (Join-Path $projectRoot "assets\bot-assistant.ico") -Destination $releaseIcon -Force
$marker = Join-Path $env:TEMP "bot-assistant-build-selftest.json"
if (Test-Path -LiteralPath $marker) { Remove-Item -LiteralPath $marker -Force }
$process = Start-Process -FilePath $exe -ArgumentList @("--self-test", ('"' + $marker + '"')) `
    -PassThru -Wait -WindowStyle Hidden
if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $marker)) {
    throw "EXE 自检失败"
}
$result = Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json
if (-not $result.ok -or $result.app_version -ne "0.7.1" -or $result.local_manager -ne "0.5.0" -or -not $result.native_manager) {
    throw "EXE 内缺少本机或服务器管理组件"
}
Remove-Item -LiteralPath $marker -Force
$licenseDir = Join-Path $projectRoot "release\third_party_licenses"
& $venvPython (Join-Path $projectRoot "scripts\collect_third_party_licenses.py") $licenseDir
if ($LASTEXITCODE -ne 0) { throw "第三方许可提取失败" }
$package = Join-Path $projectRoot "release\BotAssistant-0.7.1-win64.zip"
if (Test-Path -LiteralPath $package) { Remove-Item -LiteralPath $package -Force }
Compress-Archive -LiteralPath @(
    $exe,
    $releaseIcon,
    (Join-Path $projectRoot "scripts\Install-BotAssistant-Desktop.ps1"),
    (Join-Path $projectRoot "README.md"),
    (Join-Path $projectRoot "README.en.md"),
    (Join-Path $projectRoot "NATIVE_SETUP.md"),
    (Join-Path $projectRoot "LICENSE"),
    (Join-Path $projectRoot "NOTICE"),
    (Join-Path $projectRoot "COPYRIGHT.md"),
    (Join-Path $projectRoot "THIRD_PARTY.md"),
    $licenseDir,
    (Join-Path $projectRoot "docs")
) -DestinationPath $package -CompressionLevel Optimal
Get-FileHash -Algorithm SHA256 -LiteralPath $exe,$package | Format-Table Path,Hash -AutoSize
Write-Output "构建完成：$exe"
Write-Output "发布包：$package"
