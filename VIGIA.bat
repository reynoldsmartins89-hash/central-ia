@echo off
chcp 65001 >nul
title Central de IA - Vigia (bot + leitura + reparos)
cd /d "%~dp0"
python main.py vigia
