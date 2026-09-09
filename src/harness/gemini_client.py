"""Cliente de comunicação com a API REST da Gemini via urllib (stdlib)."""

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from harness.config import API_BASE_URL, MODELOS_PADRAO, TOOL_DECLARATION


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


def carregar_env() -> None:
    """Carrega variáveis definidas em arquivos .env para os.environ (se não definidas)."""
    candidatos = [
        Path.cwd() / ".env",
        Path.home() / ".env"
    ]
    for env_path in candidatos:
        if env_path.is_file():
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        chave, val = line.split("=", 1)
                        chave = chave.strip()
                        val = val.strip().strip('"').strip("'")
                        if chave and chave not in os.environ and val:
                            os.environ[chave] = val
            except Exception:
                pass


def carregar_api_key() -> str:
    """Obtém a GEMINI_API_KEY da variável de ambiente ou de arquivos .env."""
    carregar_env()
    return os.environ.get("GEMINI_API_KEY", "").strip()


def chamar_gemini(
    api_key: str,
    modelo: str,
    contents: List[Dict[str, Any]],
    system_prompt: str,
    fallbacks: Optional[List[str]] = None,
    api_base_url: Optional[str] = None
) -> Tuple[Dict[str, Any], str]:
    """
    Envia a requisição para a API Gemini REST via urllib (zero dependências).
    Se o modelo retornar erro de 'not found' / 404, tenta o próximo modelo da lista sem duplicatas.
    Retorna a tupla (resposta_json, modelo_utilizado).
    """
    base_url = api_base_url or API_BASE_URL
    cand_fallbacks = fallbacks if fallbacks is not None else MODELOS_PADRAO
    modelos = modelos_a_tentar(modelo, cand_fallbacks)

    ultimo_erro = None

    for mod in modelos:
        endpoint = f"{base_url}/{mod}:generateContent?key={api_key}"
        payload = {
            "system_instruction": {
                "parts": [{"text": system_prompt}]
            },
            "contents": contents,
            "tools": [TOOL_DECLARATION]
        }

        req_body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=req_body,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data, mod

        except urllib.error.HTTPError as e:
            raw_err = e.read().decode("utf-8", errors="replace")
            msg = raw_err
            try:
                parsed = json.loads(raw_err)
                msg = parsed.get("error", {}).get("message", raw_err)
            except Exception:
                pass

            # Caso seja 404 / NOT_FOUND (modelo inexistente), tenta o próximo modelo da cadeia
            if e.code == 404 or "not found" in msg.lower():
                print(f"[Aviso] Modelo '{mod}' não encontrado na API. Tentando fallback...")
                ultimo_erro = f"HTTP {e.code}: {msg}"
                continue

            raise RuntimeError(f"Erro na API Gemini (HTTP {e.code}): {msg}") from e

        except urllib.error.URLError as e:
            raise RuntimeError(f"Erro de conexão com a API Gemini: {e.reason}") from e

    raise RuntimeError(
        f"Todos os modelos da cadeia de fallback falharam ({modelos}). Último erro: {ultimo_erro}"
    )
