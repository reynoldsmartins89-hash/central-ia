@echo off
rem Abre um PowerShell como Administrador ja na pasta da Central, pronto para o resgate de HD.
powershell -NoProfile -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoExit','-Command','cd ''%~dp0''; python ferramentas\resgate_hd.py listar; Write-Host; Write-Host ''Proximo: python ferramentas\resgate_hd.py diagnosticar N'''"
