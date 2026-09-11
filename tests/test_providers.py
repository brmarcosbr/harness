"""Testes unitários para conversões de providers (funções puras, sem rede)."""

import json
from typing import Any, Dict

from harness.tools import TOOLS
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
    declaracoes = schema[0]["function_declarations"]
    assert len(declaracoes) == 4
    nomes = [d["name"] for d in declaracoes]
    assert nomes == ["executar_comando", "ler_arquivo", "escrever_arquivo", "buscar_no_projeto"]
    
    # Valida parâmetros da primeira tool
    fd0 = declaracoes[0]
    assert fd0["name"] == "executar_comando"
    assert fd0["parameters"]["type"] == "OBJECT"
    assert "comando" in fd0["parameters"]["properties"]
    assert fd0["parameters"]["properties"]["comando"]["type"] == "STRING"


def test_openai_tool_schema():
    schema = openai_tool_schema()
    assert len(schema) == 4
    nomes = [item["function"]["name"] for item in schema]
    assert nomes == ["executar_comando", "ler_arquivo", "escrever_arquivo", "buscar_no_projeto"]

    fn0 = schema[0]["function"]
    assert fn0["name"] == "executar_comando"
    assert fn0["parameters"]["type"] == "object"
    assert "comando" in fn0["parameters"]["properties"]


def test_mensagens_para_gemini_contents_completo():
    mensagens = [
        {"role": "user", "text": "liste os arquivos"},
        {
            "role": "model",
            "text": "Executando...",
            "tool_calls": [
                {"id": "call_abc_123", "name": "executar_comando", "args": {"comando": "dir"}}
            ]
        },
        {
            "role": "tool",
            "tool_call_id": "call_abc_123",
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
            "id": "call_abc_123",
            "name": "executar_comando",
            "args": {"comando": "dir"}
        }
    }

    # No Gemini 3: resposta de tool usa role "user" com functionResponse contendo "id"
    assert contents[2]["role"] == "user"
    assert contents[2]["parts"][0]["functionResponse"]["name"] == "executar_comando"
    assert contents[2]["parts"][0]["functionResponse"]["id"] == "call_abc_123"
    assert contents[2]["parts"][0]["functionResponse"]["response"]["stdout"] == "arquivo.txt"


def test_normalizar_resposta_gemini_com_id_real():
    data = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "Vou listar: "},
                        {
                            "functionCall": {
                                "id": "call_gemini_real_999",
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
    resp = normalizar_resposta_gemini(data, "gemini-3.8-flash")
    assert resp.text == "Vou listar: "
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0] == {
        "id": "call_gemini_real_999",
        "name": "executar_comando",
        "args": {"comando": "dir"}
    }
    assert resp.usage == {
        "prompt": 200,
        "completion": 45,
        "total": 245,
        "cached": 50
    }
    assert resp.modelo == "gemini-3.8-flash"


def test_normalizar_resposta_gemini_fallback_id():
    # Quando o functionCall não traz "id", deve gerar fallback "call_0"
    data = {
        "candidates": [
            {
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
        ]
    }
    resp = normalizar_resposta_gemini(data, "gemini-3.8-flash")
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0]["id"] == "call_0"



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


def test_mensagens_para_gemini_contents_agrupa_tool_calls_adjacentes():
    mensagens = [
        {"role": "user", "text": "Execute duas ferramentas"},
        {
            "role": "model",
            "text": "Executando...",
            "tool_calls": [
                {"id": "call_1", "name": "executar_comando", "args": {"comando": "dir"}},
                {"id": "call_2", "name": "ler_arquivo", "args": {"caminho": "a.txt"}}
            ]
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "executar_comando",
            "resultado": {"stdout": "ok", "codigo_saida": 0}
        },
        {
            "role": "tool",
            "tool_call_id": "call_2",
            "name": "ler_arquivo",
            "resultado": {"sucesso": True, "conteudo": "hello"}
        }
    ]
    contents = mensagens_para_gemini_contents(mensagens)

    # user + model + 1 user único agrupando as 2 functionResponses
    assert len(contents) == 3
    assert contents[0]["role"] == "user"
    assert contents[1]["role"] == "model"
    assert contents[2]["role"] == "user"

    parts_user_tools = contents[2]["parts"]
    assert len(parts_user_tools) == 2
    assert "functionResponse" in parts_user_tools[0]
    assert parts_user_tools[0]["functionResponse"]["name"] == "executar_comando"
    assert parts_user_tools[0]["functionResponse"]["id"] == "call_1"
    assert "functionResponse" in parts_user_tools[1]
    assert parts_user_tools[1]["functionResponse"]["name"] == "ler_arquivo"
    assert parts_user_tools[1]["functionResponse"]["id"] == "call_2"

    # Invariante: nenhum par de contents consecutivos tem o mesmo role
    for i in range(len(contents) - 1):
        assert contents[i]["role"] != contents[i + 1]["role"], (
            f"Roles consecutivas iguais na posição {i}: {contents[i]['role']}"
        )


def test_montar_endpoint_gemini_sem_api_key_na_url():
    from harness.providers import montar_endpoint_gemini
    base = "https://generativelanguage.googleapis.com/v1beta/models"
    modelo = "gemini-3.8-flash"
    endpoint = montar_endpoint_gemini(base, modelo)

    assert endpoint == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent"
    assert "key=" not in endpoint
    assert "?" not in endpoint


def test_normalizar_resposta_openai_finish_reason_e_aviso():
    # 1. finish_reason="length"
    data_length = {
        "id": "chatcmpl-len",
        "model": "deepseek-chat",
        "choices": [
            {
                "index": 0,
                "finish_reason": "length",
                "message": {
                    "role": "assistant",
                    "content": "Texto cortado pela metade..."
                }
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 50, "total_tokens": 60}
    }
    resp_len = normalizar_resposta_openai(data_length, "deepseek-chat")
    assert resp_len.finish_reason == "length"
    assert resp_len.aviso == "Aviso de parada da API OpenAI/DeepSeek: length"
    assert resp_len.text == "Texto cortado pela metade..."

    # 2. finish_reason="content_filter"
    data_filter = {
        "id": "chatcmpl-flt",
        "model": "deepseek-chat",
        "choices": [
            {
                "index": 0,
                "finish_reason": "content_filter",
                "message": {
                    "role": "assistant",
                    "content": ""
                }
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10}
    }
    resp_flt = normalizar_resposta_openai(data_filter, "deepseek-chat")
    assert resp_flt.finish_reason == "content_filter"
    assert resp_flt.aviso == "Aviso de parada da API OpenAI/DeepSeek: content_filter"

    # 3. Resposta vazia com finish_reason="stop"
    data_stop_vazio = {
        "id": "chatcmpl-empty",
        "model": "deepseek-chat",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": ""
                }
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10}
    }
    resp_empty = normalizar_resposta_openai(data_stop_vazio, "deepseek-chat")
    assert resp_empty.finish_reason == "stop"
    assert resp_empty.aviso == "Resposta vazia com finish_reason: stop"




# ============================================================================
# Esforço de raciocínio e teto de saída enviados no corpo da requisição (rodada E1)
# ============================================================================

class _RespostaFake:
    """Context manager mínimo que devolve um ChatCompletion vazio, no formato do urlopen."""

    def __init__(self, payload: Dict[str, Any]):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _capturar_corpo_enviado(monkeypatch) -> Dict[str, Any]:
    """Instala um urlopen falso que registra o corpo JSON enviado ao endpoint."""
    import urllib.request
    capturado: Dict[str, Any] = {}

    def fake_urlopen(req, timeout=None):
        capturado["url"] = req.full_url
        capturado["corpo"] = json.loads(req.data.decode("utf-8"))
        return _RespostaFake({
            "model": capturado["corpo"].get("model"),
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return capturado


def test_corpo_openai_compat_declara_esforco_e_teto(monkeypatch):
    from harness.providers import OpenAICompatProvider

    capturado = _capturar_corpo_enviado(monkeypatch)
    provider = OpenAICompatProvider(api_key="chave-falsa", nome="deepseek")
    provider.gerar(mensagens=[{"role": "user", "text": "oi"}], system_prompt="sys")

    corpo = capturado["corpo"]
    assert corpo["model"] == "deepseek-flash"
    assert corpo["reasoning_effort"] == "high"
    assert corpo["max_tokens"] == 65536
    # O resto do corpo continua como estava
    assert corpo["messages"][0] == {"role": "system", "content": "sys"}
    assert "tools" in corpo


def test_corpo_respeita_override_de_ambiente(monkeypatch):
    from harness.providers import OpenAICompatProvider

    monkeypatch.setenv("HARNESS_REASONING_EFFORT", "low")
    monkeypatch.setenv("HARNESS_MAX_TOKENS", "4096")

    capturado = _capturar_corpo_enviado(monkeypatch)
    provider = OpenAICompatProvider(api_key="chave-falsa", nome="deepseek")
    provider.gerar(mensagens=[{"role": "user", "text": "oi"}], system_prompt="sys")

    assert capturado["corpo"]["reasoning_effort"] == "low"
    assert capturado["corpo"]["max_tokens"] == 4096

    # O valor efetivo exposto pelo provider acompanha o override (é o que vai para as métricas)
    assert provider.reasoning_effort == "low"
    assert provider.max_tokens == 4096


def test_override_invalido_cai_no_padrao_com_aviso(monkeypatch, capsys):
    from harness.config import MAX_TOKENS_PADRAO, REASONING_EFFORT_PADRAO, obter_max_tokens, obter_reasoning_effort

    monkeypatch.setenv("HARNESS_REASONING_EFFORT", "turbo")
    monkeypatch.setenv("HARNESS_MAX_TOKENS", "0")

    assert obter_reasoning_effort() == REASONING_EFFORT_PADRAO == "high"
    assert obter_max_tokens() == MAX_TOKENS_PADRAO == 65536

    avisos = capsys.readouterr().err
    assert "HARNESS_REASONING_EFFORT" in avisos
    assert "HARNESS_MAX_TOKENS" in avisos


def test_provider_gemini_nao_envia_campos_incompativeis(monkeypatch):
    from harness.providers import GeminiProvider

    capturado = _capturar_corpo_enviado(monkeypatch)
    provider = GeminiProvider(api_key="chave-falsa")
    provider.gerar(mensagens=[{"role": "user", "text": "oi"}], system_prompt="sys")

    corpo = capturado["corpo"]
    # A API REST do Gemini recusa reasoning_effort/max_tokens: os campos não vão no corpo
    assert "reasoning_effort" not in corpo
    assert "max_tokens" not in corpo
    assert provider.reasoning_effort is None
    assert provider.max_tokens is None


# ============================================================================
# Rodada E1/Gemini: parâmetros de geração declarados no corpo (item 1)
# ============================================================================

def _erro_http(url: str, code: int, mensagem: str):
    import io
    import urllib.error

    corpo = json.dumps({"error": {"message": mensagem, "code": code, "status": "INVALID_ARGUMENT"}}).encode("utf-8")
    return urllib.error.HTTPError(url, code, mensagem, {}, io.BytesIO(corpo))


def _capturar_corpo_gemini(monkeypatch, respostas_falhas: int = 0):
    """
    Instala um urlopen falso para o endpoint Gemini que registra TODOS os corpos enviados.
    As `respostas_falhas` primeiras chamadas levantam HTTP 400 nomeando o thinkingConfig.
    """
    import urllib.request

    corpo_ok = {
        "candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 3, "totalTokenCount": 13},
    }
    corpos = []

    def fake_urlopen(req, timeout=None):
        corpos.append(json.loads(req.data.decode("utf-8")))
        if len(corpos) <= respostas_falhas:
            raise _erro_http(req.full_url, 400, 'Unknown name "thinkingConfig": Cannot find field.')
        return _RespostaFake(corpo_ok)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return corpos


def test_gemini_envia_max_output_tokens_e_thinking_level(monkeypatch):
    from harness.config import GEMINI_MAX_OUTPUT_TOKENS_PADRAO, GEMINI_THINKING_LEVEL_PADRAO
    from harness.providers import GeminiProvider

    for variavel in ("HARNESS_GEMINI_MAX_OUTPUT_TOKENS", "HARNESS_GEMINI_THINKING_LEVEL"):
        monkeypatch.delenv(variavel, raising=False)

    corpos = _capturar_corpo_gemini(monkeypatch)
    provider = GeminiProvider(api_key="chave-falsa")
    provider.gerar(mensagens=[{"role": "user", "text": "oi"}], system_prompt="sys")

    config = corpos[0]["generationConfig"]
    assert config["maxOutputTokens"] == GEMINI_MAX_OUTPUT_TOKENS_PADRAO == 65536
    assert config["thinkingConfig"]["thinkingLevel"] == GEMINI_THINKING_LEVEL_PADRAO == "medium"
    # O resto do corpo continua como estava
    assert corpos[0]["system_instruction"]["parts"][0]["text"] == "sys"
    assert "contents" in corpos[0] and "tools" in corpos[0]


def test_gemini_respeita_override_de_ambiente(monkeypatch):
    from harness.providers import GeminiProvider

    monkeypatch.setenv("HARNESS_GEMINI_MAX_OUTPUT_TOKENS", "8192")
    monkeypatch.setenv("HARNESS_GEMINI_THINKING_LEVEL", "high")

    corpos = _capturar_corpo_gemini(monkeypatch)
    provider = GeminiProvider(api_key="chave-falsa")
    provider.gerar(mensagens=[{"role": "user", "text": "oi"}], system_prompt="sys")

    config = corpos[0]["generationConfig"]
    assert config["maxOutputTokens"] == 8192
    assert config["thinkingConfig"]["thinkingLevel"] == "high"
    # O efetivo acompanha o override
    efetivos = provider.parametros_de_geracao_efetivos()
    assert efetivos["max_output_tokens"] == 8192
    assert efetivos["thinking_level"] == "high"


def test_gemini_override_invalido_cai_no_padrao_com_aviso(monkeypatch, capsys):
    from harness.config import (
        GEMINI_MAX_OUTPUT_TOKENS_PADRAO,
        GEMINI_THINKING_LEVEL_PADRAO,
        obter_gemini_max_output_tokens,
        obter_gemini_thinking_level,
    )

    monkeypatch.setenv("HARNESS_GEMINI_MAX_OUTPUT_TOKENS", "0")
    monkeypatch.setenv("HARNESS_GEMINI_THINKING_LEVEL", "turbo")

    assert obter_gemini_max_output_tokens() == GEMINI_MAX_OUTPUT_TOKENS_PADRAO
    assert obter_gemini_thinking_level() == GEMINI_THINKING_LEVEL_PADRAO
    avisos = capsys.readouterr().err
    assert "HARNESS_GEMINI_MAX_OUTPUT_TOKENS" in avisos
    assert "HARNESS_GEMINI_THINKING_LEVEL" in avisos


def test_gemini_reenvia_sem_thinking_quando_a_api_recusa(monkeypatch, capsys):
    """
    A recusa do bloco de thinking não pode ser silenciosa nem derrubar a execução: a chamada é
    refeita sem o campo e o valor efetivo fica registrado como NÃO declarado.
    """
    from harness.providers import GeminiProvider

    corpos = _capturar_corpo_gemini(monkeypatch, respostas_falhas=1)
    provider = GeminiProvider(api_key="chave-falsa")
    resp = provider.gerar(mensagens=[{"role": "user", "text": "oi"}], system_prompt="sys")

    assert len(corpos) == 2
    assert "thinkingConfig" in corpos[0]["generationConfig"]
    assert "thinkingConfig" not in corpos[1]["generationConfig"]
    # O teto continua declarado na segunda tentativa
    assert corpos[1]["generationConfig"]["maxOutputTokens"] == 65536
    assert resp.text == "ok"

    avisos = capsys.readouterr().err
    assert "recusou generationConfig.thinkingConfig" in avisos

    efetivos = provider.parametros_de_geracao_efetivos()
    assert efetivos["thinking_level"] is None
    assert efetivos["thinking_recusado"]
    assert efetivos["max_output_tokens"] == 65536
