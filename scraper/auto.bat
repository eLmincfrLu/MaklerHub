@echo off
rem MaklerHub scraper - Windows Task Scheduler ucun.
rem Istifade: auto.bat fast  |  auto.bat nightly  |  auto.bat baseline
rem Loq: scraper\logs\auto.log (firlanan); gozlenilmeyen xeta: scraper\logs\task.log
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
set MAKLERHUB_LOG_QUIET=1
if not exist "scraper\logs" mkdir "scraper\logs"
".venv\Scripts\python.exe" -m scraper.auto %* >> "scraper\logs\task.log" 2>&1
exit /b %ERRORLEVEL%
