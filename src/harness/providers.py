"""Módulo de abstração de provedores de LLM (Gemini e OpenAI-compatível/DeepSeek)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional
from harness.config import (
    DEFAULT_BASE_URLS,
    DEFAULT_FALLBACKS,
    DEFAULT_MODELS,
    PROVIDER_PRECOS,
)
from harness.errors import HarnessError
from harness.tools import TOOLS
from harness.usage import extrair_metricas_usage


@dataclass
class ProviderResponse:
    """Resposta normalizada de qualquer provedor LLM."""
    text: str
    tool_calls: List[Dict[str, Any]]  # Lista de {"id": str, "name": str, "args": dict}
    usage: Dict[str, int]             # {"prompt": int, "completion": int, "total": int, "cached": int}
    modelo: str
    finish_reason: Optional[str] = None
    aviso: Optional[str] = None


def montar_endpoint_gemini(base_url: str, modelo: str) -> str:
    """Monta a URL do endpoint Gemini sem expor a API key como parâmetro de query."""
    return f"{base_url.rstrip('/')}/{modelo}:generateContent"


class Provider(ABC):
    """Classe base abstrata para provedores de LLM no Agent Harness."""

    nome: str
    modelo_ativo: str
    precos: Dict[str, float]

    @abstractmethod
    def gerar(self, mensagens: List[Dict[str, Any]], system_prompt: str) -> ProviderResponse:
        """Gera uma resposta do modelo a partir do histórico neutro de mensagens."""
        pass

    @abstractmethod
    def tools_schema(self) -> List[Dict[str, Any]]:
        """Retorna o schema das ferramentas no formato específico do provedor."""
        pass


# ============================================================================
# Funções puras de conversão e normalização — GEMINI
# ============================================================================

def modelos_a_tentar(modelo_inicial: str, fallbacks: Optional[List[str]] = None) -> List[str]:
    """
    Função pura que retorna a lista de modelos a tentar em ordem,
    iniciando pelo modelo_inicial e incluindo os fallbacks sem duplicatas.
    """
    lista = [modelo_inicial]
    if fallbacks:
        for mod in fallbacks:
            if mod not in lista:
                lista.append(mod)
    return lista


def gemini_tool_schema(tools: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Gera o schema de tools no formato da API Gemini para todas as tools informadas."""
    lista_tools = tools if tools is not None else TOOLS
    declaracoes = []

    for tool in lista_tools:
        params = tool.get("parameters", {})
        props = params.get("properties", {})
        gemini_props = {
            k: {
                "type": v["type"].upper(),
                "description": v.get("description", "")
            }
            for k, v in props.items()
        }
        declaracoes.append({
            "name": tool["name"],
            "description": tool["description"],
            "parameters": {
                "type": "OBJECT",
                "properties": gemini_props,
                "required": params.get("required", [])
            }
        })

    return [
        {
            "function_declarations": declaracoes
        }
    ]


def mensagens_para_gemini_contents(mensagens: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Função pura que converte mensagens no formato neutro do harness para o formato contents da Gemini.
    - user: {"role": "user", "parts": [{"text": ...}]}
    - model: {"role": "model", "parts": [{"text": ...}, {"functionCall": {"id": ..., "name": ..., "args": ...}}]}
    - tool: {"role": "user", "parts": [{"functionResponse": {"name": ..., "response": ..., "id": ...}}]}
    """
    contents: List[Dict[str, Any]] = []

    for msg in mensagens:
        role = msg.get("role")

        if role == "user":
            contents.append({
                "role": "user",
                "parts": [{"text": msg.get("text", "")}]
            })

        elif role == "model":
            parts: List[Dict[str, Any]] = []
            text = msg.get("text", "")
            if text:
                parts.append({"text": text})

            for tc in msg.get("tool_calls", []):
                fc_part: Dict[str, Any] = {
                    "name": tc["name"],
                    "args": tc.get("args", {})
                }
                if tc.get("id"):
                    fc_part["id"] = tc["id"]

                part_dict: Dict[str, Any] = {
                    "functionCall": fc_part
                }
                if tc.get("thought_signature"):
                    part_dict["thoughtSignature"] = tc["thought_signature"]

                parts.append(part_dict)

            contents.append({
                "role": "model",
                "parts": parts
            })

        elif role == "tool":
            fr_part: Dict[str, Any] = {
                "name": msg.get("name", ""),
                "response": msg.get("resultado", {})
            }
            if msg.get("tool_call_id"):
                fr_part["id"] = msg["tool_call_id"]

            part_obj = {"functionResponse": fr_part}

            # Agrupa respostas de ferramentas adjacentes no mesmo bloco user
            # para evitar conteúdos consecutivos com role 'user' (HTTP 400 na API Gemini)
            if (
                contents
                and contents[-1].get("role") == "user"
                and any("functionResponse" in p for p in contents[-1].get("parts", []))
            ):
                contents[-1]["parts"].append(part_obj)
            else:
                contents.append({
                    "role": "user",
                    "parts": [part_obj]
                })

    return contents


def normalizar_resposta_gemini(data: Dict[str, Any], modelo: str) -> ProviderResponse:
    """
    Função pura que extrai e normaliza texto, tool calls e métricas de usage da resposta da Gemini.
    Extrai o ID real retornado pela API (fc.get("id")), usando fallback ordenado ("call_0", etc.) se ausente.
    Preserva thoughtSignature quando emitido por modelos Gemini 3.
    """
    candidates = data.get("candidates", [])
    if not candidates:
        usage = extrair_metricas_usage(data.get("usageMetadata", {}))
        return ProviderResponse(
            text="",
            tool_calls=[],
            usage=usage,
            modelo=modelo,
            finish_reason="NO_CANDIDATES",
            aviso="Nenhum candidato retornado pela API Gemini"
        )

    candidate = candidates[0]
    finish_reason = candidate.get("finishReason")
    content = candidate.get("content", {})
    parts = content.get("parts", [])

    textos = [p.get("text", "") for p in parts if isinstance(p, dict) and "text" in p]
    texto = "".join(textos)

    tool_calls: List[Dict[str, Any]] = []
    fc_index = 0
    for p in parts:
        if isinstance(p, dict) and "functionCall" in p:
            fc = p["functionCall"]
            call_id = fc.get("id") or f"call_{fc_index}"
            tc_dict: Dict[str, Any] = {
                "id": call_id,
                "name": fc.get("name", ""),
                "args": fc.get("args", {})
            }
            if p.get("thoughtSignature"):
                tc_dict["thought_signature"] = p["thoughtSignature"]
            elif fc.get("thoughtSignature"):
                tc_dict["thought_signature"] = fc["thoughtSignature"]

            tool_calls.append(tc_dict)
            fc_index += 1

    aviso = None
    if finish_reason in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}:
        aviso = f"Bloqueio de segurança da API Gemini: {finish_reason}"
    elif not texto and not tool_calls and finish_reason:
        aviso = f"Resposta vazia com finishReason: {finish_reason}"

    usage = extrair_metricas_usage(data.get("usageMetadata", {}))
    return ProviderResponse(
        text=texto,
        tool_calls=tool_calls,
        usage=usage,
        modelo=modelo,
        finish_reason=finish_reason,
        aviso=aviso
    )



# ============================================================================
# Funções puras de conversão e normalização — OPENAI-COMPAT
# ============================================================================

def openai_tool_schema(tools: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Gera o schema de tools no formato OpenAI para todas as tools informadas."""
    lista_tools = tools if tools is not None else TOOLS
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool.get("parameters", {})
            }
        }
        for tool in lista_tools
    ]


def mensagens_para_openai(
    mensagens: List[Dict[str, Any]],
    system_prompt: str
) -> List[Dict[str, Any]]:
    """
    Função pura que converte mensagens neutras para a lista de messages da API OpenAI/DeepSeek.
    Inclui a mensagem de sistema como primeira mensagem.
    """
    openai_msgs: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt}
    ]

    for msg in mensagens:
        role = msg.get("role")

        if role == "user":
            openai_msgs.append({
                "role": "user",
                "content": msg.get("text", "")
            })

        elif role == "model":
            asst_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": msg.get("text") or None
            }

            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                asst_msg["tool_calls"] = [
                    {
                        "id": tc.get("id", f"call_{i}"),
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc.get("args", {}))
                        }
                    }
                    for i, tc in enumerate(tool_calls)
                ]

            openai_msgs.append(asst_msg)

        elif role == "tool":
            openai_msgs.append({
                "role": "tool",
                "tool_call_id": msg.get("tool_call_id", ""),
                "content": json.dumps(msg.get("resultado", {}))
            })

    return openai_msgs


def normalizar_resposta_openai(data: Dict[str, Any], modelo: str) -> ProviderResponse:
    """
    Função pura que normaliza a resposta de ChatCompletion da OpenAI/DeepSeek.
    """
    choices = data.get("choices", [])
    if not choices:
        usage = extrair_metricas_usage(data.get("usage", {}))
        return ProviderResponse(text="", tool_calls=[], usage=usage, modelo=modelo)

    choice = choices[0]
    message = choice.get("message", {})
    text = message.get("content") or ""

    raw_tool_calls = message.get("tool_calls") or []
    tool_calls: List[Dict[str, Any]] = []

    for i, tc in enumerate(raw_tool_calls):
        call_id = tc.get("id") or f"call_{i}"
        fn = tc.get("function", {})
        fn_name = fn.get("name", "")
        raw_args = fn.get("arguments", "{}")

        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except Exception:
            args = {}

        tool_calls.append({
            "id": call_id,
            "name": fn_name,
            "args": args if isinstance(args, dict) else {}
        })

    usage = extrair_metricas_usage(data.get("usage", {}))
    modelo_real = data.get("model") or modelo

    return ProviderResponse(
        text=text,
        tool_calls=tool_calls,
        usage=usage,
        modelo=modelo_real
    )


# ============================================================================
# Implementações dos Providers
# ============================================================================

class GeminiProvider(Provider):
    """Provedor para a API Google Gemini REST."""

    def __init__(
        self,
        api_key: str,
        modelo: Optional[str] = None,
        base_url: Optional[str] = None,
        fallbacks: Optional[List[str]] = None
    ):
        self.nome = "gemini"
        self.api_key = api_key
        self.modelo_ativo = modelo or DEFAULT_MODELS["gemini"]
        self.base_url = (base_url or DEFAULT_BASE_URLS["gemini"]).rstrip("/")
        self.fallbacks = fallbacks if fallbacks is not None else DEFAULT_FALLBACKS["gemini"]
        self.precos = PROVIDER_PRECOS["gemini"]

    def tools_schema(self) -> List[Dict[str, Any]]:
        return gemini_tool_schema()

    def gerar(self, mensagens: List[Dict[str, Any]], system_prompt: str) -> ProviderResponse:
        modelos = modelos_a_tentar(self.modelo_ativo, self.fallbacks)
        contents = mensagens_para_gemini_contents(mensagens)
        ultimo_erro = None

        for mod in modelos:
            endpoint = montar_endpoint_gemini(self.base_url, mod)
            payload = {
                "system_instruction": {
                    "parts": [{"text": system_prompt}]
                },
                "contents": contents,
                "tools": self.tools_schema()
            }

            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["x-goog-api-key"] = self.api_key

            req_body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                endpoint,
                data=req_body,
                headers=headers,
                method="POST"
            )

            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    self.modelo_ativo = mod
                    return normalizar_resposta_gemini(data, mod)

            except urllib.error.HTTPError as e:
                raw_err = e.read().decode("utf-8", errors="replace")
                msg = raw_err
                try:
                    parsed = json.loads(raw_err)
                    msg = parsed.get("error", {}).get("message", raw_err)
                except Exception:
                    pass

                if e.code == 404 or "not found" in msg.lower():
                    print(f"[Aviso] Modelo '{mod}' não encontrado na API. Tentando fallback...")
                    ultimo_erro = f"HTTP {e.code}: {msg}"
                    continue

                raise HarnessError(f"Erro na API Gemini (HTTP {e.code}): {msg}") from e

            except urllib.error.URLError as e:
                raise HarnessError(f"Erro de conexão com a API Gemini: {e.reason}") from e
            except Exception as e:
                raise HarnessError(f"Erro inesperado na chamada ao Gemini: {e}") from e

        raise HarnessError(
            f"Todos os modelos da cadeia de fallback falharam ({modelos}). Último erro: {ultimo_erro}"
        )


class OpenAICompatProvider(Provider):
    """Provedor para APIs compatíveis com o formato OpenAI (DeepSeek, OpenRouter, OpenAI)."""

    def __init__(
        self,
        api_key: str,
        modelo: Optional[str] = None,
        base_url: Optional[str] = None,
        nome: str = "openai",
        precos: Optional[Dict[str, float]] = None
    ):
        self.nome = nome
        self.api_key = api_key
        self.modelo_ativo = modelo or DEFAULT_MODELS.get(nome, DEFAULT_MODELS["openai"])
        self.base_url = (base_url or DEFAULT_BASE_URLS.get(nome, DEFAULT_BASE_URLS["openai"])).rstrip("/")
        self.precos = precos or PROVIDER_PRECOS.get(nome, PROVIDER_PRECOS["openai"])

    def tools_schema(self) -> List[Dict[str, Any]]:
        return openai_tool_schema()

    def gerar(self, mensagens: List[Dict[str, Any]], system_prompt: str) -> ProviderResponse:
        endpoint = f"{self.base_url}/chat/completions"
        openai_msgs = mensagens_para_openai(mensagens, system_prompt)

        payload = {
            "model": self.modelo_ativo,
            "messages": openai_msgs,
            "tools": self.tools_schema()
        }

        req_body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=req_body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            },
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return normalizar_resposta_openai(data, self.modelo_ativo)

        except urllib.error.HTTPError as e:
            raw_err = e.read().decode("utf-8", errors="replace")
            msg = raw_err
            try:
                parsed = json.loads(raw_err)
                msg = parsed.get("error", {}).get("message", raw_err)
            except Exception:
                pass

            raise HarnessError(f"Erro na API {self.nome.upper()} (HTTP {e.code}): {msg}") from e

        except urllib.error.URLError as e:
            raise HarnessError(f"Erro de conexão com a API {self.nome.upper()}: {e.reason}") from e
        except Exception as e:
            raise HarnessError(f"Erro inesperado na chamada a {self.nome.upper()}: {e}") from e


def criar_provider(
    provider_name: str,
    api_key: str,
    modelo: Optional[str] = None,
    base_url: Optional[str] = None
) -> Provider:
    """
    Factory que instancia o provider configurado.
    Nomes suportados: 'gemini', 'openai', 'deepseek'.
    """
    p_name = (provider_name or "gemini").strip().lower()

    if p_name == "gemini":
        return GeminiProvider(
            api_key=api_key,
            modelo=modelo,
            base_url=base_url
        )
    elif p_name in ("openai", "deepseek"):
        mod = modelo or DEFAULT_MODELS[p_name]
        b_url = base_url or DEFAULT_BASE_URLS[p_name]
        precos = PROVIDER_PRECOS[p_name]
        return OpenAICompatProvider(
            api_key=api_key,
            modelo=mod,
            base_url=b_url,
            nome=p_name,
            precos=precos
        )
    else:
        raise HarnessError(
            f"Provider '{provider_name}' não suportado. Opções válidas: 'gemini', 'openai', 'deepseek'."
        )
