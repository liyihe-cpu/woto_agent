@echo off
cd /d "%~dp0"
python main.py inspect
*** Add File: D:\woto\wotohub_collector\continue.cmd
@echo off
cd /d "%~dp0"
python main.py collect %*
*** Add File: D:\woto\wotohub_collector\status.cmd
@echo off
cd /d "%~dp0"
python main.py status --batch %1
*** Add File: D:\woto\wotohub_collector\retry_failed.cmd
@echo off
cd /d "%~dp0"
python main.py collect --resume %1 --retry-failed
