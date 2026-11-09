@echo off
chcp 65001 >nul
title Central de IA - Agente Leitor
cd /d "%~dp0"
set PYTHONUNBUFFERED=1
set HF_HUB_DISABLE_SYMLINKS_WARNING=1
set PYTHONWARNINGS=ignore
set ALVO=%1
if "%ALVO%"=="" set ALVO=E:\r
echo Lendo %ALVO% (documentos, imagens, PDFs escaneados, audio e video).
echo Pode fechar esta janela a qualquer momento: a leitura continua de onde parou na proxima vez.
python main.py ler "%ALVO%"
pause
