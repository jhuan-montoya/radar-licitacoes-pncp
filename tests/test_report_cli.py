import csv
import json

from radar_pncp.cli import main
from radar_pncp.classifier import classificar_lote
from radar_pncp.models import Contratacao, limpar_objeto
from radar_pncp.report import Linha, brl, gerar_markdown, resumir


def linhas_da_fixture(regras, pagina1, pagina2):
    cs = [Contratacao.from_api(x) for x in pagina1["data"] + pagina2["data"]]
    return [Linha(c, r) for c, r in zip(cs, classificar_lote([c.objeto for c in cs], regras))]


def test_from_api_normaliza_campos(pagina1):
    c = Contratacao.from_api(pagina1["data"][0])
    assert c.uf == "MG" and c.link.startswith("https://pncp.gov.br/app/editais/")
    assert len(c.data_publicacao) == 10


def test_limpar_objeto_remove_tag_e_espacos():
    assert limpar_objeto("[Suspenso]  -  Contratação   de   site") == "Contratação de site"


def test_brl_formata_e_esconde_sigilosos():
    assert brl(15180) == "R$ 15.180"
    assert brl(0.01) == "sigiloso/—" and brl(None) == "sigiloso/—"


def test_resumo_conta_orgaos_mediana_e_ritmo(regras, pagina1, pagina2):
    linhas = linhas_da_fixture(regras, pagina1, pagina2)
    resumo = {r.categoria: r for r in resumir(linhas, regras, dias=7)}
    site = resumo["site"]
    assert site.contratacoes >= 1 and site.orgaos <= site.contratacoes
    assert site.por_semana == site.contratacoes
    assert sum(r.contratacoes for r in resumo.values()) == sum(1 for ln in linhas if ln.resultado.categoria)


def test_markdown_tem_tabela_agregada_e_secoes(regras, pagina1, pagina2):
    md = gerar_markdown(linhas_da_fixture(regras, pagina1, pagina2), regras, "Titulo", "Fonte X", dias=90)
    assert md.startswith("# Titulo")
    assert "| Categoria | Aderência | Contratações |" in md and "/semana" in md
    assert "## Site, portal, hospedagem, e-mail" in md and "pncp.gov.br/app/editais" in md


def test_cli_analisar_offline_gera_md_e_csv(tmp_path, pagina1, pagina2, capsys):
    entrada = tmp_path / "in.json"
    entrada.write_text(json.dumps(pagina1["data"] + pagina2["data"]), encoding="utf-8")
    saida = tmp_path / "out"
    assert main(["analisar", "--entrada", str(entrada), "--saida", str(saida)]) == 0
    assert (saida / "relatorio.md").read_text(encoding="utf-8").startswith("# Radar de licitações")
    with open(saida / "contratacoes.csv", encoding="utf-8") as f:
        linhas = list(csv.DictReader(f))
    assert len(linhas) == len(pagina1["data"]) + len(pagina2["data"])
    assert {"site", "chatbot_ia", ""} <= {ln["categoria"] for ln in linhas}


def test_cli_aceita_config_json(tmp_path, pagina1):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"categorias": [{"id": "cert", "nome": "Certificados", "aderencia": "alta", "palavras_chave": ["certificado"]}]}))
    entrada = tmp_path / "in.json"
    entrada.write_text(json.dumps(pagina1["data"]), encoding="utf-8")
    assert main(["analisar", "--entrada", str(entrada), "--config", str(cfg), "--saida", str(tmp_path / "o")]) == 0
    assert "Certificados" in (tmp_path / "o" / "relatorio.md").read_text(encoding="utf-8")


def test_cli_llm_sem_chave_segue_so_com_regras(tmp_path, pagina1, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    entrada = tmp_path / "in.json"
    entrada.write_text(json.dumps(pagina1["data"]), encoding="utf-8")
    assert main(["analisar", "--entrada", str(entrada), "--llm", "anthropic", "--saida", str(tmp_path / "o")]) == 0
