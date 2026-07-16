@echo off
:: Lumi DM — Android APK build script (Windows)
:: Requirements: Android Studio + Android SDK installed

setlocal

set ROOT=%~dp0..
set ANDROID_DIR=%ROOT%\android

echo.
echo  Lumi DM - Android APK Builder
echo  --------------------------------------
echo  Android project: %ANDROID_DIR%
echo.

:: Regenerate icons from my_logo.png
echo [1/3] Regenerating icons from Resouces\my_logo.png ...
python "%ROOT%\tools\generate_icons.py"
if errorlevel 1 (
    echo WARNING: Icon generation failed. Using existing icons.
)

:: Copy background to static
echo [2/3] Copying background image ...
copy /Y "%ROOT%\Resouces\background.png" "%ROOT%\static\background.png" >nul 2>&1

:: Build APK
echo [3/3] Building APK with Gradle ...
cd /d "%ANDROID_DIR%"

:: Check for gradlew.bat (needs gradle-wrapper.jar — download from Android Studio first)
if exist "gradlew.bat" (
    call gradlew.bat assembleRelease
    if errorlevel 1 (
        echo Build failed. Try: gradlew.bat assembleDebug
        call gradlew.bat assembleDebug
    )
    echo.
    echo  APK location:
    echo   Release: android\app\build\outputs\apk\release\app-release-unsigned.apk
    echo   Debug:   android\app\build\outputs\apk\debug\app-debug.apk
    echo.
    echo  To install on connected device:
    echo   adb install app\build\outputs\apk\debug\app-debug.apk
) else (
    echo.
    echo  Gradle wrapper not found. To build the APK:
    echo.
    echo  Option 1 — Android Studio (recommended):
    echo    1. Open Android Studio
    echo    2. File ^> Open ^> select the android\ folder
    echo    3. Build ^> Build Bundle(s) / APK(s) ^> Build APK(s)
    echo.
    echo  Option 2 — Command line (after Android Studio syncs once):
    echo    cd android
    echo    gradlew.bat assembleDebug
    echo.
)

endlocal
pause
