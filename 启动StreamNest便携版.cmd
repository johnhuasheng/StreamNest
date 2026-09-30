@echo off
chcp 65001 >nul
cd /d "%~dp0"
"%~dp0runtime\node.exe" "%~dp0portable-launcher.mjs" %*
if errorlevel 1 (
  echo.
  echo StreamNest 启动失败。请查看 work\launcher-logs 中的日志。
  pause
  exit /b 1
)
exit /b 0
