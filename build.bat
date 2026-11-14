@echo off
REM Build the MailDaan Windows release (one-folder distribution + ZIP).
setlocal
cd /d "%~dp0"

echo [1/3] Installing build requirements...
python -m pip install -r requirements-build.txt || goto :error

echo [2/3] Running PyInstaller...
python -m PyInstaller --noconfirm --clean maildaan.spec || goto :error

echo [3/3] Creating release ZIP...
powershell -NoProfile -Command "Compress-Archive -Path 'dist\MailDaan' -DestinationPath 'dist\MailDaan-1.1.2.zip' -Force" || goto :error

echo.
echo Build complete: dist\MailDaan\MailDaan.exe
echo Release ZIP:    dist\MailDaan-1.1.2.zip
echo Offline installer: run build-installer.bat to build dist\MailDaan-Setup-1.1.2.exe
exit /b 0

:error
echo Build failed.
exit /b 1
