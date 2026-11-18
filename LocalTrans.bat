@echo off
rem Launch Local Trans without a console window
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo First-time setup: installing dependencies with uv...
  uv sync || pause
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0main.py"
