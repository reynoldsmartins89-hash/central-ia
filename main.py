"""Central de IA — linha de comando do superagente.

Jeito mais fácil: dê dois cliques em INICIAR.bat (abre um menu).

Exemplos para o terminal:
    python main.py                  (menu)
    python main.py diagnostico
    python main.py executar "Crie um plano para lançar uma newsletter sobre IA aplicada"
    python main.py fluxo producao-editorial "IA nas pequenas empresas"
Use --modelo eco:teste para testar tudo offline, sem chamar nenhuma IA.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from superagente import habilidades, provedores, telegram
from superagente.agentes import RAIZ, carregar_env, carregar_equipe
from superagente.orquestrador import PASTA_FLUXOS, PASTA_RESULTADOS, PlanoCancelado, Superagente, formatar_plano

for fluxo_saida in (sys.stdout, sys.stderr):
    try:
        fluxo_saida.reconfigure(encoding="utf-8")  # acentos e emojis no PowerShell
    except AttributeError:
        pass

ARQUIVO_ENV = RAIZ / ".env"


def texto_plano(plano) -> str:
    return formatar_plano(plano)


def confirmar_plano(plano) -> bool:
    if telegram.configurado():
        return telegram.pedir_aprovacao(texto_plano(plano))
    resposta = input("\nExecutar este plano? [S/n] ").strip().lower()
    return resposta in ("", "s", "sim", "y")


def salvar_no_env(chave: str, valor: str) -> None:
    """Grava/atualiza CHAVE=valor no .env."""
    linhas = ARQUIVO_ENV.read_text(encoding="utf-8").splitlines() if ARQUIVO_ENV.exists() else []
    linhas = [l for l in linhas if not l.startswith(f"{chave}=")] + [f"{chave}={valor}"]
    ARQUIVO_ENV.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    os.environ[chave] = valor


def configurar_telegram() -> int:
    if not os.getenv("TELEGRAM_BOT_TOKEN", "").strip():
        print("Falta o TELEGRAM_BOT_TOKEN no arquivo .env (veja o README, seção Telegram).")
        return 1
    print("Abra o Telegram, procure o seu bot e envie a ele qualquer mensagem (ex.: oi).")
    input("Depois de enviar, aperte ENTER aqui… ")
    chat_id = telegram.descobrir_chat_id()
    if not chat_id:
        print("Não encontrei nenhuma mensagem. Envie 'oi' ao bot e tente de novo.")
        return 1
    salvar_no_env("TELEGRAM_CHAT_ID", chat_id)
    telegram.enviar("🤖 Central de IA conectada! Você receberá aqui os planos para aprovar.")
    print("✅ Telegram configurado. Confira a mensagem no seu celular.")
    return 0


def menu() -> list[str]:
    """Menu simples para quem não quer digitar comandos."""
    print("\n=== 🧠 CENTRAL DE IA ===")
    print("1) Pedir algo ao superagente (ele escolhe os agentes)")
    print("2) Produzir conteúdo completo a partir de um tema")
    print("3) Conversar com um agente específico")
    print("4) Ver o que está funcionando (diagnóstico)")
    print("5) Abrir a pasta de resultados")
    print("6) Conectar o Telegram")
    print("7) 🤖 Ligar o bot do Telegram (comandar tudo pelo celular)")
    print("0) Sair")
    opcao = input("\nEscolha: ").strip()
    if opcao == "1":
        return ["executar", input("O que você quer? ").strip()]
    if opcao == "2":
        return ["fluxo", "producao-editorial", input("Tema: ").strip()]
    if opcao == "3":
        _, equipe = carregar_equipe()
        nomes = list(equipe)
        for i, n in enumerate(nomes, 1):
            print(f"  {i}) {n} — {equipe[n].descricao}")
        escolhido = nomes[int(input("Número do agente: ")) - 1]
        return ["perguntar", escolhido, input("Sua pergunta: ").strip()]
    if opcao == "4":
        return ["diagnostico"]
    if opcao == "5":
        PASTA_RESULTADOS.mkdir(exist_ok=True)
        os.startfile(PASTA_RESULTADOS)  # type: ignore[attr-defined]
        return ["sair"]
    if opcao == "6":
        return ["telegram"]
    if opcao == "7":
        return ["bot"]
    return ["sair"]


def main(argv: list[str] | None = None) -> int:
    carregar_env()
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        argv = menu()
        if argv == ["sair"]:
            return 0

    p = argparse.ArgumentParser(prog="central-ia", description="Superagente e equipe de agentes de IA")
    p.add_argument("--modelo", help='força um modelo para todos, ex.: "eco:teste" ou "ollama:qwen3:4b"')
    p.add_argument("-s", "--sim", action="store_true", help="não pede confirmação do plano")
    sub = p.add_subparsers(dest="comando", required=True)

    sub.add_parser("diagnostico", help="mostra quais provedores de IA estão prontos")
    sub.add_parser("agentes", help="lista a equipe de agentes")
    sub.add_parser("habilidades", help="lista as habilidades (SKILL.md)")
    sub.add_parser("fluxos", help="lista os fluxos fixos")
    sub.add_parser("telegram", help="conecta o seu Telegram (descobre o chat id)")
    sub.add_parser("bot", help="liga o bot: comande a Central pelo Telegram")
    c_idx = sub.add_parser("indexar", help="indexa a biblioteca de um agente de conhecimento (ou todos)")
    c_idx.add_argument("agente", nargs="?", default="todos")
    c_ler = sub.add_parser("ler", help="Agente Leitor: transcreve/lê vídeos, áudios, imagens e documentos para .md")
    c_ler.add_argument("alvo", nargs="?", default="", help="disco/pasta catalogado, ou 'progresso'")
    c_ler.add_argument("--tipos", nargs="*", choices=["documento", "imagem", "audio/video"], default=None)
    c_ler.add_argument("--minutos", type=float, default=None, help="duração máxima desta rodada")
    c_ler.add_argument("--limite", type=int, default=None, help="máximo de arquivos nesta rodada")
    c_drv = sub.add_parser("drive", help="Agente Drive: catálogo, duplicados e leitura pela API, sem baixar o Drive")
    c_drv.add_argument("acao", choices=["conectar", "catalogar", "duplicados", "separar", "ler"])
    c_drv.add_argument("pasta", nargs="?", default="", help="em 'separar': só esta pasta do Drive (ex.: EBOOKS)")
    c_drv.add_argument("--minutos", type=float, default=None)
    c_drv.add_argument("--limite", type=int, default=None)
    c_fot = sub.add_parser("fotos", help="envia fotos/vídeos bons do HD para o Google Fotos")
    c_fot.add_argument("acao", choices=["conectar", "enviar", "recuperadas"])
    c_fot.add_argument("pasta", nargs="?", default="")
    c_dup = sub.add_parser("duplicados", help="encontra cópias idênticas e as separa (com seu consentimento)")
    c_dup.add_argument("acao", choices=["procurar", "relatorio", "separar", "conferir", "apagar"])
    c_dup.add_argument("alvo", nargs="*", default=[], help="discos/pastas (padrão: todos os catalogados)")
    c_dup.add_argument("--quantidade", type=int, default=100)
    c_rep = sub.add_parser("reparar", help="Agente Reparador: recupera arquivos danificados (sempre numa cópia)")
    c_rep.add_argument("acao", choices=["executar", "relatorio", "separar-perdidos"])
    c_rep.add_argument("alvo", nargs="?", default="")
    c_rep.add_argument("--limite", type=int, default=None)
    sub.add_parser("vigia", help="Agente Vigia: mantém bot e leitura ligados, religa o que cair e avisa no Telegram")
    c_int = sub.add_parser("integridade", help="encontra arquivos corrompidos ou incompletos (só lê)")
    c_int.add_argument("acao", choices=["verificar", "relatorio"])
    c_int.add_argument("alvo", nargs="?", default="")
    c_cur = sub.add_parser("curador", help="cataloga, classifica e reorganiza arquivos (com seu consentimento)")
    c_cur.add_argument("acao", choices=["catalogar", "colecoes", "resumo", "buscar", "classificar",
                                         "propostas", "aplicar", "desfazer"])
    c_cur.add_argument("alvo", nargs="?", default="", help="pasta/disco (ou o termo, em 'buscar')")
    c_cur.add_argument("--quantidade", type=int, default=10, help="tamanho do lote (classificar/aplicar)")

    c = sub.add_parser("perguntar", help="fala direto com um agente")
    c.add_argument("agente")
    c.add_argument("texto")

    c = sub.add_parser("executar", help="o superagente planeja e delega sozinho")
    c.add_argument("objetivo")

    c = sub.add_parser("fluxo", help="executa um fluxo fixo de fluxos/")
    c.add_argument("nome")
    c.add_argument("entrada")

    a = p.parse_args(argv)

    if a.comando == "diagnostico":
        for nome, ok, detalhe in provedores.diagnostico():
            print(f"{'✅' if ok else '⬜'} {nome:<11} {detalhe}")
        print(f"{'✅' if telegram.configurado() else '⬜'} {'telegram':<11} "
              f"{'conectado' if telegram.configurado() else 'não configurado'}")
        return 0

    if a.comando == "telegram":
        return configurar_telegram()

    if a.comando == "vigia":
        from superagente import vigia
        vigia.rodar()
        return 0

    if a.comando == "reparar":
        from superagente import reparador
        if a.acao == "executar":
            if not a.alvo:
                print('Informe o disco, ex.: reparar executar "E:\\"')
                return 1
            reparador.reparar(a.alvo, a.limite)
        elif a.acao == "separar-perdidos":
            texto = (f"⛏ Garimpar e depois mover os arquivos ILEGÍVEIS de {a.alvo} para "
                     f"'{reparador.PASTA_PARA_APAGAR}' (nada é apagado; dá para desfazer)?")
            if not a.alvo or not (a.sim or (telegram.pedir_aprovacao(texto) if telegram.configurado() else
                                            input(texto + " [s/N] ").strip().lower() in ("s", "sim"))):
                print("Nada foi movido.")
                return 0
            reparador.separar_perdidos(a.alvo)
        print(reparador.relatorio(a.alvo or None))
        return 0

    if a.comando in ("ler", "drive") and (a.comando == "drive" and a.acao == "ler" or
                                          a.comando == "ler" and a.alvo != "progresso"):
        from superagente.vigia import travar
        if not travar("leitura"):
            print("📚 Uma leitura já está em andamento (o Vigia cuida dela). Acompanhe com: ler progresso")
            return 0

    if a.comando == "ler":
        from superagente import leitor
        if a.alvo != "progresso":
            leitor.processar(a.alvo or None, a.tipos, a.limite, a.minutos)
        print(leitor.progresso())
        return 0

    if a.comando == "drive":
        from superagente import drive
        if a.acao == "conectar":
            if not drive.configurado():
                print("Vai abrir o navegador: entre na sua conta Google e clique em PERMITIR (somente leitura).")
                import subprocess
                subprocess.run(drive.comando_conexao(), stdout=subprocess.DEVNULL)  # não exibe a chave de acesso
            print(drive.status())
        elif a.acao == "catalogar":
            drive.catalogar()
        elif a.acao == "duplicados":
            print(drive.duplicados())
        elif a.acao == "separar":
            prefixo = a.pasta.strip("/\\") + "/" if a.pasta else ""
            texto = (f"🗂 Mover as cópias repetidas de '{prefixo or 'todo o Drive'}' para "
                     f"'{drive.PASTA_REPETIDOS}' no próprio Drive (nada é apagado; dá para desfazer)?")
            if a.sim or (telegram.pedir_aprovacao(texto) if telegram.configurado() else
                         input(texto + " [s/N] ").strip().lower() in ("s", "sim")):
                drive.separar_duplicados(prefixo)
            else:
                print("Nada foi movido.")
        elif a.acao == "ler":
            drive.ler_tudo(a.limite, a.minutos)
        return 0

    if a.comando == "fotos":
        from superagente import fotos
        if a.acao == "conectar":
            if not fotos.configurado():
                print("Vai abrir o navegador: entre na sua conta Google e clique em PERMITIR (Google Fotos).")
                import subprocess
                subprocess.run(fotos.comando_conexao(), stdout=subprocess.DEVNULL)  # não exibe a chave de acesso
            print("✅ Google Fotos conectado." if fotos.configurado() else "⚠ Não conectou; tente de novo.")
        elif a.acao == "recuperadas":
            fotos.enviar_recuperadas(tamanho=a.pasta or "foto")
        elif not a.pasta:
            print('Informe a pasta, ex.: fotos enviar "E:/Fotos"')
            return 1
        else:
            fotos.enviar(a.pasta)
        return 0

    if a.comando == "duplicados":
        from superagente import duplicados
        if a.acao == "procurar":
            duplicados.encontrar(a.alvo or None)
        elif a.acao == "relatorio":
            print(duplicados.relatorio())
        elif a.acao in ("conferir", "apagar"):
            if len(a.alvo) != 1:
                print('Informe UM disco, ex.: duplicados conferir "E:\\"')
                return 1
            if a.acao == "apagar":
                texto = (f"🗑 APAGAR as cópias de '{duplicados.PASTA_QUARENTENA}' em {a.alvo[0]}? Cada cópia e o seu "
                         "original serão relidos por inteiro antes; só sai o que for idêntico byte a byte.")
                if not (a.sim or (telegram.pedir_aprovacao(texto) if telegram.configurado() else
                                  input(texto + " [s/N] ").strip().lower() in ("s", "sim"))):
                    print("Nada foi apagado.")
                    return 0
            duplicados.conferir(a.alvo[0], apagar=a.acao == "apagar")
        elif a.acao == "separar":
            if len(a.alvo) != 1:
                print('Informe UM disco, ex.: duplicados separar "E:\\"')
                return 1
            texto = (f"🗂 Separar até {a.quantidade} cópias duplicadas de {a.alvo[0]} para a pasta "
                     f"'{duplicados.PASTA_QUARENTENA}' (nada é apagado; os originais ficam onde estão)?\n\n"
                     + duplicados.relatorio(5))
            ok = a.sim or telegram.pedir_aprovacao(texto) if telegram.configurado() else a.sim or \
                input(f"{texto}\n\nSeparar? [s/N] ").strip().lower() in ("s", "sim")
            if ok:
                duplicados.separar(a.alvo[0], a.quantidade)
            else:
                print("Nada foi movido.")
        return 0

    if a.comando == "integridade":
        from superagente import integridade
        if a.acao == "verificar":
            if not a.alvo:
                print('Informe o disco, ex.: integridade verificar "E:\\"')
                return 1
            integridade.varrer(a.alvo)
        print(integridade.relatorio(a.alvo or None))
        return 0

    if a.comando == "curador":
        from superagente import curador
        alvo = a.alvo or os.getenv("CURADOR_RAIZ", "")
        if a.acao == "resumo":
            print(curador.resumo(alvo or None))
        elif a.acao == "buscar":
            for caminho, tamanho in curador.buscar(a.alvo):
                print(f"{tamanho / 1e6:>9.1f} MB  {caminho}")
        elif not alvo:
            print("Informe a pasta ou disco, ex.: curador catalogar \"G:\\Meu Drive\"")
            return 1
        elif a.acao == "catalogar":
            curador.catalogar(alvo)
            curador.identificar_colecoes(alvo)
        elif a.acao == "colecoes":
            curador.identificar_colecoes(alvo)
            print(curador.resumo_colecoes(alvo))
        elif a.acao == "classificar":
            print(f"{curador.classificar(alvo, limite=a.quantidade)} coleções classificadas")
            print(curador.resumo_colecoes(alvo))
        elif a.acao == "propostas":
            print(f"{curador.propor(alvo)} pastas propostas para mover (nada foi movido). Exemplos:")
            for de, para, tema in curador.listar_propostas(alvo):
                print(f"• [{tema}]\n   de:   {de}\n   para: {para}")
        elif a.acao == "aplicar":
            exemplos = curador.listar_propostas(alvo, a.quantidade)
            if not exemplos:
                print("Nenhuma proposta. Rode antes: classificar e propostas.")
                return 0
            texto = f"📦 Curador quer mover {len(exemplos)} PASTAS inteiras em {alvo}:\n" + "\n".join(
                f"• {Path(de).name} → {tema}" for de, _, tema in exemplos[:15])
            ok = a.sim or telegram.pedir_aprovacao(texto) if telegram.configurado() else a.sim or \
                input(f"{texto}\n\nMover? [s/N] ").strip().lower() in ("s", "sim")
            if ok:
                curador.aplicar(alvo, a.quantidade)
            else:
                print("Nada foi movido.")
        elif a.acao == "desfazer":
            curador.desfazer()
        return 0

    if a.comando == "indexar":
        from superagente import conhecimento
        _, equipe = carregar_equipe()
        alvos = [ag for ag in equipe.values() if ag.base and a.agente in ("todos", ag.nome)]
        if not alvos:
            print("Nenhum agente de conhecimento com esse nome.")
            return 1
        for ag in alvos:
            try:
                conhecimento.indexar(ag.nome, ag.base)
            except FileNotFoundError as erro:
                print(f"⚠ {ag.nome}: {erro}")
        return 0

    if a.comando == "bot":
        from superagente import bot
        bot.rodar()
        return 0

    if a.comando == "agentes":
        mestre, equipe = carregar_equipe()
        print(f"🧠 superagente  → {mestre.habilidade}  ({mestre.modelos[0]})")
        for nome, ag in equipe.items():
            print(f"   • {nome:<13} {ag.descricao}")
        return 0

    if a.comando == "habilidades":
        for h in habilidades.listar():
            print(f"• {h.caminho:<45} {h.descricao[:70]}")
        return 0

    if a.comando == "fluxos":
        for f in sorted(PASTA_FLUXOS.glob("*.toml")):
            print(f"• {f.stem}")
        return 0

    if a.comando == "perguntar":
        _, equipe = carregar_equipe()
        if a.agente not in equipe:
            print(f"Agente '{a.agente}' não existe. Use: python main.py agentes")
            return 1
        modelo, resposta = equipe[a.agente].executar(a.texto, modelos=[a.modelo] if a.modelo else None)
        print(f"[{modelo}]\n\n{resposta}")
        return 0

    aprovar = None if a.sim else confirmar_plano
    sa = Superagente(forcar_modelo=a.modelo, aprovar=aprovar)
    if a.comando == "executar":
        sa.executar(a.objetivo)
    elif a.comando == "fluxo":
        sa.fluxo(a.nome, a.entrada)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PlanoCancelado as aviso:
        print(f"\n{aviso}")
        sys.exit(0)
    except provedores.ErroProvedor as erro:
        print(f"\n❌ {erro}\nDica: escolha 'diagnóstico' no menu e confira o arquivo .env")
        sys.exit(1)
