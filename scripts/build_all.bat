@echo off
:: Lumi DM — Build all Windows/desktop artifacts
:: Run from project root: scripts\build_all.bat

setlocal

set ROOT=%~dp0..
echo.
echo  Lumi DM — Full Build (Windows)
echo  ═══════════════════════════════════════
echo.

:: ── Step 1: Regenerate icons ──
echo [1/4] Generating icons from Resouces\my_logo.png ...
python "%ROOT%\tools\generate_icons.py"
if errorlevel 1 echo WARNING: Icon generation failed, using existing icons.

:: ── Step 2: Copy background image ──
echo [2/4] Copying assets ...
copy /Y "%ROOT%\Resouces\background.png" "%ROOT%\static\background.png" >nul

:: ── Step 3: Install Python deps ──
echo [3/4] Installing Python dependencies ...
pip install -r "%ROOT%\requirements.txt"

:: ── Step 4: Build Windows EXE ──
echo [4/4] Building Windows EXE with PyInstaller ...
call "%ROOT%\scripts\build_server.ps1" -Name Lumi-DM

echo.
echo  Build complete!
echo  ─────────────────────────────────────────
echo   Windows EXE:  dist\Lumi-DM.exe
echo   Browser ext:  browser-extension\  (load unpacked in chrome://extensions)
echo   Electron app: cd electron ^&^& npm install ^&^& npm run build
echo   Android APK:  run scripts\build_android.bat
echo   iOS IPA:      run scripts\build_ios.sh  (macOS only)
echo.

endlocal
pause
