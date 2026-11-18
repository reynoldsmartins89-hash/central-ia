# Monta (lendo o conteúdo) as bibliotecas dos agentes de curadoria que ainda não têm índice.
# Rodar de novo continua de onde parou.
$env:PYTHONIOENCODING = "utf-8"
Set-Location (Split-Path $PSScriptRoot -Parent)
$log = Join-Path $env:USERPROFILE ".central-ia\logs\curadoria_bibliotecas.log"
foreach ($agente in "tradicao_catolica", "economista_catolico", "bitcoin") {
    "=== $agente $(Get-Date)" | Tee-Object -FilePath $log -Append
    python main.py curadoria $agente 2>&1 | Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -ne 0) {
        python main.py avisar "⚠️ A biblioteca do agente $agente parou com erro (veja logs\curadoria_bibliotecas.log). Rodar de novo continua de onde parou."
    }
}
python main.py avisar "📚 Bibliotecas prontas: Tradição Católica, Economista Católico e Bitcoin agora respondem com os seus materiais."
