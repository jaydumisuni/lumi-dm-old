@echo off
setlocal EnableDelayedExpansion
title Lumi DM - Android APK Builder
pushd "%~dp0"
set ROOT=%CD%
popd
cd /d "%ROOT%"

echo.
echo  Lumi DM - Android APK Builder
echo  ======================================
echo.

set ANDROID_DIR=%ROOT%\android
set DIST_DIR=%ROOT%\dist

if not exist "%DIST_DIR%" mkdir "%DIST_DIR%"

:: ── Check Java ───────────────────────────────────────────────────────────────
java -version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Java not found. Install JDK 17+ and add it to PATH.
    echo  Download: https://adoptium.net/
    echo.
    pause & exit /b 1
)

:: ── Set up Gradle wrapper if missing ─────────────────────────────────────────
set GRADLE_PROPS=%ANDROID_DIR%\gradle\wrapper\gradle-wrapper.properties
set GRADLE_JAR=%ANDROID_DIR%\gradle\wrapper\gradle-wrapper.jar
set GRADLEW=%ANDROID_DIR%\gradlew.bat

if not exist "%ANDROID_DIR%\gradle\wrapper" mkdir "%ANDROID_DIR%\gradle\wrapper"

:: Write wrapper properties (tells wrapper which Gradle version to download)
if not exist "%GRADLE_PROPS%" (
    echo  Writing gradle-wrapper.properties...
    (
        echo distributionBase=GRADLE_USER_HOME
        echo distributionPath=wrapper/dists
        echo distributionUrl=https\://services.gradle.org/distributions/gradle-8.6-bin.zip
        echo networkTimeout=10000
        echo validateDistributionUrl=true
        echo zipStoreBase=GRADLE_USER_HOME
        echo zipStorePath=wrapper/dists
    ) > "%GRADLE_PROPS%"
)

:: Download wrapper JAR if missing
if not exist "%GRADLE_JAR%" (
    echo  Downloading gradle-wrapper.jar...
    powershell -NoProfile -Command ^
        "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; " ^
        "Invoke-WebRequest -Uri 'https://github.com/gradle/gradle/raw/v8.6.0/gradle/wrapper/gradle-wrapper.jar' " ^
        "-OutFile '%GRADLE_JAR%' -UseBasicParsing" 2>nul
    if not exist "%GRADLE_JAR%" (
        echo  ERROR: Could not download gradle-wrapper.jar.
        echo  Please open the android\ folder in Android Studio and let it sync.
        echo.
        pause & exit /b 1
    )
)

:: Write gradlew.bat if missing
if not exist "%GRADLEW%" (
    echo  Writing gradlew.bat...
    (
        echo @if "%%DEBUG%%"=="" @echo off
        echo @rem ##########################################################################
        echo @rem  Gradle startup script for Windows
        echo @rem ##########################################################################
        echo setlocal
        echo set DIRNAME=%%~dp0
        echo if "%%DIRNAME%%"=="" set DIRNAME=.
        echo set APP_HOME=%%DIRNAME%%
        echo set CLASSPATH=%%APP_HOME%%\gradle\wrapper\gradle-wrapper.jar
        echo for /f "tokens=*" %%%%i in ^('java -XshowSettings:properties -version 2^>^&1 ^| findstr "java.home"'^) do set JAVA_HOME_FOUND=%%%%i
        echo java -classpath "%%CLASSPATH%%" org.gradle.wrapper.GradleWrapperMain %%*
    ) > "%GRADLEW%"
)

cd /d "%ANDROID_DIR%"

:: ── Step 1: Icons ────────────────────────────────────────────────────────────
echo [1/3] Generating icons...
cd /d "%ROOT%"
python tools\generate_icons.py >nul 2>&1
cd /d "%ANDROID_DIR%"

:: ── Step 2: Build release APK ────────────────────────────────────────────────
echo [2/3] Building APK...
echo  (First run downloads Gradle ~130MB — this may take a few minutes)
echo.
call "%GRADLEW%" assembleRelease --no-daemon --stacktrace
if errorlevel 1 (
    echo.
    echo  BUILD FAILED.
    echo  Common fixes:
    echo    1. Open android\ in Android Studio ^> File ^> Sync Project with Gradle Files
    echo    2. Make sure ANDROID_HOME or ANDROID_SDK_ROOT is set
    echo    3. Install Android SDK Build-Tools 34 via Android Studio SDK Manager
    echo.
    pause & exit /b 1
)

:: ── Step 3: Copy APK to dist\ ────────────────────────────────────────────────
echo [3/3] Copying to dist\...
set APK_SRC=%ANDROID_DIR%\app\build\outputs\apk\release\app-release.apk
set APK_DEST=%DIST_DIR%\Lumi-DM-1.0.0.apk

if exist "%APK_SRC%" (
    copy /Y "%APK_SRC%" "%APK_DEST%" >nul
    echo.
    echo  ============================================
    echo   APK READY: dist\Lumi-DM-1.0.0.apk
    echo  ============================================
    echo.
) else (
    echo  WARNING: APK not found at expected path.
    echo  Check build output above for errors.
    echo.
)

echo  INSTALL OPTIONS:
echo  ----------------
echo  USB ^(ADB^):     adb install "%APK_DEST%"
echo  File transfer: Copy APK to phone, tap to install.
echo                 Enable: Settings ^> Install unknown apps ^> your file manager
echo.
pause
