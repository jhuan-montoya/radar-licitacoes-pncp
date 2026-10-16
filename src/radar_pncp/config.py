"""Carrega a configuração de categorias (YAML ou JSON)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

ADERENCIAS = ("alta", "media", "baixa")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Categoria:
    id: str
    nome: str
    aderencia: str
    palavras_chave: List[str]
    excluir: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class Config:
    categorias: List[Categoria]
    excluir_global: List[str]
    valor_minimo_confiavel: float = 100.0


def _lista(valor: Any, onde: str) -> List[str]:
    if valor is None:
        return []
    if not isinstance(valor, list) or not all(isinstance(v, str) for v in valor):
        raise ConfigError(f"{onde} deve ser uma lista de textos")
    return valor


def carregar_config(caminho: Path) -> Config:
    caminho = Path(caminho)
    texto = caminho.read_text(encoding="utf-8")
    if caminho.suffix.lower() == ".json":
        bruto = json.loads(texto)
    else:
        import yaml  # dependência declarada; importada aqui para JSON funcionar sem ela

        bruto = yaml.safe_load(texto)
    return config_de_dict(bruto)


def config_de_dict(bruto: Dict[str, Any]) -> Config:
    if not isinstance(bruto, dict) or not bruto.get("categorias"):
        raise ConfigError("a configuração precisa ter a chave 'categorias'")
    categorias, ids = [], set()
    for i, c in enumerate(bruto["categorias"]):
        cid = c.get("id")
        if not cid or cid in ids:
            raise ConfigError(f"categorias[{i}]: 'id' ausente ou duplicado")
        ids.add(cid)
        aderencia = c.get("aderencia", "media")
        if aderencia not in ADERENCIAS:
            raise ConfigError(f"categoria '{cid}': aderencia deve ser uma de {ADERENCIAS}")
        palavras = _lista(c.get("palavras_chave"), f"categoria '{cid}'.palavras_chave")
        if not palavras:
            raise ConfigError(f"categoria '{cid}': palavras_chave vazia")
        categorias.append(Categoria(cid, c.get("nome", cid), aderencia, palavras, _lista(c.get("excluir"), f"categoria '{cid}'.excluir")))
    return Config(
        categorias=categorias,
        excluir_global=_lista(bruto.get("excluir_global"), "excluir_global"),
        valor_minimo_confiavel=float(bruto.get("valor_minimo_confiavel", 100.0)),
    )
