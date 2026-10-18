@echo off
setlocal
title AirPath Mobile
echo Starting AirPath Mobile...
echo Preview: http://127.0.0.1:5180
echo.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0mobile\scripts\start-windows.ps1" %*
if errorlevel 1 (
  echo.
  echo AirPath Mobile could not start. The error is shown above.
  echo Startup log: %~dp0.local\mobile-launch.log
  echo You can also open http://127.0.0.1:5180 if the preview is already running.
  pause
  exit /b 1
)
