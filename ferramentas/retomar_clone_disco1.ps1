# Para o clone do DISCO1 em andamento (se houver) e retoma com a versão atual da ferramenta,
# aproveitando o que já foi copiado (mapa .mapa.json). Rodar como Administrador.
# Copia só o espaço ocupado por arquivos (lista em DISCO1-areas.json, tirada do $Bitmap do NTFS).
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
Start-Transcript -Path "E:\Resgate-HD\retomar-$stamp.txt" | Out-Null
try {
    # O número do disco muda conforme a ordem em que os HDs são ligados: acha o DISCO1 pelo tamanho
    # exato gravado no mapa (1.000.204.884.992 bytes, como a caixinha USB informa).
    $tamanho = 1000204884992
    $disco = Get-Disk | Where-Object { $_.Size -eq $tamanho -and $_.BusType -eq 'USB' } | Select-Object -First 1
    if (-not $disco) {
        "O HD de 1 TB do clone (DISCO1) não está conectado. Ligue-o (na mesma caixinha USB) e rode de novo."
        return
    }
    $n = $disco.Number
    "DISCO1 encontrado: disco $n ($($disco.FriendlyName))"
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match 'DISCO1\.img' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Start-Sleep 3
    Set-Location (Split-Path $PSScriptRoot)
    $python = (Get-Command python -ErrorAction SilentlyContinue).Source
    # Rodando como tarefa do Windows (conta SYSTEM) o LOCALAPPDATA é outro: procura também na pasta do usuário.
    $usuario = Split-Path (Split-Path (Split-Path $PSScriptRoot))
    foreach ($real in @((Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.14-64\python.exe'),
                        (Join-Path $usuario 'AppData\Local\Python\pythoncore-3.14-64\python.exe'))) {
        if (Test-Path $real) { $python = $real; break }
    }
    "Python: $python | pasta: $(Get-Location)"
    $env:PYTHONIOENCODING = 'utf-8'
    $areas = 'E:\Resgate-HD\DISCO1-areas.json'
    $extra = if (Test-Path $areas) { "--areas `"$areas`"" } else { '' }
    cmd /c "`"$python`" -u ferramentas\resgate_hd.py clonar $n E:\Resgate-HD\DISCO1.img $extra >> E:\Resgate-HD\DISCO1-clone-utf8.log 2>&1"
    "Python terminou com código $LASTEXITCODE"
} catch {
    "ERRO: $_"
} finally {
    Stop-Transcript | Out-Null
}
