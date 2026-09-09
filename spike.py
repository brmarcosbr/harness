"""
Spike de Validação: Agent Harness Mínimo com Gemini API e Tool Use.

Requisitos atendidos:
- Zero dependências externas (stdlib: urllib, json, subprocess, os, sys, pathlib, argparse).
- Leitura de GEMINI_API_KEY via variável de ambiente ou arquivo .env.
- Modelo configurável no topo com fallback automático para gemini-2.5-flash.
- Loop multi-turno de no máximo 3 turnos.
- Tool use em function calling no formato oficial da API Gemini: 'executar_comando' (subprocess Windows, cwd=atual, timeout 30s).
- Métricas a cada turno: número do turno, prompt tokens, completion tokens e tamanho do texto gerado.
- Tratamento de timeout de comando e erros de API.
- Parâmetro CLI --tarefa com valor default.
- Resumo final com turnos e total de tokens de entrada e saída.
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Constantes de configuração do modelo
# Prefere o modelo Flash mais recente na API, com fallback automático
MODEL_NAME = "gemini-2.5-flash"
FALLBACK_MODEL = "gemini-2.5-flash"
SECONDARY_FALLBACK = "gemini-2.0-flash"

MAX_TURNS = 3
COMMAND_TIMEOUT_SECONDS = 30
API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

SYSTEM_PROMPT = (
    "Você é um assistente operacional em um ambiente Windows. "
    "Você tem acesso à ferramenta 'executar_comando' para executar comandos no sistema. "
    "Ao usar 'executar_comando', forneça comandos compatíveis com o terminal Windows (PowerShell ou CMD). "
    "Cumpra os pedidos do usuário de forma concisa e direta."
)

TOOL_DECLARATION = {
    "function_declarations": [
        {
            "name": "executar_comando",
            "description": (
                "Executa um comando de linha de comando no terminal do Windows (PowerShell/CMD) "
                "no diretório atual de trabalho. Retorna stdout, stderr e o código de saída."
            ),
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "comando": {
                        "type": "STRING",
                        "description": "O comando de terminal a ser executado no Windows."
                    }
                },
                "required": ["comando"]
            }
        }
    ]
}


def carregar_api_key() -> str:
    """Obtém a GEMINI_API_KEY da variável de ambiente ou de arquivos .env."""
    if key := os.environ.get("GEMINI_API_KEY"):
        if key.strip():
            return key.strip()

    # Busca em .env no diretório atual ou no diretório do usuário
    candidatos = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parent / ".env",
        Path.home() / ".env"
    ]

    for env_path in candidatos:
        if env_path.is_file():
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if line.startswith("GEMINI_API_KEY="):
                            val = line.split("=", 1)[1].strip().strip('"').strip("'")
                            if val:
                                return val
            except Exception:
                pass

    return ""


def executar_comando(comando: str) -> Dict[str, Any]:
    """
    Executa o comando em subprocess no Windows com timeout de 30s.
    Captura stdout, stderr e código de saída.
    """
    try:
        resultado = subprocess.run(
            comando,
            shell=True,
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            encoding="utf-8",
            errors="replace"
        )
        return {
            "stdout": resultado.stdout,
            "stderr": resultado.stderr,
            "codigo_saida": resultado.returncode
        }
    except subprocess.TimeoutExpired:
        return {
            "stdout": "",
            "stderr": f"Erro: Timeout de execução atingido ({COMMAND_TIMEOUT_SECONDS}s).",
            "codigo_saida": -1
        }
    except Exception as e:
        return {
            "stdout": "",
            "stderr": f"Erro inesperado ao executar comando: {e}",
            "codigo_saida": -1
        }


def chamar_gemini(
    api_key: str,
    modelo: str,
    contents: List[Dict[str, Any]],
    system_prompt: str
) -> Tuple[Dict[str, Any], str]:
    """
    Envia a requisição para a API Gemini REST via urllib (zero dependências).
    Se o modelo retornar erro de 'not found' / 404, tenta o fallback configurado.
    Retorna a tupla (resposta_json, modelo_utilizado).
    """
    modelos_a_tentar = [modelo]
    # Cadeia de fallback em caso de modelo inexistente
    for fb in [FALLBACK_MODEL, SECONDARY_FALLBACK]:
        if fb not in modelos_a_tentar:
            modelos_a_tentar.append(fb)

    ultimo_erro = None

    for mod in modelos_a_tentar:
        endpoint = f"{API_BASE_URL}/{mod}:generateContent?key={api_key}"
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
        f"Todos os modelos da cadeia de fallback falharam ({modelos_a_tentar}). Último erro: {ultimo_erro}"
    )


def executar_loop(tarefa: str, api_key: str):
    """Executa o loop de harness multi-turno com medição de tokens e execução de tools."""
    print("=" * 60)
    print("INICIANDO SPIKE: AGENT HARNESS (GEMINI)")
    print("=" * 60)
    print(f"Tarefa: {tarefa}")
    print(f"Modelo configurado: {MODEL_NAME}")
    print(f"Diretório atual: {os.getcwd()}")
    print("-" * 60)

    contents: List[Dict[str, Any]] = [
        {
            "role": "user",
            "parts": [{"text": tarefa}]
        }
    ]

    modelo_ativo = MODEL_NAME
    total_prompt_tokens = 0
    total_completion_tokens = 0
    turnos_usados = 0

    for turno in range(1, MAX_TURNS + 1):
        turnos_usados = turno
        print(f"\n>>> TURNO {turno} / {MAX_TURNS}")

        # Chamada ao modelo
        try:
            resp, modelo_ativo = chamar_gemini(api_key, modelo_ativo, contents, SYSTEM_PROMPT)
        except Exception as e:
            print(f"[ERRO DE API] Falha na chamada da API: {e}", file=sys.stderr)
            sys.exit(1)

        # Extração de métricas de tokens (Usage)
        usage = resp.get("usageMetadata", {})
        prompt_tokens = usage.get("promptTokenCount", 0)
        completion_tokens = usage.get("candidatesTokenCount", 0)

        total_prompt_tokens += prompt_tokens
        total_completion_tokens += completion_tokens

        # Processamento da resposta
        candidates = resp.get("candidates", [])
        if not candidates:
            print("[Aviso] Nenhuma resposta retornada pelo modelo.")
            break

        candidate = candidates[0]
        content = candidate.get("content", {})
        parts = content.get("parts", [])

        # Extração de texto e tool calls
        textos = [p.get("text", "") for p in parts if "text" in p]
        texto_gerado = "".join(textos)
        tamanho_texto = len(texto_gerado)

        # Métricas do turno
        print(f"Prompt tokens: {prompt_tokens}")
        print(f"Completion tokens: {completion_tokens}")
        print(f"Tamanho do texto gerado: {tamanho_texto} caracteres")

        function_calls = [p["functionCall"] for p in parts if "functionCall" in p]

        if function_calls:
            # Anexa a resposta do modelo ao histórico da conversa
            contents.append({"role": "model", "parts": parts})

            # Executa cada chamada de ferramenta e anexa os resultados
            for fc in function_calls:
                func_name = fc.get("name")
                func_args = fc.get("args", {})

                print(f"\n[Tool Call] Função: '{func_name}'")
                if func_name == "executar_comando":
                    comando = func_args.get("comando", "")
                    print(f"[Tool Exec] Executando comando: {comando}")
                    resultado_tool = executar_comando(comando)
                    print(
                        f"[Tool Output] Código: {resultado_tool['codigo_saida']} | "
                        f"Stdout: {len(resultado_tool['stdout'])} chars | "
                        f"Stderr: {len(resultado_tool['stderr'])} chars"
                    )

                    # Anexa resposta da função ao histórico
                    contents.append({
                        "role": "function",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": func_name,
                                    "response": resultado_tool
                                }
                            }
                        ]
                    })
                else:
                    print(f"[Tool Error] Função desconhecida: '{func_name}'")
                    contents.append({
                        "role": "function",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": func_name,
                                    "response": {"erro": f"Ferramenta desconhecida: {func_name}"}
                                }
                            }
                        ]
                    })
        else:
            # Resposta final de texto
            print(f"\n[Resposta Final do Modelo]:\n{texto_gerado.strip()}")
            break

    # Resumo final
    print("\n" + "=" * 60)
    print("RESUMO DA EXECUÇÃO")
    print("=" * 60)
    print(f"Turnos utilizados: {turnos_usados} de {MAX_TURNS}")
    print(f"Total Prompt Tokens: {total_prompt_tokens}")
    print(f"Total Completion Tokens: {total_completion_tokens}")
    print(f"Total Geral de Tokens: {total_prompt_tokens + total_completion_tokens}")
    print(f"Modelo final: {modelo_ativo}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Spike de validação técnica do Agent Harness (Gemini API + Tool Use)."
    )
    parser.add_argument(
        "--tarefa",
        type=str,
        default="liste os arquivos desta pasta e crie um arquivo ola.txt com o conteúdo 'spike ok'",
        help="Instrução a ser executada pelo agente."
    )
    args = parser.parse_args()

    api_key = carregar_api_key()
    if not api_key:
        print(
            "ERRO: Chave de API da Gemini não encontrada!\n"
            "Defina a variável de ambiente GEMINI_API_KEY ou configure-a em um arquivo .env:\n"
            "  GEMINI_API_KEY=sua_chave_aqui\n",
            file=sys.stderr
        )
        sys.exit(1)

    executar_loop(args.tarefa, api_key)


if __name__ == "__main__":
    main()
