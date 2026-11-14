@echo off
REM Build the MailDaan fully offline Windows installer (dist\MailDaan-Setup-<version>.exe).
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build-installer.ps1" %*
exit /b %ERRORLEVEL%
