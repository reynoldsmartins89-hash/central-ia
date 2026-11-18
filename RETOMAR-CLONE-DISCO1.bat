@echo off
rem Retoma o clone do HD de 1 TB (disco 1) com a versao que pula areas ruins. Pede permissao de Administrador.
powershell -NoProfile -Command "Start-Process powershell -Verb RunAs -WindowStyle Hidden -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','%~dp0ferramentas\retomar_clone_disco1.ps1'"
echo Clone retomado em segundo plano. Pode fechar esta janela.
pause
