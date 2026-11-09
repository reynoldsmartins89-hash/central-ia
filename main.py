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
            ok = telegram.pedir_aprovacao(texto) if telegram.configurado() else \
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
