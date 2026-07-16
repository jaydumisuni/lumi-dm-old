@echo off
:: Double-click this on Windows to build the Windows installer (.exe)
cd /d "%~dp0"
call scripts\build_installer.bat
