import pytest

from radar_pncp.classifier import classificar_lote, compilar_termo, normalizar
from radar_pncp.config import ConfigError, config_de_dict
from radar_pncp.llm import ClassificadorLLM, interpretar_resposta, montar_prompt


def test_normalizar_remove_acento_e_caixa():
    assert normalizar("  Inteligência   ARTIFICIAL ") == "inteligencia artificial"


def test_sintaxe_dos_termos():
    assert compilar_termo("chatbot").search("servico de chatbots")
    assert compilar_termo("ia$").search("curso de ia para servidores")
    assert not compilar_termo("ia$").search("viagem de iate")
    assert compilar_termo("^aquisicao").search("aquisicao de pneus")
    assert not compilar_termo("^aquisicao").search("servico de aquisicao")


@pytest.mark.parametrize("objeto,esperada", [
    ("Contratação de empresa para desenvolvimento e hospedagem do sítio eletrônico da Câmara", "site"),
    ("Gerenciamento de redes sociais e criação de mídia digital", "redes_sociais"),
    ("Implantação de chatbot de WhatsApp para atendimento ao cidadão", "chatbot_ia"),
    ("Curso de capacitação com uso de Inteligência Artificial para servidores", "chatbot_ia"),
    ("Licença de uso de software de gestão tributária", "software_licenca"),
    ("Serviços de filmagem e fotografia para eventos", "audiovisual"),
    ("[Cancelado] - Oficina de informática para idosos", "treinamento_ti"),
])
def test_classificacao_por_regras(regras, objeto, esperada):
    assert regras.classificar(objeto).categoria == esperada


def test_exclusao_global_tem_prioridade(regras):
    r = regras.classificar("Aquisição de notebooks com software de gestão")
    assert r.categoria is None and r.excluido_por == "^aquisicao"
    assert regras.classificar("Reforma do prédio e instalação de site").excluido_por == "reforma"


def test_sem_correspondencia_fica_sem_categoria(regras):
    r = regras.classificar("Fornecimento de gás de cozinha")
    assert r.categoria is None and r.excluido_por is None


def test_mais_termos_distintos_vence_e_empate_segue_ordem(regras):
    r = regras.classificar("Software de atendimento virtual com chatbot e whatsapp")
    assert r.categoria == "chatbot_ia" and len(r.termos) >= 3
    assert "software_licenca" in r.categorias_candidatas
    assert regras.classificar("site e redes sociais").categoria == "site"  # empate: ordem do YAML


def test_excluir_por_categoria():
    cfg = config_de_dict({"categorias": [
        {"id": "a", "palavras_chave": ["site"], "excluir": ["ssl"]},
        {"id": "b", "palavras_chave": ["certificado"]},
    ]})
    from radar_pncp.classifier import ClassificadorRegras
    r = ClassificadorRegras(cfg).classificar("certificado ssl para o site")
    assert r.categoria == "b"


@pytest.mark.parametrize("bruto", [
    {},
    {"categorias": [{"id": "a", "palavras_chave": []}]},
    {"categorias": [{"id": "a", "palavras_chave": ["x"]}, {"id": "a", "palavras_chave": ["y"]}]},
    {"categorias": [{"id": "a", "palavras_chave": ["x"], "aderencia": "enorme"}]},
])
def test_config_invalida(bruto):
    with pytest.raises(ConfigError):
        config_de_dict(bruto)


def test_fixture_real_classifica_o_esperado(regras, pagina1, pagina2):
    from radar_pncp.models import Contratacao
    cs = [Contratacao.from_api(x) for x in pagina1["data"] + pagina2["data"]]
    por_trecho = {c.objeto[:40]: regras.classificar(c.objeto) for c in cs}
    cats = {r.categoria for r in por_trecho.values()}
    assert {"site", "redes_sociais", "chatbot_ia", "audiovisual"} <= cats
    assert any(r.excluido_por for r in por_trecho.values())


# --- LLM ---------------------------------------------------------------
def test_interpretar_resposta_valida_e_invalida():
    assert interpretar_resposta('ok {"categoria": "site"}', ["site"]) == "site"
    assert interpretar_resposta('{"categoria": "nenhuma"}', ["site"]) is None
    assert interpretar_resposta("sem json", ["site"]) is None


def test_llm_so_atua_onde_regras_nao_acharam(regras):
    chamados = []

    def completar(prompt):
        chamados.append(prompt)
        return '{"categoria": "site"}'

    llm = ClassificadorLLM(regras.config.categorias, completar)
    objetos = ["Chatbot para a prefeitura", "Fornecimento de gás de cozinha", "Aquisição de pneus"]
    res = classificar_lote(objetos, regras, llm)
    assert [r.categoria for r in res] == ["chatbot_ia", "site", None]
    assert [r.metodo for r in res] == ["regras", "llm", "regras"]
    assert len(chamados) == 1 and "gás de cozinha" in chamados[0]


def test_llm_com_falha_cai_para_regras(regras):
    def quebra(_):
        raise RuntimeError("sem rede")

    llm = ClassificadorLLM(regras.config.categorias, quebra)
    res = classificar_lote(["Fornecimento de gás de cozinha"], regras, llm)
    assert res[0].categoria is None and llm.falhas == 1


def test_llm_respeita_limite_de_chamadas_e_cache(regras, tmp_path):
    n = []
    llm = ClassificadorLLM(regras.config.categorias, lambda p: n.append(1) or '{"categoria": "site"}', max_chamadas=1, cache=tmp_path / "c.json")
    assert llm.classificar("a") == "site"
    assert llm.classificar("a") == "site"   # cache
    assert llm.classificar("b") is None     # limite atingido
    llm.salvar_cache()
    assert len(n) == 1
    novo = ClassificadorLLM(regras.config.categorias, lambda p: "x", cache=tmp_path / "c.json")
    assert novo.classificar("a") == "site"


def test_prompt_lista_categorias(regras):
    p = montar_prompt("objeto qualquer", regras.config.categorias)
    assert "chatbot_ia" in p and "nenhuma" in p
