"""Agregação e geração dos relatórios (Markdown e CSV)."""
from __future__ import annotations

import csv
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .classifier import ClassificadorRegras, Resultado
from .models import Contratacao

CSV_COLUNAS = [
    "id", "categoria", "aderencia", "metodo", "termos", "uf", "municipio", "orgao", "modalidade",
    "valor_estimado", "data_publicacao", "data_encerramento", "objeto", "link",
]


@dataclass(frozen=True)
class Linha:
    contratacao: Contratacao
    resultado: Resultado


@dataclass(frozen=True)
class ResumoCategoria:
    categoria: str
    nome: str
    aderencia: str
    contratacoes: int
    orgaos: int
    mediana: Optional[float]
    soma: float
    por_semana: Optional[float]


def brl(valor: Optional[float], minimo: float = 100.0) -> str:
    if valor is None or valor < minimo:
        return "sigiloso/—"
    return "R$ " + f"{valor:,.0f}".replace(",", ".")


def resumir(linhas: Sequence[Linha], regras: ClassificadorRegras, dias: Optional[int] = None) -> List[ResumoCategoria]:
    minimo = regras.config.valor_minimo_confiavel
    grupos: Dict[str, List[Contratacao]] = defaultdict(list)
    for ln in linhas:
        if ln.resultado.categoria:
            grupos[ln.resultado.categoria].append(ln.contratacao)
    resumos = []
    for cat in regras.config.categorias:
        itens = grupos.get(cat.id, [])
        valores = [c.valor_estimado for c in itens if c.valor_estimado is not None and c.valor_estimado >= minimo]
        resumos.append(ResumoCategoria(
            categoria=cat.id, nome=cat.nome, aderencia=cat.aderencia, contratacoes=len(itens),
            orgaos=len({c.cnpj_orgao for c in itens}),
            mediana=statistics.median(valores) if valores else None,
            soma=sum(valores),
            por_semana=round(len(itens) / (dias / 7), 1) if dias else None,
        ))
    return resumos


def tabela_resumo(resumos: Sequence[ResumoCategoria], minimo: float = 100.0) -> str:
    com_ritmo = any(r.por_semana is not None for r in resumos)
    cab = "| Categoria | Aderência | Contratações | Órgãos | Valor mediano | Valor total |" + (" Ritmo |" if com_ritmo else "")
    sep = "|---|---|---:|---:|---:|---:|" + ("---:|" if com_ritmo else "")
    linhas = [cab, sep]
    for r in resumos:
        ritmo = f" ~{r.por_semana:g}/semana |" if com_ritmo else ""
        linhas.append(f"| {r.nome} | {r.aderencia} | {r.contratacoes} | {r.orgaos} | {brl(r.mediana, minimo)} | {brl(r.soma or None, minimo)} |{ritmo}")
    return "\n".join(linhas)


def _celula(texto: str, limite: int) -> str:
    return texto.replace("|", "/")[:limite]


def gerar_markdown(
    linhas: Sequence[Linha],
    regras: ClassificadorRegras,
    titulo: str,
    descricao: str,
    dias: Optional[int] = None,
    max_linhas: int = 25,
) -> str:
    minimo = regras.config.valor_minimo_confiavel
    total = len(linhas)
    excluidas = sum(1 for ln in linhas if ln.resultado.excluido_por)
    classificadas = sum(1 for ln in linhas if ln.resultado.categoria)
    por_llm = sum(1 for ln in linhas if ln.resultado.categoria and ln.resultado.metodo == "llm")
    saida = [f"# {titulo}", "", descricao, ""]
    saida += [
        f"- Contratações analisadas: **{total}**",
        f"- Classificadas em alguma categoria: **{classificadas}** (via LLM: {por_llm})",
        f"- Descartadas por termos de exclusão: {excluidas}",
        f"- Sem categoria: {total - classificadas - excluidas}",
        "",
        "## Demanda por categoria",
        "",
        tabela_resumo(resumir(linhas, regras, dias), minimo),
        "",
    ]
    for cat in regras.config.categorias:
        itens = [ln for ln in linhas if ln.resultado.categoria == cat.id]
        if not itens:
            continue
        itens.sort(key=lambda ln: -(ln.contratacao.valor_estimado or 0))
        saida += [f"## {cat.nome} ({len(itens)})", "", f"Aderência: {cat.aderencia}. Mostrando as {min(len(itens), max_linhas)} de maior valor.", ""]
        saida += ["| Publicação | Encerra | Município | Órgão | Modalidade | Valor est. | Objeto | Link |", "|---|---|---|---|---|---:|---|---|"]
        for ln in itens[:max_linhas]:
            c = ln.contratacao
            saida.append(
                f"| {c.data_publicacao} | {c.data_encerramento} | {_celula(c.municipio, 30)} | {_celula(c.orgao.title(), 40)} | "
                f"{_celula(c.modalidade, 25)} | {brl(c.valor_estimado, minimo)} | {_celula(c.objeto, 200)} | [PNCP]({c.link}) |"
            )
        saida.append("")
    return "\n".join(saida)


def gravar_csv(linhas: Sequence[Linha], regras: ClassificadorRegras, caminho: Path) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with open(caminho, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUNAS)
        for ln in linhas:
            c, r = ln.contratacao, ln.resultado
            w.writerow([
                c.id, r.categoria or "", regras.aderencia(r.categoria), r.metodo if r.categoria else "", ";".join(r.termos),
                c.uf, c.municipio, c.orgao, c.modalidade, "" if c.valor_estimado is None else c.valor_estimado,
                c.data_publicacao, c.data_encerramento, c.objeto, c.link,
            ])


def contagem_modalidades(linhas: Sequence[Linha]) -> Counter:
    return Counter(ln.contratacao.modalidade for ln in linhas)
