# One-time setup of a project-local MySQL 8.4 (Windows) — only if you don't already have MySQL.
# Downloads Oracle's official no-install ZIP into .mysql\ (git-ignored), writes my.ini (localhost only,
# X protocol off) and initialises an empty data directory with user root and an EMPTY password.
# Afterwards start it with:  powershell -ExecutionPolicy Bypass -File scripts\start_mysql.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dir = Join-Path $root ".mysql"
$server = Join-Path $dir "server"
if (Test-Path (Join-Path $server "bin\mysqld.exe")) { Write-Host "Already set up in $dir"; exit 0 }
New-Item -ItemType Directory -Force $dir | Out-Null
$zip = Join-Path $dir "mysql.zip"
Write-Host "Downloading MySQL 8.4.3 (~260 MB)..."
curl.exe -L -o $zip "https://downloads.mysql.com/archives/get/p/23/file/mysql-8.4.3-winx64.zip"
Expand-Archive -Path $zip -DestinationPath $dir
Rename-Item (Join-Path $dir "mysql-8.4.3-winx64") $server
Remove-Item $zip
$base = $server -replace '\\', '/'
$data = (Join-Path $dir "data\db") -replace '\\', '/'
@"
[mysqld]
basedir=$base
datadir=$data
port=3306
bind-address=127.0.0.1
mysqlx=OFF
character-set-server=utf8mb4
collation-server=utf8mb4_unicode_ci
[client]
port=3306
default-character-set=utf8mb4
"@ | Set-Content -Encoding ascii (Join-Path $dir "my.ini")
New-Item -ItemType Directory -Force (Join-Path $dir "data") | Out-Null
& (Join-Path $server "bin\mysqld.exe") --defaults-file="$(Join-Path $dir 'my.ini')" --initialize-insecure --console
Write-Host "Done. Start with: powershell -ExecutionPolicy Bypass -File scripts\start_mysql.ps1"
