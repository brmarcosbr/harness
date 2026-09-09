"""Testes unitários para o módulo resposta (funções puras, sem rede)."""

from harness.resposta import extrair_texto_e_tool_calls, montar_function_response


def test_extrair_candidate_so_com_texto():
    candidate = {
        "content": {
            "parts": [
                {"text": "Olá mundo!"},
                {"text": " Segunda parte."}
            ],
            "role": "model"
        }
    }
    texto, calls = extrair_texto_e_tool_calls(candidate)
    assert texto == "Olá mundo! Segunda parte."
    assert calls == []


def test_extrair_candidate_so_com_function_call():
    candidate = {
        "content": {
            "parts": [
                {
                    "functionCall": {
                        "name": "executar_comando",
                        "args": {"comando": "dir"}
                    }
                }
            ],
            "role": "model"
        }
    }
    texto, calls = extrair_texto_e_tool_calls(candidate)
    assert texto == ""
    assert len(calls) == 1
    assert calls[0]["name"] == "executar_comando"
    assert calls[0]["args"] == {"comando": "dir"}


def test_extrair_candidate_com_ambos():
    candidate = {
        "content": {
            "parts": [
                {"text": "Executando o comando a seguir: "},
                {
                    "functionCall": {
                        "name": "executar_comando",
                        "args": {"comando": "dir"}
                    }
                }
            ],
            "role": "model"
        }
    }
    texto, calls = extrair_texto_e_tool_calls(candidate)
    assert texto == "Executando o comando a seguir: "
    assert len(calls) == 1
    assert calls[0]["name"] == "executar_comando"


def test_extrair_candidate_vazio():
    for vazio in [{}, None, {"content": {}}, {"content": {"parts": []}}]:
        texto, calls = extrair_texto_e_tool_calls(vazio)
        assert texto == ""
        assert calls == []


def test_montar_function_response():
    resultado = {"stdout": "ok", "stderr": "", "codigo_saida": 0}
    part = montar_function_response("executar_comando", resultado)
    assert part == {
        "functionResponse": {
            "name": "executar_comando",
            "response": resultado
        }
    }
