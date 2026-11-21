@echo off
rem Liga o resgate AUTOMATICO do HD de 1 TB: clone, extracao dos arquivos e antivirus, sem mais cliques.
rem Cria a tarefa do Windows "Resgate-HD DISCO1" (roda sozinha, volta quando o PC liga). Pede UM "Sim".
powershell -NoProfile -Command "Start-Process powershell -Verb RunAs -WindowStyle Hidden -Wait -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','%~dp0ferramentas\instalar_tarefa_clone_disco1.ps1'"
if exist E:\Resgate-HD\tarefa-instalada.txt (echo Pronto: o resgate agora roda sozinho. Pode fechar esta janela.) else (echo Nao deu certo: a permissao foi recusada? Rode de novo e clique em Sim.)
pause
