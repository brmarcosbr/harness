"""Módulo de métricas de uso de tokens e cálculo de custo."""

from typing import Any, Dict, Optional
from harness.config import PRECOS_PADRAO


def extrair_metricas_usage(usage: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """
    Extrai métricas de tokens a partir do dicionário usageMetadata retornado pela API Gemini.
    Retorna dicionário com prompt, completion, total e cached (padrão 0 quando ausente).
    """
    if not usage:
        return {
            "prompt": 0,
            "completion": 0,
            "total": 0,
            "cached": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
        }

    prompt = usage.get("promptTokenCount", 0) or 0
    completion = usage.get("candidatesTokenCount", 0) or 0
    total = usage.get("totalTokenCount")
    if total is None:
        total = prompt + completion
    cached = usage.get("cachedContentTokenCount", 0) or 0

    return {
        "prompt": prompt,
        "completion": completion,
        "total": total,
        "cached": cached,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "cached_tokens": cached,
    }


def calcular_custo(
    prompt_total: int,
    cached_total: int,
    completion_total: int,
    precos: Optional[Dict[str, float]] = None
) -> float:
    """
    Calcula o custo estimado da execução aplicando o desconto de tokens em cache.
    Fórmula: ((prompt - cached) * input + cached * cache + completion * output) / 1_000_000
    """
    if precos is None:
        precos = PRECOS_PADRAO

    preco_input = precos.get("input", 0.0)
    preco_cache = precos.get("cache", 0.0)
    preco_output = precos.get("output", 0.0)

    prompt_nao_cacheado = max(0, prompt_total - cached_total)

    custo = (
        prompt_nao_cacheado * preco_input
        + cached_total * preco_cache
        + completion_total * preco_output
    ) / 1_000_000

    return custo
