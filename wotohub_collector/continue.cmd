@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
python -u vue_full_collector.py --resume latest %*

