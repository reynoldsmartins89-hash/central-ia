# Para o clone antigo do disco 1 (se houver) e recomeça com a versão corrigida. Rodar como Administrador.
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'clonar 1' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Sleep 3
Remove-Item 'E:\Resgate-HD\DISCO1.img', 'E:\Resgate-HD\DISCO1.img.mapa.json' -Force -ErrorAction SilentlyContinue
Set-Location (Split-Path $PSScriptRoot)
python -u ferramentas\resgate_hd.py clonar 1 'E:\Resgate-HD\DISCO1.img' *> 'E:\Resgate-HD\DISCO1-clone.log'
