"""Modelo normalizado de uma contratação e conversão a partir do JSON do PNCP."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

PNCP_APP = "https://pncp.gov.br/app/editais"

# Códigos de modalidade de contratação do PNCP (Lei 14.133/2021).
MODALIDADES = {
    1: "Leilão - Eletrônico",
    2: "Diálogo Competitivo",
    3: "Concurso",
    4: "Concorrência - Eletrônica",
    5: "Concorrência - Presencial",
    6: "Pregão - Eletrônico",
    7: "Pregão - Presencial",
    8: "Dispensa de Licitação",
    9: "Inexigibilidade",
    10: "Manifestação de Interesse",
    11: "Pré-qualificação",
    12: "Credenciamento",
    13: "Leilão - Presencial",
}

_TAG_INICIAL = re.compile(r"^\s*\[[^\]]*\]\s*-?\s*")


def limpar_objeto(texto: Optional[str]) -> str:
    """Remove tags iniciais como '[Cancelado]' e normaliza espaços."""
    return re.sub(r"\s+", " ", _TAG_INICIAL.sub("", texto or "")).strip()


@dataclass(frozen=True)
class Contratacao:
    id: str
    cnpj_orgao: str
    orgao: str
    uf: str
    municipio: str
    modalidade: str
    objeto: str
    valor_estimado: Optional[float]
    data_publicacao: str
    data_encerramento: str
    link: str

    @classmethod
    def from_api(cls, item: Dict[str, Any]) -> "Contratacao":
        orgao = item.get("orgaoEntidade") or {}
        unidade = item.get("unidadeOrgao") or {}
        cnpj = orgao.get("cnpj", "")
        ano, seq = item.get("anoCompra"), item.get("sequencialCompra")
        controle = item.get("numeroControlePNCP") or f"{cnpj}-{ano}-{seq}"
        return cls(
            id=controle,
            cnpj_orgao=cnpj,
            orgao=(orgao.get("razaoSocial") or "").strip(),
            uf=unidade.get("ufSigla", ""),
            municipio=unidade.get("municipioNome", ""),
            modalidade=item.get("modalidadeNome", ""),
            objeto=limpar_objeto(item.get("objetoCompra")),
            valor_estimado=item.get("valorTotalEstimado"),
            data_publicacao=(item.get("dataPublicacaoPncp") or "")[:10],
            data_encerramento=(item.get("dataEncerramentoProposta") or "")[:10],
            link=f"{PNCP_APP}/{cnpj}/{ano}/{seq}",
        )
