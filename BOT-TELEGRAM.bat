@echo off
chcp 65001 >nul
title Central de IA - Bot do Telegram
cd /d "%~dp0"
:loop
python main.py bot
echo Bot parou. Reiniciando em 10 segundos (feche a janela para parar)...
timeout /t 10 >nul
goto loop
