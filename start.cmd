@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Missing project environment. Run the setup commands in README.md first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -B -m scripts.launch %*
if errorlevel 1 (
  echo Startup failed. Please keep the error above for troubleshooting.
  pause
  exit /b 1
)
