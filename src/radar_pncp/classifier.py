"""Classificador por regras: palavras-chave por categoria, sem acento e sem caixa.

Sintaxe dos termos (em config/categorias.yaml):
  termo     casa no início de uma palavra ("chatbot" casa "chatbots")
  termo$    exige fim de palavra ("ia$" não casa "iate")
  ^termo    ancora no início do objeto ("^aquisicao" só casa se o texto começa assim)
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Pattern, Sequence

from .config import Categoria, Config


def normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sem_acento.lower()).strip()


def compilar_termo(termo: str) -> Pattern[str]:
    t = normalizar(termo)
    ancorado, fim = t.startswith("^"), t.endswith("$")
    t = t.strip("^$")
    return re.compile(("^" if ancorado else r"\b") + re.escape(t) + (r"\b" if fim else ""))


@dataclass
class Resultado:
    categoria: Optional[str] = None
    categorias_candidatas: List[str] = field(default_factory=list)
    termos: List[str] = field(default_factory=list)
    excluido_por: Optional[str] = None
    metodo: str = "regras"


class ClassificadorRegras:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._global = [(t, compilar_termo(t)) for t in config.excluir_global]
        self._cats = [
            (c, [(t, compilar_termo(t)) for t in c.palavras_chave], [compilar_termo(t) for t in c.excluir])
            for c in config.categorias
        ]
        self._por_id: Dict[str, Categoria] = {c.id: c for c in config.categorias}

    def aderencia(self, categoria: Optional[str]) -> str:
        return self._por_id[categoria].aderencia if categoria in self._por_id else ""

    def nome(self, categoria: Optional[str]) -> str:
        return self._por_id[categoria].nome if categoria in self._por_id else "Sem categoria"

    def excluido_global(self, objeto: str) -> Optional[str]:
        texto = normalizar(objeto)
        for termo, rx in self._global:
            if rx.search(texto):
                return termo
        return None

    def classificar(self, objeto: str) -> Resultado:
        texto = normalizar(objeto)
        motivo = self.excluido_global(objeto)
        if motivo:
            return Resultado(excluido_por=motivo)

        pontuacao: Dict[str, List[str]] = {}
        for cat, termos, excl in self._cats:
            if any(rx.search(texto) for rx in excl):
                continue
            achados = [t for t, rx in termos if rx.search(texto)]
            if achados:
                pontuacao[cat.id] = achados
        if not pontuacao:
            return Resultado()
        # Mais termos distintos vence; empate fica com a categoria declarada primeiro.
        ordem = [c.id for c in self.config.categorias]
        melhor = max(pontuacao, key=lambda cid: (len(pontuacao[cid]), -ordem.index(cid)))
        return Resultado(categoria=melhor, categorias_candidatas=[c for c in ordem if c in pontuacao], termos=pontuacao[melhor])


def classificar_lote(
    objetos: Sequence[str],
    regras: ClassificadorRegras,
    llm=None,
) -> List[Resultado]:
    """Regras primeiro; o LLM (opcional) só vê o que as regras deixaram sem categoria."""
    resultados = [regras.classificar(o) for o in objetos]
    if llm is not None:
        for obj, res in zip(objetos, resultados):
            if res.categoria is None and res.excluido_por is None:
                cid = llm.classificar(obj)
                if cid:
                    res.categoria, res.metodo = cid, "llm"
    return resultados
