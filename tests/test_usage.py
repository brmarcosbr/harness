"""Testes unitários para o módulo usage (funções puras, sem rede)."""

import pytest
from harness.usage import extrair_metricas_usage, calcular_custo


def test_extrair_metricas_usage_completo():
    usage = {
        "promptTokenCount": 725,
        "candidatesTokenCount": 130,
        "totalTokenCount": 855,
        "cachedContentTokenCount": 355
    }
    metricas = extrair_metricas_usage(usage)
    assert metricas["prompt"] == 725
    assert metricas["completion"] == 130
    assert metricas["total"] == 855
    assert metricas["cached"] == 355


def test_extrair_metricas_usage_parcial():
    usage = {
        "promptTokenCount": 100,
        "candidatesTokenCount": 50
    }
    metricas = extrair_metricas_usage(usage)
    assert metricas["prompt"] == 100
    assert metricas["completion"] == 50
    assert metricas["total"] == 150  # calculado se ausente
    assert metricas["cached"] == 0   # padrão 0


def test_extrair_metricas_usage_vazio():
    for vazio in [{}, None]:
        metricas = extrair_metricas_usage(vazio)
        assert metricas["prompt"] == 0
        assert metricas["completion"] == 0
        assert metricas["total"] == 0
        assert metricas["cached"] == 0


def test_calcular_custo_com_desconto_cache():
    # Caso do spike: prompt 725, cached 355, completion 130
    # Preços: input 0.30, cache 0.03, output 2.50 por 1M tokens
    # Esperado:
    # prompt_nao_cacheado = 725 - 355 = 370 -> 370 * 0.30 = 111.0
    # cached = 355 * 0.03 = 10.65
    # completion = 130 * 2.50 = 325.0
    # total = 111.0 + 10.65 + 325.0 = 446.65 / 1_000_000 = 0.00044665 USD
    precos = {
        "input": 0.30,
        "output": 2.50,
        "cache": 0.03
    }
    custo = calcular_custo(
        prompt_total=725,
        cached_total=355,
        completion_total=130,
        precos=precos
    )
    assert pytest.approx(custo, rel=1e-6) == 0.00044665


def test_calcular_custo_sem_cache():
    precos = {
        "input": 0.30,
        "output": 2.50,
        "cache": 0.03
    }
    # Sem cache (cached_total = 0)
    # prompt 100 * 0.30 = 30.0
    # completion 50 * 2.50 = 125.0
    # total = 155.0 / 1_000_000 = 0.000155 USD
    custo = calcular_custo(
        prompt_total=100,
        cached_total=0,
        completion_total=50,
        precos=precos
    )
    assert pytest.approx(custo, rel=1e-6) == 0.000155


def test_precos_aviso_data_antiga_e_override_ambiente(monkeypatch, capsys):
    from datetime import date, timedelta
    from harness.config import (
        PRECOS_CONFERIDOS_EM,
        PROVIDER_PRECOS,
        TABELA_PRECOS_PADRAO,
        verificar_idade_precos,
    )

    # 1. Com data recente (ou None padrão se PRECOS_CONFERIDOS_EM for recente)
    data_hoje = date.today().isoformat()
    assert verificar_idade_precos(data_conferencia=data_hoje, limite_dias=180) is False
    assert capsys.readouterr().err == ""

    # 2. Com data antiga simulada (> 180 dias atrás), emite aviso em stderr
    data_antiga = (date.today() - timedelta(days=200)).isoformat()
    assert verificar_idade_precos(data_conferencia=data_antiga, limite_dias=180) is True
    err_output = capsys.readouterr().err
    assert "[AVISO] Tabela de preços de LLM não é atualizada" in err_output
    assert data_antiga in err_output

    # 3. Com override via variáveis de ambiente PRECO_*
    monkeypatch.setenv("PRECO_GEMINI_INPUT", "0.99")
    monkeypatch.setenv("PRECO_OPENAI_OUTPUT", "1.25")
    monkeypatch.setenv("PRECO_DEEPSEEK_CACHE", "0.015")

    assert PROVIDER_PRECOS["gemini"]["input"] == 0.99
    assert PROVIDER_PRECOS["openai"]["output"] == 1.25
    assert PROVIDER_PRECOS["deepseek"]["cache"] == 0.015

    # 4. Sem variáveis de ambiente, mantém o padrão
    monkeypatch.delenv("PRECO_GEMINI_INPUT", raising=False)
    assert PROVIDER_PRECOS["gemini"]["input"] == TABELA_PRECOS_PADRAO["gemini"]["input"]


def test_tabela_deepseek_oficial_e_modelo_default(monkeypatch):
    from harness.config import (
        DEFAULT_MODELS,
        PRECOS_CONFERIDOS_EM,
        PROVIDER_PRECOS,
        TABELA_PRECOS_PADRAO,
        obter_precos_com_override,
    )

    for chave in ("PRECO_DEEPSEEK_INPUT", "PRECO_DEEPSEEK_OUTPUT", "PRECO_DEEPSEEK_CACHE"):
        monkeypatch.delenv(chave, raising=False)

    # Oficial conferido em 2026-09-11 (api-docs.deepseek.com/quick_start/pricing),
    # modelo deepseek-flash, valores OFF-PEAK: 0.15 / 0.60 / 0.003
    esperado = {"input": 0.15, "output": 0.60, "cache": 0.003}
    assert TABELA_PRECOS_PADRAO["deepseek"] == esperado
    assert obter_precos_com_override()["deepseek"] == esperado
    assert PROVIDER_PRECOS["deepseek"] == esperado
    assert PRECOS_CONFERIDOS_EM == "2026-09-11"

    # O nome antigo é alias aposentado: o default tem que travar no modelo servido
    assert DEFAULT_MODELS["deepseek"] == "deepseek-flash"


def test_override_de_preco_deepseek_vence_a_tabela(monkeypatch):
    from harness.config import PROVIDER_PRECOS, TABELA_PRECOS_PADRAO, obter_precos_com_override

    monkeypatch.setenv("PRECO_DEEPSEEK_INPUT", "9.99")
    monkeypatch.setenv("PRECO_DEEPSEEK_OUTPUT", "8.88")
    monkeypatch.setenv("PRECO_DEEPSEEK_CACHE", "0.111")

    assert obter_precos_com_override()["deepseek"] == {"input": 9.99, "output": 8.88, "cache": 0.111}
    assert PROVIDER_PRECOS["deepseek"]["input"] == 9.99
    # A tabela tabelada em si não é mutada pelo override
    assert TABELA_PRECOS_PADRAO["deepseek"] == {"input": 0.15, "output": 0.60, "cache": 0.003}

