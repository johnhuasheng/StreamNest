@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动 StreamNest，请稍候...
set "STREAMNEST_NODE=%ProgramFiles%\nodejs\node.exe"
if not exist "%STREAMNEST_NODE%" set "STREAMNEST_NODE=node"
"%STREAMNEST_NODE%" streamnest-launcher.mjs %*
if errorlevel 1 (
  echo.
  echo 启动失败，请把这个窗口里的提示发给我。
  pause
  exit /b 1
)
echo StreamNest 已经启动，这个窗口可以关闭。
ping 127.0.0.1 -n 3 >nul
