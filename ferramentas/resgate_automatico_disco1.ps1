# Resgate automático do HD de 1 TB (DISCO1), do começo ao fim, sem ninguém clicar.
# Roda como tarefa do Windows (conta SYSTEM): "Resgate-HD DISCO1" (ver instalar_tarefa_clone_disco1.ps1).
#  1. mantém o clone (resgate_hd.py) rodando; se cair, reinicia de onde parou;
#  2. a cada ciclo, extrai para E:\Resgate-HD\DISCO1 os arquivos que já estão inteiros na imagem;
#  3. quando o clone termina: extração final, antivírus (Defender) e aviso em DISCO1-ESTADO.txt.
# Nunca escreve no HD com defeito.
$pasta   = 'E:\Resgate-HD'
$tamanho = 1000204884992
$ciclo   = 15 * 60
$repo    = Split-Path $PSScriptRoot
$estado  = "$pasta\DISCO1-ESTADO.txt"
$mapa    = "$pasta\DISCO1.img.mapa.json"
$saida   = "$pasta\DISCO1"

if (-not (Test-Path $pasta)) { return }  # HD de 8 TB desligado: nada a fazer
function Anotar($msg) { "$(Get-Date -Format 'dd/MM/yyyy HH:mm')  $msg" | Out-File -Append -Encoding utf8 $estado }

$usuario = Split-Path (Split-Path $repo)
$python = $null
foreach ($p in @((Join-Path $usuario 'AppData\Local\Python\pythoncore-3.14-64\python.exe'),
                 (Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.14-64\python.exe'))) {
    if (Test-Path $p) { $python = $p; break }
}
if (-not $python) { $python = (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $python) { Anotar 'ERRO: Python não encontrado.'; return }
$env:PYTHONIOENCODING = 'utf-8'
Set-Location $repo

function CloneTerminou {
    if (-not (Test-Path $mapa)) { return $false }
    $m = Get-Content $mapa -Raw | ConvertFrom-Json
    $pend = @($m.blocos_ruins | Where-Object { $m.passada3_feita -notcontains $_ })
    return ($m.posicao -ge $tamanho) -and (@($m.pulados).Count -eq 0) -and
           (@($m.pulados_prox).Count -eq 0) -and ($pend.Count -eq 0)
}
function ProcessoClone {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match 'DISCO1\.img' -and $_.CommandLine -match 'clonar' }
}
function Extrair {
    if (Test-Path "$repo\ferramentas\extrair_ntfs.py") {
        cmd /c "`"$python`" -u ferramentas\extrair_ntfs.py `"$pasta\DISCO1.img`" `"$saida`" >> `"$pasta\DISCO1-extracao.log`" 2>&1"
    }
}

Anotar 'Resgate automático iniciado.'
# A versão em execução pode ser antiga: reinicia o clone com a atual (retoma do mapa, nada se perde).
ProcessoClone | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 5

while (-not (CloneTerminou)) {
    if (-not (ProcessoClone)) {
        $disco = Get-Disk | Where-Object { $_.Size -eq $tamanho -and $_.BusType -eq 'USB' } | Select-Object -First 1
        if ($disco) {
            Start-Process powershell.exe -WindowStyle Hidden -ArgumentList '-NoProfile', '-ExecutionPolicy',
                'Bypass', '-File', "`"$PSScriptRoot\retomar_clone_disco1.ps1`""
            Anotar "Clone (re)iniciado no disco $($disco.Number)."
        } else {
            Anotar 'HD de 1 TB não aparece no Windows (desconectado ou sem responder). Tento de novo em 15 min.'
        }
    }
    Start-Sleep 60
    Extrair
    Start-Sleep $ciclo
}

Anotar 'Clone terminado. Extração final dos arquivos…'
Extrair
$defender = Join-Path $env:ProgramFiles 'Windows Defender\MpCmdRun.exe'
if ((Test-Path $defender) -and (Test-Path $saida)) {
    Anotar 'Verificando os arquivos recuperados com o antivírus (Defender)…'
    & $defender -Scan -ScanType 3 -File $saida | Out-File -Encoding utf8 "$pasta\DISCO1-antivirus.txt"
}
Anotar "CONCLUÍDO. Arquivos recuperados em $saida (relatório em DISCO1-extracao.log)."
