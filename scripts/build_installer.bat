@echo off
setlocal EnableDelayedExpansion
title Lumi DM - Build Installer

:: Resolve ROOT to an absolute path (scripts\ is one level below project root)
pushd "%~dp0.."
set ROOT=%CD%
popd
cd /d "%ROOT%"

echo.
echo  Lumi DM - Full Installer Build
echo  ======================================
echo.

:: ── 1. Check Python ─────────────────────────────────────────────────────────
echo [1/7] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found. Install Python 3.10+ and add to PATH.
    pause & exit /b 1
)
for /f "tokens=*" %%v in ('python --version') do echo  Found: %%v

:: ── 2. Install Python dependencies ──────────────────────────────────────────
echo.
echo [2/7] Installing Python dependencies...
python -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo  ERROR: pip install failed.
    pause & exit /b 1
)
python -m pip install pyinstaller --quiet
echo  Done.

:: ── 3. Download aria2c (torrent support) ─────────────────────────────────────
echo.
echo [3/7] Checking aria2c (torrent engine)...
set ARIA2C_EXE=%ROOT%\tools\aria2c.exe
if not exist "%ARIA2C_EXE%" (
    echo  Downloading aria2c...
    :: Use PowerShell to download aria2c static binary
    powershell -NoProfile -Command ^
        "$url='https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-win-64bit-build1.zip';" ^
        "$zip='%TEMP%\aria2c.zip';" ^
        "Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing;" ^
        "Expand-Archive -Path $zip -DestinationPath '%TEMP%\aria2c_extract' -Force;" ^
        "Copy-Item '%TEMP%\aria2c_extract\aria2-1.37.0-win-64bit-build1\aria2c.exe' '%ARIA2C_EXE%';" ^
        "Remove-Item $zip -Force;" ^
        "Remove-Item '%TEMP%\aria2c_extract' -Recurse -Force"
    if not exist "%ARIA2C_EXE%" (
        echo  WARNING: aria2c download failed. Torrent support will be disabled.
        echo           You can manually place aria2c.exe in tools\aria2c.exe
        set ARIA2C_EXE=
    ) else (
        echo  Downloaded: tools\aria2c.exe
    )
) else (
    echo  Found: tools\aria2c.exe
)

:: ── 3b. Download FFmpeg (video merging / HD quality) ─────────────────────────
echo.
echo [3b] Checking FFmpeg (HD video merging)...
set FFMPEG_EXE=%ROOT%\tools\ffmpeg.exe
if not exist "%FFMPEG_EXE%" (
    echo  Downloading FFmpeg...
    powershell -NoProfile -Command ^
        "$url='https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip';" ^
        "$zip='%TEMP%\ffmpeg_dl.zip';" ^
        "Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing;" ^
        "Expand-Archive -Path $zip -DestinationPath '%TEMP%\ffmpeg_extract' -Force;" ^
        "$ffexe=(Get-ChildItem '%TEMP%\ffmpeg_extract' -Recurse -Filter 'ffmpeg.exe' | Select-Object -First 1).FullName;" ^
        "if ($ffexe) { Copy-Item $ffexe '%FFMPEG_EXE%' };" ^
        "Remove-Item $zip -Force;" ^
        "Remove-Item '%TEMP%\ffmpeg_extract' -Recurse -Force"
    if not exist "%FFMPEG_EXE%" (
        echo  WARNING: FFmpeg download failed. HD quality merging will be disabled.
        echo           You can manually place ffmpeg.exe in tools\ffmpeg.exe
        set FFMPEG_EXE=
    ) else (
        echo  Downloaded: tools\ffmpeg.exe
    )
) else (
    echo  Found: tools\ffmpeg.exe
)

:: ── 4. Build server binary with PyInstaller ──────────────────────────────────
echo.
echo [4/7] Bundling Python server with PyInstaller...
rmdir /s /q dist\server 2>nul
mkdir dist\server 2>nul

:: Build PyInstaller command - add aria2c and ffmpeg if present
set PYINST_CMD=python -m PyInstaller --onefile --name LUMIDM-server --distpath "%ROOT%\dist\server" --workpath "%ROOT%\build\pyinstaller" --specpath "%ROOT%\build" --noconfirm --exclude-module PyQt5 --exclude-module PyQt6 --exclude-module PySide2 --exclude-module PySide6 --exclude-module tkinter --add-data "%ROOT%\core;core"

if defined ARIA2C_EXE (
    if exist "%ARIA2C_EXE%" (
        set PYINST_CMD=!PYINST_CMD! --add-binary "%ARIA2C_EXE%;."
    )
)

if defined FFMPEG_EXE (
    if exist "%FFMPEG_EXE%" (
        set PYINST_CMD=!PYINST_CMD! --add-binary "%FFMPEG_EXE%;tools"
    )
)

set PYINST_CMD=!PYINST_CMD! "%ROOT%\server.py"

%PYINST_CMD%

if errorlevel 1 (
    echo  ERROR: PyInstaller failed. See output above.
    pause & exit /b 1
)
echo  Server binary: dist\server\LUMIDM-server.exe

:: ── 5. Generate icons ─────────────────────────────────────────────────────────
echo.
echo [5/7] Generating icons...
python tools\generate_icons.py >nul 2>&1
if errorlevel 1 (
    echo  WARNING: Icon generation failed, using existing icons.
) else (
    echo  Done.
)

:: ── 6. Install Electron dependencies ────────────────────────────────────────
echo.
echo [6/7] Installing Electron dependencies...
cd electron
call npm install --prefer-offline 2>nul || call npm install
if errorlevel 1 (
    cd ..
    echo  ERROR: npm install failed.
    pause & exit /b 1
)
cd ..
echo  Done.

:: ── 7. Build Electron installer ─────────────────────────────────────────────
echo.
echo [7/7] Building Windows installer (.exe)...
cd electron
call npm run build
if errorlevel 1 (
    cd ..
    echo  ERROR: electron-builder failed. See output above.
    pause & exit /b 1
)
cd ..

echo.
echo  ============================================
echo   BUILD COMPLETE
echo  ============================================
echo.
if exist "dist\electron\Lumi DM Setup*.exe" (
    for %%f in ("dist\electron\Lumi DM Setup*.exe") do (
        echo   Installer: %%f
    )
) else (
    dir /b "dist\electron\*.exe" 2>nul
)
echo.
echo  Double-click the installer to install Lumi DM.
echo  After install, launch from the desktop shortcut.
echo.
pause
