"""Linha de comando: `radar-pncp baixar` e `radar-pncp analisar`."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .classifier import ClassificadorRegras, classificar_lote
from .client import PNCPClient, PNCPError, deduplicar
from .config import carregar_config
from .llm import ClassificadorLLM, LLMIndisponivel, MODELO_PADRAO, completar_anthropic
from .models import MODALIDADES, Contratacao
from .report import Linha, gerar_markdown, gravar_csv

CONFIG_PADRAO = Path(__file__).resolve().parents[2] / "config" / "categorias.yaml"
log = logging.getLogger("radar_pncp")


def _data(texto: str) -> date:
    return datetime.strptime(texto, "%Y-%m-%d").date()


def _modalidades(texto: str) -> List[int]:
    try:
        codigos = [int(x) for x in texto.split(",") if x.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError("use códigos numéricos separados por vírgula, ex.: 6,8")
    invalidos = [c for c in codigos if c not in MODALIDADES]
    if invalidos:
        raise argparse.ArgumentTypeError(f"modalidade desconhecida: {invalidos}")
    return codigos


def _opcoes_busca(p: argparse.ArgumentParser) -> None:
    p.add_argument("--uf", default="MG", help="sigla da UF (padrão: MG)")
    p.add_argument("--modalidades", type=_modalidades, default=[8],
                   help="códigos PNCP separados por vírgula (padrão: 8 = dispensa; 6 = pregão eletrônico; 12 = credenciamento)")
    p.add_argument("--modo", choices=["publicadas", "abertas"], default="publicadas",
                   help="publicadas: janela de publicação; abertas: propostas ainda abertas")
    p.add_argument("--dias", type=int, default=90, help="janela em dias terminando em --fim (modo publicadas)")
    p.add_argument("--inicio", type=_data, help="início da janela, AAAA-MM-DD (substitui --dias)")
    p.add_argument("--fim", type=_data, default=None, help="fim da janela, AAAA-MM-DD (padrão: hoje)")
    p.add_argument("--chunk-dias", type=int, default=30, help="tamanho do bloco de datas por consulta")
    p.add_argument("--intervalo", type=float, default=0.7, help="segundos mínimos entre requisições")
    p.add_argument("--cache", type=Path, help="pasta de cache das respostas (reexecuções sem rede)")


def _baixar(args: argparse.Namespace) -> List[Dict[str, Any]]:
    cliente = PNCPClient(intervalo_min=args.intervalo, cache_dir=args.cache)
    fim = args.fim or date.today()
    brutos: List[Dict[str, Any]] = []
    for mod in args.modalidades:
        if args.modo == "abertas":
            it = cliente.abertas(args.uf, mod, ate=args.fim or fim + timedelta(days=365))
        else:
            inicio = args.inicio or fim - timedelta(days=args.dias)
            it = cliente.publicadas(args.uf, mod, inicio, fim, args.chunk_dias)
        antes = len(brutos)
        for i, item in enumerate(it, 1):
            brutos.append(item)
            if i % 500 == 0:
                log.info("modalidade %s: %d registros", mod, i)
        log.info("modalidade %s (%s): %d registros", mod, MODALIDADES[mod], len(brutos) - antes)
    return deduplicar(brutos)


def _janela_dias(args: argparse.Namespace) -> Optional[int]:
    if args.modo == "abertas":
        return None
    if args.inicio:
        return ((args.fim or date.today()) - args.inicio).days + 1
    return args.dias


def cmd_baixar(args: argparse.Namespace) -> int:
    dados = _baixar(args)
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    args.saida.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
    print(f"{len(dados)} contratações salvas em {args.saida}")
    return 0


def _criar_llm(args: argparse.Namespace, regras: ClassificadorRegras) -> Optional[ClassificadorLLM]:
    if args.llm == "nenhum":
        return None
    try:
        completar = completar_anthropic(args.llm_modelo)
    except LLMIndisponivel as exc:
        log.warning("LLM indisponível (%s); seguindo só com regras", exc)
        return None
    return ClassificadorLLM(regras.config.categorias, completar, args.llm_max, args.saida / "cache-llm.json")


def cmd_analisar(args: argparse.Namespace) -> int:
    config = carregar_config(args.config)
    regras = ClassificadorRegras(config)
    brutos = json.loads(args.entrada.read_text(encoding="utf-8")) if args.entrada else _baixar(args)
    contratacoes = [Contratacao.from_api(x) for x in brutos]

    llm = _criar_llm(args, regras)
    resultados = classificar_lote([c.objeto for c in contratacoes], regras, llm)
    if llm:
        llm.salvar_cache()
        log.info("LLM: %d chamadas, %d falhas", llm.chamadas, llm.falhas)
    linhas = [Linha(c, r) for c, r in zip(contratacoes, resultados)]

    dias = None if args.entrada else _janela_dias(args)
    if args.entrada:
        descricao = f"Fonte: PNCP, arquivo `{args.entrada.name}`."
    elif args.modo == "abertas":
        descricao = f"Fonte: API de consulta do PNCP, UF {args.uf}, propostas abertas, modalidades {args.modalidades}."
    else:
        descricao = f"Fonte: API de consulta do PNCP, UF {args.uf}, últimos {dias} dias, modalidades {args.modalidades}."
    md = gerar_markdown(linhas, regras, f"Radar de licitações — {args.uf}", descricao, dias, args.max_linhas)

    args.saida.mkdir(parents=True, exist_ok=True)
    (args.saida / "relatorio.md").write_text(md, encoding="utf-8")
    gravar_csv(linhas, regras, args.saida / "contratacoes.csv")
    print(f"{len(linhas)} contratações analisadas; relatório em {args.saida}/relatorio.md e {args.saida}/contratacoes.csv")
    return 0


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="radar-pncp", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="comando", required=True)

    b = sub.add_parser("baixar", help="baixa contratações do PNCP para um JSON")
    _opcoes_busca(b)
    b.add_argument("--saida", type=Path, required=True, help="arquivo JSON de saída")
    b.set_defaults(func=cmd_baixar)

    a = sub.add_parser("analisar", help="classifica e gera relatorio.md + contratacoes.csv")
    _opcoes_busca(a)
    a.add_argument("--entrada", type=Path, help="JSON já baixado (modo offline; não consulta a API)")
    a.add_argument("--config", type=Path, default=CONFIG_PADRAO, help="YAML/JSON de categorias")
    a.add_argument("--saida", type=Path, default=Path("saida"), help="pasta de saída")
    a.add_argument("--max-linhas", type=int, default=25, help="linhas por categoria no Markdown")
    a.add_argument("--llm", choices=["nenhum", "anthropic"], default="nenhum",
                   help="classificador opcional para o que as regras não pegaram (padrão: nenhum, offline)")
    a.add_argument("--llm-modelo", default=MODELO_PADRAO)
    a.add_argument("--llm-max", type=int, default=200, help="máximo de chamadas ao LLM")
    a.set_defaults(func=cmd_analisar)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = construir_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    try:
        return args.func(args)
    except PNCPError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
