"""Cliente HTTP da API de consulta do PNCP: paginação, rate limit e retries."""
from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

log = logging.getLogger(__name__)

BASE_URL = "https://pncp.gov.br/api/consulta/v1"
TAMANHO_PAGINA_MAX = 50
STATUS_REPETIVEIS = {408, 425, 429, 500, 502, 503, 504}

# transporte: (url, timeout) -> (status, headers, corpo). Injetável para testes.
Transport = Callable[[str, float], Tuple[int, Dict[str, str], bytes]]


class PNCPError(RuntimeError):
    """Falha definitiva ao consultar o PNCP (erro de cliente ou retries esgotados)."""


def urllib_transport(url: str, timeout: float) -> Tuple[int, Dict[str, str], bytes]:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "radar-licitacoes-pncp/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:  # status != 2xx também devolve corpo
        return exc.code, dict(exc.headers or {}), exc.read()


def _yyyymmdd(d: date) -> str:
    return d.strftime("%Y%m%d")


def dividir_janela(inicio: date, fim: date, dias: int) -> List[Tuple[date, date]]:
    """Divide [inicio, fim] em blocos de até `dias` dias (extremos inclusivos)."""
    if fim < inicio:
        raise ValueError("data final anterior à inicial")
    blocos, atual = [], inicio
    while atual <= fim:
        bloco_fim = min(atual + timedelta(days=dias - 1), fim)
        blocos.append((atual, bloco_fim))
        atual = bloco_fim + timedelta(days=1)
    return blocos


class PNCPClient:
    def __init__(
        self,
        transport: Transport = urllib_transport,
        base_url: str = BASE_URL,
        intervalo_min: float = 0.7,
        max_tentativas: int = 6,
        backoff_base: float = 2.0,
        backoff_max: float = 60.0,
        timeout: float = 60.0,
        cache_dir: Optional[Path] = None,
        sleep: Callable[[float], None] = time.sleep,
        relogio: Callable[[], float] = time.monotonic,
    ) -> None:
        self.transport = transport
        self.base_url = base_url.rstrip("/")
        self.intervalo_min = intervalo_min
        self.max_tentativas = max_tentativas
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.timeout = timeout
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._sleep = sleep
        self._relogio = relogio
        self._ultimo_pedido: Optional[float] = None

    # --- baixo nível -------------------------------------------------
    def _respeitar_rate_limit(self) -> None:
        if self._ultimo_pedido is not None:
            falta = self.intervalo_min - (self._relogio() - self._ultimo_pedido)
            if falta > 0:
                self._sleep(falta)
        self._ultimo_pedido = self._relogio()

    def _espera(self, tentativa: int, headers: Dict[str, str]) -> float:
        retry_after = {k.lower(): v for k, v in headers.items()}.get("retry-after")
        if retry_after and retry_after.isdigit():
            return min(float(retry_after), self.backoff_max)
        return min(self.backoff_base * (2 ** tentativa), self.backoff_max)

    def _arquivo_cache(self, url: str) -> Optional[Path]:
        if not self.cache_dir:
            return None
        return self.cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".json")

    def get_json(self, caminho: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """GET com retries. Devolve None quando a API responde 204 (sem resultados)."""
        url = f"{self.base_url}/{caminho}?{urllib.parse.urlencode(params)}"
        arq = self._arquivo_cache(url)
        if arq and arq.exists():
            return json.loads(arq.read_text(encoding="utf-8"))

        ultimo_erro = "desconhecido"
        for tentativa in range(self.max_tentativas):
            self._respeitar_rate_limit()
            headers: Dict[str, str] = {}
            try:
                status, headers, corpo = self.transport(url, self.timeout)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                ultimo_erro = f"erro de rede: {exc}"
            else:
                if status == 204:
                    return None
                if status == 200:
                    try:
                        dados = json.loads(corpo)
                    except json.JSONDecodeError as exc:
                        ultimo_erro = f"JSON inválido: {exc}"
                    else:
                        if arq:
                            arq.parent.mkdir(parents=True, exist_ok=True)
                            arq.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
                        return dados
                elif status in STATUS_REPETIVEIS:
                    ultimo_erro = f"HTTP {status}"
                else:
                    raise PNCPError(f"HTTP {status} em {url}: {corpo[:200].decode('utf-8', 'replace')}")
            if tentativa < self.max_tentativas - 1:
                espera = self._espera(tentativa, headers)
                log.warning("%s (tentativa %d/%d); nova tentativa em %.1fs", ultimo_erro, tentativa + 1, self.max_tentativas, espera)
                self._sleep(espera)
        raise PNCPError(f"retries esgotados ({ultimo_erro}) em {url}")

    # --- paginação ---------------------------------------------------
    def paginas(self, caminho: str, params: Dict[str, Any], tamanho: int = TAMANHO_PAGINA_MAX) -> Iterator[Dict[str, Any]]:
        pagina = 1
        while True:
            dados = self.get_json(caminho, {**params, "pagina": pagina, "tamanhoPagina": tamanho})
            if not dados or not dados.get("data"):
                return
            yield dados
            if pagina >= dados.get("totalPaginas", 1):
                return
            pagina += 1

    # --- consultas ---------------------------------------------------
    def publicadas(self, uf: str, modalidade: int, inicio: date, fim: date, chunk_dias: int = 30) -> Iterator[Dict[str, Any]]:
        """Contratações publicadas na janela [inicio, fim]; a janela é fatiada em blocos."""
        for ini, f in dividir_janela(inicio, fim, chunk_dias):
            params = {"dataInicial": _yyyymmdd(ini), "dataFinal": _yyyymmdd(f), "codigoModalidadeContratacao": modalidade, "uf": uf}
            for pg in self.paginas("contratacoes/publicacao", params):
                yield from pg["data"]

    def abertas(self, uf: str, modalidade: int, ate: date) -> Iterator[Dict[str, Any]]:
        """Contratações com proposta ainda aberta, com encerramento até `ate`."""
        params = {"dataFinal": _yyyymmdd(ate), "codigoModalidadeContratacao": modalidade, "uf": uf}
        for pg in self.paginas("contratacoes/proposta", params):
            yield from pg["data"]


def deduplicar(itens: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove repetições (o mesmo registro pode aparecer em mais de uma janela/modalidade)."""
    vistos, saida = set(), []
    for it in itens:
        chave = it.get("numeroControlePNCP") or (it.get("orgaoEntidade", {}).get("cnpj"), it.get("anoCompra"), it.get("sequencialCompra"))
        if chave not in vistos:
            vistos.add(chave)
            saida.append(it)
    return saida
