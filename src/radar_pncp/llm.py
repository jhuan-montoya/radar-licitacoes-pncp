"""Classificador opcional por LLM. Só atua onde as regras não acharam categoria.

Qualquer falha (sem chave, sem pacote, erro de API, resposta inválida) devolve None e o
registro simplesmente fica como as regras o deixaram: o modo offline é o padrão.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Callable, Dict, Optional, Sequence

from .config import Categoria

log = logging.getLogger(__name__)

MODELO_PADRAO = "claude-haiku-4-5"


class LLMIndisponivel(RuntimeError):
    pass


def montar_prompt(objeto: str, categorias: Sequence[Categoria]) -> str:
    linhas = "\n".join(f"- {c.id}: {c.nome}" for c in categorias)
    return (
        "Você classifica o objeto de uma contratação pública brasileira em UMA categoria.\n"
        f"Categorias:\n{linhas}\n- nenhuma: não se encaixa em nenhuma das anteriores\n\n"
        f"Objeto: {objeto[:800]}\n\n"
        'Responda apenas com JSON: {"categoria": "<id>"}'
    )


def interpretar_resposta(texto: str, validos: Sequence[str]) -> Optional[str]:
    achado = re.search(r"\{.*?\}", texto or "", re.S)
    if not achado:
        return None
    try:
        cid = json.loads(achado.group(0)).get("categoria")
    except (json.JSONDecodeError, AttributeError):
        return None
    return cid if cid in validos else None


class ClassificadorLLM:
    def __init__(
        self,
        categorias: Sequence[Categoria],
        completar: Callable[[str], str],
        max_chamadas: int = 200,
        cache: Optional[Path] = None,
    ) -> None:
        self.categorias = list(categorias)
        self.validos = [c.id for c in categorias]
        self.completar = completar
        self.max_chamadas = max_chamadas
        self.chamadas = 0
        self.falhas = 0
        self._cache_path = Path(cache) if cache else None
        self._cache: Dict[str, Optional[str]] = {}
        if self._cache_path and self._cache_path.exists():
            self._cache = json.loads(self._cache_path.read_text(encoding="utf-8"))

    def classificar(self, objeto: str) -> Optional[str]:
        if objeto in self._cache:
            return self._cache[objeto]
        if self.chamadas >= self.max_chamadas:
            return None
        self.chamadas += 1
        try:
            cid = interpretar_resposta(self.completar(montar_prompt(objeto, self.categorias)), self.validos)
        except Exception as exc:  # noqa: BLE001 - qualquer falha cai para as regras
            self.falhas += 1
            log.warning("LLM falhou (%s); mantendo resultado das regras", exc)
            return None
        self._cache[objeto] = cid
        return cid

    def salvar_cache(self) -> None:
        if self._cache_path:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(json.dumps(self._cache, ensure_ascii=False), encoding="utf-8")


def completar_anthropic(modelo: str = MODELO_PADRAO) -> Callable[[str], str]:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise LLMIndisponivel("ANTHROPIC_API_KEY não definida")
    try:
        import anthropic
    except ImportError as exc:
        raise LLMIndisponivel("pacote 'anthropic' não instalado (pip install '.[llm]')") from exc
    client = anthropic.Anthropic()

    def completar(prompt: str) -> str:
        msg = client.messages.create(model=modelo, max_tokens=50, messages=[{"role": "user", "content": prompt}])
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")

    return completar
