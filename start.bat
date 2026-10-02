@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  MaklerHub hazirlanir...
if not exist .venv (
  python -m venv .venv || (echo Python tapilmadi. https://www.python.org/downloads/ unvanindan yukleyin. & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
python -m pip install -q --disable-pip-version-check -r requirements.txt
start "" cmd /c "timeout /t 4 >nul & start http://127.0.0.1:5000"
python run.py
pause
