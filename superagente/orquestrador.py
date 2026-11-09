"""O superagente: planeja, delega aos especialistas e consolida o resultado.

Dois modos:
  * executar(objetivo)  — o superagente decide sozinho quais agentes usar (plano dinâmico)
  * fluxo(nome, entrada) — segue um fluxo fixo definido em fluxos/<nome>.toml
Todo resultado é salvo em resultados/<data>-<assunto>/ para você revisar e aprovar.
"""
from __future__ import annotations

import json
import re
import tomllib
import unicodedata
from datetime import datetime
from pathlib import Path

from . import carrossel, imagens, obsidian, telegram
from .agentes import RAIZ, Agente, carregar_equipe


class PlanoCancelado(Exception):
    """Você rejeitou o plano; nada foi executado."""

PASTA_FLUXOS = RAIZ / "fluxos"
PASTA_RESULTADOS = RAIZ / "resultados"
LIMITE_CONTEXTO = 24_000  # caracteres de resultados anteriores repassados a cada etapa


def _slug(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", texto).strip("-").lower()[:50] or "tarefa"


def _extrair_json(texto: str):
    """Pega o primeiro bloco JSON (lista ou objeto) de uma resposta de modelo."""
    bloco = re.search(r"```(?:json)?\s*(.*?)```", texto, re.S)
    candidato = bloco.group(1) if bloco else texto
    inicio = min((i for i in (candidato.find("["), candidato.find("{")) if i >= 0), default=-1)
    if inicio < 0:
        raise ValueError("nenhum JSON na resposta")
    return json.JSONDecoder().raw_decode(candidato[inicio:])[0]


class Superagente:
    def __init__(self, forcar_modelo: str | None = None, aprovar=None, log=print, ao_vivo: bool = False):
        """
        forcar_modelo: usa este "provedor:modelo" em todos os agentes (ex.: "eco:teste").
        aprovar: função(plano) -> bool chamada antes de executar; None = aprova sempre.
        ao_vivo: avisa no Telegram o início e o fim de cada etapa.
        """
        self.mestre, self.equipe = carregar_equipe()
        self.modelos_forcados = [forcar_modelo] if forcar_modelo else None
        self.aprovar = aprovar
        self.log = log
        self.ao_vivo = ao_vivo

    # ------------------------------------------------------------------ planejamento
    def planejar(self, objetivo: str) -> list[dict]:
        catalogo = "\n".join(f"- {n}: {a.descricao}" for n, a in self.equipe.items())
        tarefa = (
            f"Objetivo do usuário:\n{objetivo}\n\n"
            f"Agentes disponíveis:\n{catalogo}\n\n"
            "Monte o plano. Responda SOMENTE com JSON no formato:\n"
            '[{"agente": "<nome>", "tarefa": "<instrução específica>"}, ...]'
        )
        try:
            _, resposta = self.mestre.executar(tarefa, modelos=self.modelos_forcados, log=self.log)
            plano = _extrair_json(resposta)
            if isinstance(plano, dict):  # alguns modelos embrulham: {"plano": [...]}
                plano = next((v for v in plano.values() if isinstance(v, list)), [])
            plano = [{"agente": p["agente"], "tarefa": str(p.get("tarefa") or objetivo)}
                     for p in plano if isinstance(p, dict) and p.get("agente") in self.equipe]
            if plano:
                return plano
            self.log("⚠ Plano sem agentes válidos; usando o generalista.")
        except Exception as erro:  # modelo devolveu algo que não é JSON, ou nenhum respondeu
            self.log(f"⚠ Não consegui planejar ({str(erro)[:120]}); usando o generalista.")
        return [{"agente": "generalista", "tarefa": objetivo}]

    # ------------------------------------------------------------------ execução
    def _rodar_plano(self, titulo: str, entrada: str, plano: list[dict], tipo: str = "superagente") -> Path:
        self.log("\n📋 Plano:")
        for i, etapa in enumerate(plano, 1):
            self.log(f"  {i}. [{etapa['agente']}] {etapa['tarefa']}")
        if self.aprovar and not self.aprovar(plano):
            telegram.enviar("❌ Plano cancelado. Nada foi executado.")
            raise PlanoCancelado("Plano cancelado por você. Nada foi executado.")

        pasta = PASTA_RESULTADOS / f"{datetime.now():%Y-%m-%d_%H%M%S}_{_slug(titulo)}"
        pasta.mkdir(parents=True, exist_ok=True)
        registro = {"titulo": titulo, "entrada": entrada, "etapas": []}
        contexto = f"## Entrada original\n{entrada}"

        for i, etapa in enumerate(plano, 1):
            agente: Agente = self.equipe[etapa["agente"]]
            self.log(f"\n▶ Etapa {i}/{len(plano)} — {agente.nome}")
            if self.ao_vivo:
                telegram.enviar(f"▶ Etapa {i}/{len(plano)} — {agente.nome} trabalhando…")
            modelo, resposta = agente.executar(
                etapa["tarefa"], contexto[-LIMITE_CONTEXTO:], self.modelos_forcados, log=self.log)
            self.log(f"   ✔ concluída com {modelo}")
            if self.ao_vivo:
                telegram.enviar(f"✔ {agente.nome} concluiu usando {modelo}")
            arquivo = pasta / f"{i:02d}-{agente.nome}.md"
            arquivo.write_text(resposta, encoding="utf-8")
            if agente.nome == "diretor_de_arte" and not self.modelos_forcados:
                if self.ao_vivo:
                    telegram.enviar("🎨 Produzindo as imagens…")
                lista = imagens.produzir(resposta, pasta, log=self.log)
                fontes = sorted({i.get("fonte", "falhou") for i in lista})
                if self.ao_vivo and lista:
                    telegram.enviar(f"🖼 {sum('arquivo' in i for i in lista)}/{len(lista)} imagens prontas ({', '.join(fontes)})")
            if agente.nome == "designer_carrossel" and not self.modelos_forcados:
                laminas = carrossel.montar(resposta, pasta, log=self.log)
                if self.ao_vivo:
                    for lamina in laminas:
                        telegram.enviar_arquivo(lamina, f"Carrossel — {lamina.stem}")
            contexto += f"\n\n## Resultado da etapa {i} ({agente.nome})\n{resposta}"
            registro["etapas"].append({**etapa, "modelo": modelo, "arquivo": arquivo.name})

        (pasta / "registro.json").write_text(
            json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8")
        (pasta / "APROVACAO.md").write_text(
            "# Aprovação humana\n\nStatus: PENDENTE\n\n"
            "Revise os arquivos desta pasta. Nada é publicado automaticamente.\n"
            "Troque o status para APROVADO quando estiver de acordo.\n", encoding="utf-8")
        # Arquivo único com tudo, para ler no celular ou colar onde quiser
        completo = pasta / "RESULTADO-COMPLETO.md"
        completo.write_text(
            f"# {titulo}\n\n> Pedido: {entrada}\n\n" + "\n\n".join(
                f"## {i}. {e['agente']} ({e['modelo']})\n\n"
                + (pasta / e["arquivo"]).read_text(encoding="utf-8")
                for i, e in enumerate(registro["etapas"], 1)), encoding="utf-8")
        nota = obsidian.salvar_execucao(titulo, tipo, registro, pasta)

        self.log(f"\n✅ Pronto. Resultados em: {pasta}")
        if nota:
            self.log(f"📓 Nota no Obsidian: {nota}")
        ultima = registro["etapas"][-1]
        resumo = (pasta / ultima["arquivo"]).read_text(encoding="utf-8")[:1500]
        telegram.enviar(
            f"✅ Central de IA concluiu: {titulo[:200]}\n"
            f"{len(plano)} etapas · {', '.join(sorted({e['modelo'] for e in registro['etapas']}))}\n"
            f"{'📓 Salvo no Obsidian (status: pendente)' if nota else ''}\n\n"
            f"— Prévia ({ultima['agente']}) —\n{resumo}")
        telegram.enviar_arquivo(completo, "Resultado completo")
        return pasta

    def executar(self, objetivo: str) -> Path:
        """Modo superagente: decide sozinho quais especialistas usar."""
        self.log(f"🧠 Superagente planejando: {objetivo}")
        return self._rodar_plano(objetivo, objetivo, self.planejar(objetivo))

    def fluxo(self, nome: str, entrada: str) -> Path:
        """Modo fluxo fixo: segue fluxos/<nome>.toml."""
        arquivo = PASTA_FLUXOS / f"{nome}.toml"
        with open(arquivo, "rb") as f:
            dados = tomllib.load(f)
        plano = [{"agente": e["agente"], "tarefa": e["tarefa"]} for e in dados["etapas"]]
        desconhecidos = [p["agente"] for p in plano if p["agente"] not in self.equipe]
        if desconhecidos:
            raise ValueError(f"agentes inexistentes no fluxo {nome}: {desconhecidos}")
        return self._rodar_plano(f"{nome}: {entrada}", entrada, plano, tipo=nome)
