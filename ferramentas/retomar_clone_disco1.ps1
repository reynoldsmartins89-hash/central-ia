# Para o clone do disco 1 em andamento (se houver) e retoma com a versão atual da ferramenta,
# aproveitando o que já foi copiado (mapa .mapa.json). Rodar como Administrador.
# Copia só o espaço ocupado por arquivos (lista em DISCO1-areas.json, tirada do $Bitmap do NTFS).
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
Start-Transcript -Path "E:\Resgate-HD\retomar-$stamp.txt" | Out-Null
try {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match 'clonar 1' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Start-Sleep 3
    Set-Location (Split-Path $PSScriptRoot)
    $python = (Get-Command python -ErrorAction SilentlyContinue).Source
    $real = Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.14-64\python.exe'
    if (Test-Path $real) { $python = $real }
    "Python: $python | pasta: $(Get-Location)"
    $env:PYTHONIOENCODING = 'utf-8'
    $areas = 'E:\Resgate-HD\DISCO1-areas.json'
    $extra = if (Test-Path $areas) { "--areas `"$areas`"" } else { '' }
    cmd /c "`"$python`" -u ferramentas\resgate_hd.py clonar 1 E:\Resgate-HD\DISCO1.img $extra >> E:\Resgate-HD\DISCO1-clone-utf8.log 2>&1"
    "Python terminou com código $LASTEXITCODE"
} catch {
    "ERRO: $_"
} finally {
    Stop-Transcript | Out-Null
}
