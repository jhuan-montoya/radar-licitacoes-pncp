import urllib.error
from datetime import date

import pytest

from radar_pncp.client import PNCPClient, PNCPError, deduplicar, dividir_janela
from helpers import TransporteFalso, json_resp


def cliente(transporte, **kw):
    esperas = []
    c = PNCPClient(transport=transporte, intervalo_min=0.5, sleep=esperas.append, relogio=lambda: 0.0, **kw)
    return c, esperas


def test_dividir_janela_cobre_intervalo_sem_sobreposicao():
    blocos = dividir_janela(date(2026, 7, 18), date(2026, 10, 16), 30)
    assert blocos[0][0] == date(2026, 7, 18) and blocos[-1][1] == date(2026, 10, 16)
    for (_, fim), (ini, _) in zip(blocos, blocos[1:]):
        assert (ini - fim).days == 1
    assert all((f - i).days <= 29 for i, f in blocos)
    with pytest.raises(ValueError):
        dividir_janela(date(2026, 2, 1), date(2026, 1, 1), 30)


def test_paginacao_percorre_todas_as_paginas(pagina1, pagina2):
    t = TransporteFalso([json_resp(pagina1), json_resp(pagina2)])
    c, _ = cliente(t)
    itens = list(c.publicadas("MG", 8, date(2026, 10, 1), date(2026, 10, 16)))
    assert len(itens) == len(pagina1["data"]) + len(pagina2["data"])
    assert "pagina=1" in t.urls[0] and "pagina=2" in t.urls[1]
    assert "uf=MG" in t.urls[0] and "codigoModalidadeContratacao=8" in t.urls[0]
    assert "dataInicial=20261001" in t.urls[0] and "dataFinal=20261016" in t.urls[0]


def test_204_significa_sem_resultados():
    c, _ = cliente(TransporteFalso([(204, {}, b"")]))
    assert list(c.publicadas("MG", 8, date(2026, 10, 1), date(2026, 10, 2))) == []


def test_retry_em_429_respeita_retry_after(pagina2):
    pagina2["totalPaginas"] = 1
    t = TransporteFalso([(429, {"Retry-After": "7"}, b""), json_resp(pagina2)])
    c, esperas = cliente(t)
    assert c.get_json("x", {})["data"]
    assert 7.0 in esperas


def test_retry_em_5xx_e_erro_de_rede_com_backoff_exponencial(pagina2):
    t = TransporteFalso([(503, {}, b""), urllib.error.URLError("caiu"), (502, {}, b""), json_resp(pagina2)])
    c, esperas = cliente(t)
    assert c.get_json("x", {}) is not None
    assert [e for e in esperas if e >= 2] == [2.0, 4.0, 8.0]


def test_erro_de_cliente_nao_e_repetido():
    t = TransporteFalso([(400, {}, b'{"message":"Tamanho de p\xc3\xa1gina inv\xc3\xa1lido"}')])
    c, _ = cliente(t)
    with pytest.raises(PNCPError, match="HTTP 400"):
        c.get_json("x", {})
    assert len(t.urls) == 1


def test_retries_esgotados_levantam_erro():
    t = TransporteFalso([(500, {}, b"")] * 3)
    c, _ = cliente(t, max_tentativas=3)
    with pytest.raises(PNCPError, match="retries esgotados"):
        c.get_json("x", {})


def test_rate_limit_espera_intervalo_minimo_entre_pedidos(pagina2):
    pagina2["totalPaginas"] = 1
    t = TransporteFalso([json_resp(pagina2), json_resp(pagina2)])
    esperas = []
    c = PNCPClient(transport=t, intervalo_min=0.7, sleep=esperas.append, relogio=lambda: 100.0)
    c.get_json("x", {"a": 1})
    c.get_json("x", {"a": 2})
    assert esperas == [0.7]


def test_cache_evita_nova_chamada(tmp_path, pagina2):
    t = TransporteFalso([json_resp(pagina2)])
    c, _ = cliente(t, cache_dir=tmp_path)
    c.get_json("x", {"a": 1})
    c.get_json("x", {"a": 1})
    assert len(t.urls) == 1


def test_deduplicar_por_numero_de_controle(pagina1):
    itens = pagina1["data"] + pagina1["data"][:3]
    assert len(deduplicar(itens)) == len(pagina1["data"])
