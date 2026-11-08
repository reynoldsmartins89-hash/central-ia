@echo off
chcp 65001 >nul
title Central de IA
cd /d "%~dp0"
if not exist .env copy .env.example .env >nul
:inicio
python main.py
echo.
pause
cls
goto inicio
