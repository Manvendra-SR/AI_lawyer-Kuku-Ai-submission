# Starts the project-local MySQL 8.4 server (installed in .mysql/ from Oracle's official no-install ZIP).
# Only needed if you do NOT already have MySQL running. Listens on 127.0.0.1:3306, user root, empty password.
# Stop it with Ctrl+C in this window (or: .mysql\server\bin\mysqladmin.exe -u root shutdown).
$root = Split-Path -Parent $PSScriptRoot
$mysqld = Join-Path $root ".mysql\server\bin\mysqld.exe"
$ini = Join-Path $root ".mysql\my.ini"
if (-not (Test-Path $mysqld)) { Write-Error "Bundled MySQL not found at $mysqld. Use your own MySQL and set DATABASE_URL in .env."; exit 1 }
Write-Host "Starting MySQL on 127.0.0.1:3306 (Ctrl+C to stop)..."
& $mysqld --defaults-file="$ini" --console
