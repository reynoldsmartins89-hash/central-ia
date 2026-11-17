@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Gerenciador de E-mails (nada e apagado) ===
python main.py emails tudo
echo.
echo Confira o painel que abriu no navegador. Para mover o que foi indicado:
python main.py emails aplicar
pause
