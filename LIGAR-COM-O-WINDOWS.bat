@echo off
chcp 65001 >nul
rem Instala o Agente Vigia para ligar junto com o Windows (atalho na pasta Inicializar).
set "DESTINO=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Central de IA - Vigia.bat"
(
echo @echo off
echo start "Central de IA - Vigia" /min "%~dp0VIGIA.bat"
) > "%DESTINO%"
echo.
echo  Pronto! O Agente Vigia vai ligar sozinho sempre que o PC iniciar.
echo  Para desfazer: apague "Central de IA - Vigia" da pasta Inicializar (Win+R, shell:startup).
echo.
pause
