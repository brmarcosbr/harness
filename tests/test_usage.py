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
