"""Módulo de métricas de uso de tokens e cálculo de custo."""

from typing import Any, Dict, Optional


def extrair_metricas_usage(usage: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """
    Extrai métricas de tokens a partir do dicionário usage retornado por APIs de LLM.
    Retorna dicionário exclusivamente com prompt, completion, total e cached (padrão 0 quando ausente).
    """
    if not usage:
        return {
            "prompt": 0,
            "completion": 0,
            "total": 0,
            "cached": 0,
        }

    # Suporta convenção Gemini (camelCase) e OpenAI/DeepSeek (snake_case)
    prompt = usage.get("promptTokenCount")
    if prompt is None:
        prompt = usage.get("prompt_tokens", 0)
    prompt = prompt or 0

    completion = usage.get("candidatesTokenCount")
    if completion is None:
        completion = usage.get("completion_tokens", 0)
    completion = completion or 0

    total = usage.get("totalTokenCount")
    if total is None:
        total = usage.get("total_tokens")
    if total is None:
        total = prompt + completion
    total = total or 0

    cached = usage.get("cachedContentTokenCount")
    if cached is None:
        # Convenção DeepSeek direta: prompt_cache_hit_tokens
        cached = usage.get("prompt_cache_hit_tokens")
    if cached is None:
        # Convenção OpenAI: prompt_tokens_details.cached_tokens
        details = usage.get("prompt_tokens_details")
        if isinstance(details, dict):
            cached = details.get("cached_tokens")
    cached = cached or 0

    return {
        "prompt": prompt,
        "completion": completion,
        "total": total,
        "cached": cached,
    }


def calcular_custo(
    prompt_total: int,
    cached_total: int,
    completion_total: int,
    precos: Dict[str, float]
) -> float:
    """
    Calcula o custo estimado da execução aplicando o desconto de tokens em cache.
    Fórmula: ((prompt - cached) * input + cached * cache + completion * output) / 1_000_000
    """
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
