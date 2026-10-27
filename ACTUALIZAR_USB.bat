@echo off
chcp 65001 >nul
REM ===== EDITA SOLO ESTA LINEA: tu usuario de GitHub / nombre del repositorio =====
set REPO=DJVENTUZ/mainbit-asistente
REM ================================================================================
set LABEL=USB Blue
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "$ErrorActionPreference='Stop';" ^
 "$v=Get-Volume | Where-Object { $_.FileSystemLabel -eq '%LABEL%' -and $_.DriveLetter } | Select-Object -First 1;" ^
 "if(-not $v){ Write-Host 'No encuentro la USB llamada %LABEL%. Conectala y reintenta.' -ForegroundColor Red; exit 1 };" ^
 "$d=($v.DriveLetter+':\');" ^
 "$zip=Join-Path $env:TEMP 'MainbitAsistente.zip';" ^
 "Write-Host ('Descargando ultima version -> '+$d);" ^
 "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;" ^
 "Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/%REPO%/releases/download/latest/MainbitAsistente.zip' -OutFile $zip;" ^
 "Expand-Archive -Path $zip -DestinationPath $d -Force;" ^
 "Remove-Item $zip -Force;" ^
 "Write-Host 'LISTO: USB actualizada. Ejecuta '$d'MainbitAsistente\MainbitAsistente.exe' -ForegroundColor Green"
if errorlevel 1 ( echo. & echo Fallo la actualizacion. Revisa internet y el nombre del repositorio. )
pause
