import json
from pathlib import Path

import pytest

from radar_pncp.classifier import ClassificadorRegras
from radar_pncp.config import carregar_config

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = Path(__file__).resolve().parents[1] / "config" / "categorias.yaml"


@pytest.fixture
def pagina1():
    return json.loads((FIXTURES / "dispensas_mg_pagina1.json").read_text(encoding="utf-8"))


@pytest.fixture
def pagina2():
    return json.loads((FIXTURES / "dispensas_mg_pagina2.json").read_text(encoding="utf-8"))


@pytest.fixture
def regras():
    return ClassificadorRegras(carregar_config(CONFIG))
