# Cria (ou atualiza) a tarefa do Windows "Resgate-HD DISCO1" e já a inicia. Rodar como Administrador
# (é o único "Sim" necessário). A tarefa roda como SYSTEM, fora de qualquer aplicativo: não cai se o
# app Claude atualizar ou fechar, volta sozinha quando o PC liga e é reativada a cada 30 min se parar.
# Ela executa resgate_automatico_disco1.ps1: clone → extração dos arquivos → antivírus.
$script = Join-Path $PSScriptRoot 'resgate_automatico_disco1.ps1'
$acao = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$aoLigar = New-ScheduledTaskTrigger -AtStartup
$aoLigar.Delay = 'PT2M'  # espera 2 min após ligar, para os HDs USB aparecerem
$repetir = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Days 3650)
$conta = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$opcoes = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -StartWhenAvailable
Register-ScheduledTask -TaskName 'Resgate-HD DISCO1' -Action $acao -Trigger @($aoLigar, $repetir) `
    -Principal $conta -Settings $opcoes -Force | Out-Null
Start-ScheduledTask -TaskName 'Resgate-HD DISCO1'
"Tarefa criada e iniciada em $(Get-Date)." | Out-File -Encoding utf8 'E:\Resgate-HD\tarefa-instalada.txt'
