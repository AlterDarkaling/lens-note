@echo off
chcp 65001 >nul
title lens-note - Photography Data Retrospective
setlocal enabledelayedexpansion

where python >nul 2>nul
if errorlevel 1 (
    where py >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] Python 3 not found in PATH. Please install Python 3.9+ first.
        pause
        exit /b 1
    )
    set "PY_CMD=py -3"
) else (
    set "PY_CMD=python"
)

if "%~1"=="" (
    %PY_CMD% "%~dp0lens_note.py"
) else (
    %PY_CMD% "%~dp0lens_note.py" %*
)
if errorlevel 1 pause
