"""Agentes especialistas: uma habilidade + uma lista de modelos com reserva."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import habilidades, provedores

RAIZ = Path(__file__).resolve().parent.parent
ARQUIVO_AGENTES = RAIZ / "agentes" / "agentes.toml"

REGRAS_GERAIS = (
    "Responda em português do Brasil, em Markdown. "
    "Nunca invente fatos, números, citações ou fontes; sinalize incertezas. "
    "Você faz parte de uma equipe: entregue apenas a sua parte, completa e pronta para a próxima etapa. "
    "IMPORTANTE: a Central de IA TEM um Agente Executor que faz ações reais no PC do usuário (arquivos, "
    "pastas, programas, scripts, HDs e Drive) quando ele manda a ordem pelo Telegram (/pc <ordem>, ou só "
    "escrever a ordem). Nunca diga que ninguém pode executar: se a tarefa exigir ação no computador, "
    "descreva-a e termine dizendo EXATAMENTE: 'Para eu fazer isso, responda *sim* — vou mostrar o plano "
    "com os botões ✅ Aprovar / ❌ Rejeitar.' Nunca peça para o usuário mandar só um emoji ✅. "
    "NUNCA diga que algo já foi executado, iniciado ou confirmado, e NUNCA invente comandos, scripts ou nomes "
    "de arquivo: você só escreve; quem executa é o Executor, depois do botão Aprovar."
)


ARQUIVO_ALMA = RAIZ / "agentes" / "ALMA.md"


def alma() -> str:
    """Identidade comum a todos os agentes (o SOUL.md do Hermes). Passa pela vigilância antes de entrar:
    se alguém enfiar uma instrução perigosa no arquivo, ele é ignorado."""
    try:
        texto = ARQUIVO_ALMA.read_text(encoding="utf-8")
    except OSError:
        return ""
    from . import seguranca
    texto = seguranca.limpar(texto).strip()
    return "" if seguranca.verificar(texto)[0] == "perigoso" else texto


def carregar_env(arquivo: Path = RAIZ / ".env") -> None:
    """Carrega variáveis do .env sem sobrescrever as já definidas no sistema."""
    if not arquivo.exists():
        return
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if linha and not linha.startswith("#") and "=" in linha:
            chave, _, valor = linha.partition("=")
            os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))


def _config_assinaturas(arquivo: Path = ARQUIVO_AGENTES) -> dict:
    try:
        with open(arquivo, "rb") as f:
            return tomllib.load(f).get("assinaturas", {})
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def ordenar_assinaturas(lista: list[str], config: dict | None = None, agente: str = "") -> list[str]:
    """Põe as assinaturas da `frente` primeiro e as de `por_ultimo` no fim (ex.: Codex → … → Claude).
    Agentes em `manter_ordem` (ex.: o revisor, com o Claude) usam a própria lista, com a frente logo depois."""
    config = _config_assinaturas() if config is None else config
    if not config.get("ativo"):
        return list(lista)
    if agente and agente in config.get("manter_ordem", []):
        primeiro = lista[:1]
        return primeiro + [m for m in config.get("frente", []) if m not in primeiro] + \
            [m for m in lista[1:] if m not in config.get("frente", [])]
    frente = [m for m in config.get("frente", []) if m]
    ultimos = tuple(m for m in config.get("por_ultimo", []) if m)

    def no_fim(m: str) -> bool:
        return any(m == u or m.startswith(u + "/") for u in ultimos)

    def na_frente(m: str) -> bool:
        return any(m == f or m.startswith(f + "/") for f in frente)

    if agente and agente in config.get("com_busca", []):
        frente = [f"{m}+web" if m == "cli:codex" else m for m in frente]
    meio = [m for m in lista if not na_frente(m) and not no_fim(m)]
    fim = [m for m in lista if no_fim(m)]
    return frente + meio + fim


@dataclass
class Agente:
    nome: str
    descricao: str
    habilidade: str
    modelos: list[str] = field(default_factory=list)
    base: str = ""            # pasta da biblioteca (agentes de conhecimento)
    privacidade: str = "nuvem"  # "local" = só modelos no seu PC (Ollama); nada sai da máquina
    acervo: bool = False        # consulta o acervo inteiro lido pelo Agente Leitor (Drive + HDs)

    def modelos_permitidos(self, modelos: list[str] | None = None) -> list[str]:
        lista = modelos or ordenar_assinaturas(self.modelos, agente=self.nome)
        if self.privacidade == "local":
            lista = [m for m in lista if m.split(":", 1)[0] in ("ollama", "eco")] or ["ollama:qwen3:4b"]
        return lista

    def executar(self, tarefa: str, contexto: str = "", modelos: list[str] | None = None,
                 log=print, consulta: str | None = None, conversa: bool = False) -> tuple[str, str]:
        """Executa a tarefa. Retorna (modelo_usado, resposta).
        `consulta`: texto usado para buscar na biblioteca/acervo (padrão: a própria tarefa)."""
        busca = consulta or tarefa
        if self.privacidade == "local":
            from . import caixa_postal
            if caixa_postal.papel() == "nuvem":   # agente privado: só roda no seu PC, nunca na nuvem
                raise caixa_postal.PrecisaDoPC(f"o agente {self.nome} é privado (só IA local)")
        hab = habilidades.carregar(self.habilidade)
        identidade = alma()
        sistema = (f"{identidade}\n\n---\n" if identidade else "") + f"{hab.como_prompt()}\n\n---\n{REGRAS_GERAIS}"
        mensagem = f"## Tarefa\n{tarefa}"
        if self.base:
            from . import conhecimento, leitor
            # base "curadoria:<nome>": usa a biblioteca curada com esse nome (vários agentes podem compartilhá-la)
            biblioteca = self.base.removeprefix("curadoria:") if self.base.startswith("curadoria:") else self.nome
            trechos = conhecimento.buscar(biblioteca, busca)
            # material que não é texto (áudio, vídeo, imagem, compactados) vem das fichas do Agente Leitor
            trechos += leitor.buscar(busca, limite=max(2, 8 - len(trechos)), prefixo=self.base)
            if trechos:
                log(f"   📚 {len(trechos)} trechos da biblioteca de {self.nome}")
                mensagem = f"{conhecimento.como_contexto(trechos)}\n\n{mensagem}"
                sistema += REGRAS_CONVERSA if conversa else REGRAS_BIBLIOTECA
            else:
                log(f"   📚 nada encontrado na biblioteca de {self.nome} (está indexada?)")
                if not conversa:
                    sistema += "\nNenhum trecho da biblioteca foi encontrado para esta tarefa: avise isso no início da resposta."
        if self.acervo:
            from . import conhecimento, leitor
            achados = leitor.buscar(busca)
            if achados:
                log(f"   🗃 {len(achados)} materiais do seu acervo (Drive/HDs) relacionados")
                mensagem = (conhecimento.como_contexto(achados).replace(
                    "Trechos da sua biblioteca", "Materiais do seu acervo pessoal (Drive e HDs)")
                    + f"\n\n{mensagem}")
                sistema += ("\n\nVocê recebeu materiais do acervo pessoal do usuário: aproveite-os quando forem "
                            + ("relevantes, mencionando-os com naturalidade (sem marcadores [n])." if conversa else
                               "relevantes, cite-os como [n] e aponte se algum merece leitura completa."))
        if contexto:
            mensagem = f"## Contexto (resultados anteriores)\n{contexto}\n\n{mensagem}"
        return provedores.chamar_com_reserva(self.modelos_permitidos(modelos), sistema, mensagem, log=log)


REGRAS_CONVERSA = (
    "\n\nVocê recebeu trechos da biblioteca pessoal do usuário: use o que for útil, com naturalidade, explicando "
    "com suas palavras (nunca copie trechos longos). Se for relevante, mencione a obra ou o curso de onde veio "
    "a ideia, de forma natural (ex.: 'no curso X que você tem…'), sem marcadores [n] nem lista de fontes."
)

REGRAS_BIBLIOTECA = (
    "\n\nVocê é um agente de conhecimento: fundamente a resposta nos trechos da biblioteca fornecidos, "
    "cite-os como [n] e liste as fontes usadas no final. Explique com suas próprias palavras — "
    "nunca copie trechos longos do material. Se os trechos não cobrirem a pergunta, diga claramente "
    "o que veio da biblioteca e o que é conhecimento geral."
)


def carregar_combinacoes(arquivo: Path = ARQUIVO_AGENTES) -> dict[str, dict]:
    """Equipes recomendadas: {nome: {"agentes": [...], "quando": "..."}}."""
    with open(arquivo, "rb") as f:
        return tomllib.load(f).get("combinacoes", {})


def carregar_equipe(arquivo: Path = ARQUIVO_AGENTES) -> tuple[Agente, dict[str, Agente]]:
    """Retorna (superagente, {nome: agente})."""
    with open(arquivo, "rb") as f:
        dados = tomllib.load(f)
    s = dados["superagente"]
    superagente = Agente("superagente", "Orquestrador", s["habilidade"], s["modelos"])
    equipe = {
        nome: Agente(nome, a.get("descricao", ""), a["habilidade"], a["modelos"],
                     a.get("base", ""), a.get("privacidade", "nuvem"), bool(a.get("acervo", False)))
        for nome, a in dados.get("agentes", {}).items()
    }
    return superagente, equipe
