@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
for /f "usebackq delims=" %%B in (`powershell -NoProfile -Command "(Get-Content -Raw run_state.json | ConvertFrom-Json).batch_id"`) do set CURRENT_BATCH=%%B
echo Step 1: finish bd and stop before eg.
python -u vue_full_collector.py --resume %CURRENT_BATCH% --stop-after-country bd
if errorlevel 1 goto :failed
echo Step 2: create 1k batch beginning with eg.
python -u vue_full_collector.py --start-after-country bd
goto :end
:failed
echo bd was not completed. Review collector_live.log and run this file again.
:end
pause
