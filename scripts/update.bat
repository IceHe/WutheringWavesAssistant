@echo off
setlocal
title WWA Updater
set "WWA_UPDATE_SCRIPT=%~dp0update.ps1"
if not exist "%WWA_UPDATE_SCRIPT%" set "WWA_UPDATE_SCRIPT=%~dp0..\..\scripts\update.ps1"
set "WWA_UPDATE_CHANNEL=%~1"
if not defined WWA_UPDATE_CHANNEL set "WWA_UPDATE_CHANNEL=0"
set "WWA_UPDATE_OPTIONS="
if /i "%~2"=="--no-restart" set "WWA_UPDATE_OPTIONS=-NoRestart"

powershell.exe -NoLogo -NoProfile -File "%WWA_UPDATE_SCRIPT%" -Channel "%WWA_UPDATE_CHANNEL%" %WWA_UPDATE_OPTIONS%
set "WWA_UPDATE_RESULT=%errorlevel%"
if not "%WWA_UPDATE_RESULT%"=="0" pause
exit /b %WWA_UPDATE_RESULT%
