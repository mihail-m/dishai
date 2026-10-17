@echo off
setlocal
title DishAI - Inversion Watch
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-windows.ps1" %*
if errorlevel 1 (
  echo.
  echo DishAI could not start. The error is shown above.
  echo Check your internet connection and try again.
  pause
  exit /b 1
)
