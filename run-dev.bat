@echo off
REM Launch MailDaan from source (development convenience).
setlocal
cd /d "%~dp0"
python main.py %*
endlocal
