@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem PyPDEVS 는 이 폴더의 pypdevs 에 들어 있어 따로 설치하지 않아도 된다 (pip 로 설치돼 있으면 그것을 써도 같다).
where python >nul 2>nul
if %errorlevel%==0 (set PY=python) else (set PY=py -3)
start "" http://127.0.0.1:8765
%PY% app.py
pause
