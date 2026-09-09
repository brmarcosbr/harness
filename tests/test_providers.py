"""Testes unitários para conversões de providers (funções puras, sem rede)."""

import json
from harness.config import TOOL_DEFINITION_NEUTRA
from harness.providers import (
    gemini_tool_schema,
    mensagens_para_gemini_contents,
    normalizar_resposta_gemini,
    openai_tool_schema,
    mensagens_para_openai,
    normalizar_resposta_openai,
    modelos_a_tentar,
    ProviderResponse,
)


def test_gemini_tool_schema():
    schema = gemini_tool_schema()
    assert len(schema) == 1
    assert "function_declarations" in schema[0]
    fd = schema[0]["function_declarations"][0]
    assert fd["name"] == "executar_comando"
    assert fd["parameters"]["type"] == "OBJECT"
    assert "comando" in fd["parameters"]["properties"]
    assert fd["parameters"]["properties"]["comando"]["type"] == "STRING"


def test_openai_tool_schema():
    schema = openai_tool_schema()
    assert len(schema) == 1
    assert schema[0]["type"] == "function"
    fn = schema[0]["function"]
    assert fn["name"] == "executar_comando"
    assert fn["parameters"]["type"] == "object"
    assert "comando" in fn["parameters"]["properties"]


def test_mensagens_para_gemini_contents_completo():
    mensagens = [
        {"role": "user", "text": "liste os arquivos"},
        {
            "role": "model",
            "text": "Executando...",
            "tool_calls": [
                {"id": "call_0", "name": "executar_comando", "args": {"comando": "dir"}}
            ]
        },
        {
            "role": "tool",
            "tool_call_id": "call_0",
            "name": "executar_comando",
            "resultado": {"stdout": "arquivo.txt", "stderr": "", "codigo_saida": 0}
        }
    ]
    contents = mensagens_para_gemini_contents(mensagens)
    assert len(contents) == 3

    assert contents[0]["role"] == "user"
    assert contents[0]["parts"] == [{"text": "liste os arquivos"}]

    assert contents[1]["role"] == "model"
    assert len(contents[1]["parts"]) == 2
    assert contents[1]["parts"][0] == {"text": "Executando..."}
    assert contents[1]["parts"][1] == {
        "functionCall": {
            "name": "executar_comando",
            "args": {"comando": "dir"}
        }
    }

    assert contents[2]["role"] == "function"
    assert contents[2]["parts"][0]["functionResponse"]["name"] == "executar_comando"
    assert contents[2]["parts"][0]["functionResponse"]["response"]["stdout"] == "arquivo.txt"


def test_normalizar_resposta_gemini():
    data = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "Vou listar: "},
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
        ],
        "usageMetadata": {
            "promptTokenCount": 200,
            "candidatesTokenCount": 45,
            "totalTokenCount": 245,
            "cachedContentTokenCount": 50
        }
    }
    resp = normalizar_resposta_gemini(data, "gemini-2.5-flash")
    assert resp.text == "Vou listar: "
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0] == {
        "id": "call_0",
        "name": "executar_comando",
        "args": {"comando": "dir"}
    }
    assert resp.usage == {
        "prompt": 200,
        "completion": 45,
        "total": 245,
        "cached": 50
    }
    assert resp.modelo == "gemini-2.5-flash"


def test_mensagens_para_openai_completo():
    system_prompt = "Você é um assistente."
    mensagens = [
        {"role": "user", "text": "liste os arquivos"},
        {
            "role": "model",
            "text": "Executando...",
            "tool_calls": [
                {"id": "call_123", "name": "executar_comando", "args": {"comando": "dir"}}
            ]
        },
        {
            "role": "tool",
            "tool_call_id": "call_123",
            "name": "executar_comando",
            "resultado": {"stdout": "arquivo.txt", "stderr": "", "codigo_saida": 0}
        }
    ]
    openai_msgs = mensagens_para_openai(mensagens, system_prompt)
    assert len(openai_msgs) == 4

    assert openai_msgs[0] == {"role": "system", "content": system_prompt}
    assert openai_msgs[1] == {"role": "user", "content": "liste os arquivos"}
    assert openai_msgs[2]["role"] == "assistant"
    assert openai_msgs[2]["content"] == "Executando..."
    assert len(openai_msgs[2]["tool_calls"]) == 1
    tc = openai_msgs[2]["tool_calls"][0]
    assert tc["id"] == "call_123"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "executar_comando"
    assert json.loads(tc["function"]["arguments"]) == {"comando": "dir"}
    assert openai_msgs[3]["role"] == "tool"
    assert openai_msgs[3]["tool_call_id"] == "call_123"
    assert json.loads(openai_msgs[3]["content"]) == {"stdout": "arquivo.txt", "stderr": "", "codigo_saida": 0}


def test_normalizar_resposta_openai():
    data = {
        "id": "chatcmpl-xyz",
        "model": "deepseek-chat",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Comando enviado",
                    "tool_calls": [
                        {
                            "id": "call_abc",
                            "type": "function",
                            "function": {
                                "name": "executar_comando",
                                "arguments": '{"comando": "ls"}'
                            }
                        }
                    ]
                }
            }
        ],
        "usage": {
            "prompt_tokens": 150,
            "completion_tokens": 30,
            "total_tokens": 180,
            "prompt_cache_hit_tokens": 40
        }
    }
    resp = normalizar_resposta_openai(data, "deepseek-chat")
    assert resp.text == "Comando enviado"
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0] == {
        "id": "call_abc",
        "name": "executar_comando",
        "args": {"comando": "ls"}
    }
    assert resp.usage == {
        "prompt": 150,
        "completion": 30,
        "total": 180,
        "cached": 40
    }
    assert resp.modelo == "deepseek-chat"


def test_modelos_a_tentar_sem_duplicatas_e_com_fallback():
    modelos = modelos_a_tentar("gemini-2.5-flash", ["gemini-2.0-flash"])
    assert modelos == ["gemini-2.5-flash", "gemini-2.0-flash"]


def test_modelos_a_tentar_com_duplicata_no_fallback():
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

