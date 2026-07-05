@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
title tao_video_short

echo ============================================
echo   tao_video_short - AI cat clip tu dong
echo ============================================
echo.

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Tao moi truong ao Python...
  py -3.14 -m venv .venv
  if errorlevel 1 python -m venv .venv
)

echo [2/3] Cai dat thu vien (lan dau co the mat vai phut)...
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo !!! Cai dat that bai. Kiem tra ket noi mang roi chay lai run.bat
  pause
  exit /b 1
)

echo.
echo [3/3] Dang khoi dong... Trinh duyet se tu mo tai http://127.0.0.1:8000
echo     (De tat ung dung: dong cua so nay hoac bam Ctrl+C)
echo.
start "" http://127.0.0.1:8000
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

pause
