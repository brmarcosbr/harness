"""Módulo de processamento de respostas da API Gemini e formatação de tool responses."""

from typing import Any, Dict, List, Optional, Tuple


def extrair_texto_e_tool_calls(
    candidate: Optional[Dict[str, Any]]
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Função pura que extrai o texto concatenado e a lista de function calls
    a partir de um candidate da resposta da API Gemini.
    Retorna a tupla (texto_gerado, lista_de_function_calls).
    """
    if not candidate or not isinstance(candidate, dict):
        return "", []

    content = candidate.get("content", {})
    if not isinstance(content, dict):
        return "", []

    parts = content.get("parts", [])
    if not isinstance(parts, list):
        return "", []

    textos = [p.get("text", "") for p in parts if isinstance(p, dict) and "text" in p]
    texto_gerado = "".join(textos)

    function_calls = [
        p["functionCall"]
        for p in parts
        if isinstance(p, dict) and "functionCall" in p and isinstance(p["functionCall"], dict)
    ]

    return texto_gerado, function_calls


def montar_function_response(name: str, resultado: Dict[str, Any]) -> Dict[str, Any]:
    """
    Função pura que monta uma part de functionResponse no formato oficial da API Gemini.
    """
    return {
        "functionResponse": {
            "name": name,
            "response": resultado
        }
    }
