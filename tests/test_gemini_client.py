"""Testes unitários para o módulo gemini_client (funções puras, sem rede)."""

from harness.gemini_client import modelos_a_tentar


def test_modelos_a_tentar_sem_duplicatas_e_com_fallback():
    # Quando o modelo inicial não está nos fallbacks
    modelos = modelos_a_tentar("gemini-2.5-flash", ["gemini-2.0-flash"])
    assert modelos == ["gemini-2.5-flash", "gemini-2.0-flash"]


def test_modelos_a_tentar_com_duplicata_no_fallback():
    # Corrigindo a redundância do spike onde MODEL_NAME e FALLBACK_MODEL eram iguais
    modelos = modelos_a_tentar("gemini-2.5-flash", ["gemini-2.5-flash", "gemini-2.0-flash"])
    assert modelos == ["gemini-2.5-flash", "gemini-2.0-flash"]


def test_modelos_a_tentar_com_fallbacks_vazios():
    modelos = modelos_a_tentar("gemini-2.5-flash", [])
    assert modelos == ["gemini-2.5-flash"]
    modelos_none = modelos_a_tentar("gemini-2.5-flash", None)
    assert modelos_none == ["gemini-2.5-flash"]


def test_modelos_a_tentar_preserva_ordem():
    modelos = modelos_a_tentar(
        "modelo-a",
        ["modelo-b", "modelo-a", "modelo-c", "modelo-b"]
    )
    assert modelos == ["modelo-a", "modelo-b", "modelo-c"]
