@echo off
setlocal
cd /d "%~dp0..\.."
if not exist "logs" mkdir "logs"
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv not found. Run: python -m venv .venv
  exit /b 1
)
echo [%date% %time%] starting docnotice >> "logs\uvicorn.log"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 >> "logs\uvicorn.log" 2>&1
exit /b %ERRORLEVEL%