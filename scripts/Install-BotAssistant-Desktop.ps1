param(
    [string]$PackageDirectory = $PSScriptRoot
)

$ErrorActionPreference = "Stop"
if ($env:CODEX_WINDOWS_SANDBOX_PACKAGE_FAMILY) {
    throw "This Codex shell redirects LocalAppData. Run this installer from File Explorer or a normal PowerShell window."
}
$source = (Resolve-Path -LiteralPath $PackageDirectory).Path
$exe = Join-Path $source "BotAssistant.exe"
$icon = Join-Path $source "bot-assistant.ico"
if (-not (Test-Path -LiteralPath $exe) -or -not (Test-Path -LiteralPath $icon)) {
    throw "Run this script from the extracted Windows package."
}

$install = Join-Path $env:LOCALAPPDATA "BotAssistant"
New-Item -ItemType Directory -Path $install -Force | Out-Null
$installedExe = Join-Path $install "BotAssistant.exe"
$installedIcon = Join-Path $install "bot-assistant-0.7.1.ico"
Copy-Item -LiteralPath $exe -Destination $installedExe -Force
Copy-Item -LiteralPath $icon -Destination $installedIcon -Force

$desktop = [Environment]::GetFolderPath("DesktopDirectory")
if (-not (Test-Path -LiteralPath $desktop)) {
    throw "Windows desktop folder was not found."
}
$shortcutName = "bot" + [char]0x52A9 + [char]0x624B + ".lnk"
$shortcutPath = Join-Path $desktop $shortcutName
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $installedExe
$shortcut.Arguments = ""
$shortcut.WorkingDirectory = $install
$shortcut.IconLocation = "$installedIcon,0"
$shortcut.Description = "Bot Assistant multi-account control desk"
$shortcut.Save()
Write-Output "Desktop shortcut updated: $shortcutPath"
