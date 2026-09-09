"""Módulo do loop multi-turno do Agent Harness."""

import os
import sys
from typing import Any, Dict, List, Optional
from harness.config import (
    MAX_TURNS,
    MODELOS_PADRAO,
    PRECOS_PADRAO,
    SYSTEM_PROMPT,
)
from harness.gemini_client import chamar_gemini
from harness.resposta import extrair_texto_e_tool_calls, montar_function_response
from harness.tools import executar_comando
from harness.usage import calcular_custo, extrair_metricas_usage


def executar_loop(
    tarefa: str,
    api_key: str,
    modelo_inicial: Optional[str] = None,
    max_turns: int = MAX_TURNS,
    api_base_url: Optional[str] = None
):
    """
    Executa o loop de harness multi-turno com medição de tokens e execução de tools.
    Preserva rigorosamente o comportamento e as mensagens originais do spike.
    """
    modelo_ativo = modelo_inicial or MODELOS_PADRAO[0]

    print("=" * 60)
    print("INICIANDO SPIKE: AGENT HARNESS (GEMINI)")
    print("=" * 60)
    print(f"Tarefa: {tarefa}")
    print(f"Modelo configurado: {modelo_ativo}")
    print(f"Diretório atual: {os.getcwd()}")
    print("-" * 60)

    contents: List[Dict[str, Any]] = [
        {
            "role": "user",
            "parts": [{"text": tarefa}]
        }
    ]

    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_cached_tokens = 0
    turnos_usados = 0
    concluido = False

    for turno in range(1, max_turns + 1):
        turnos_usados = turno
        print(f"\n>>> TURNO {turno} / {max_turns}")

        # Chamada ao modelo
        try:
            resp, modelo_ativo = chamar_gemini(
                api_key=api_key,
                modelo=modelo_ativo,
                contents=contents,
                system_prompt=SYSTEM_PROMPT,
                api_base_url=api_base_url
            )
        except Exception as e:
            print(f"[ERRO DE API] Falha na chamada da API: {e}", file=sys.stderr)
            sys.exit(1)

        # Extração de métricas de tokens
        usage = resp.get("usageMetadata", {})
        metricas = extrair_metricas_usage(usage)

        total_prompt_tokens += metricas["prompt"]
        total_completion_tokens += metricas["completion"]
        total_cached_tokens += metricas["cached"]

        # Processamento da resposta
        candidates = resp.get("candidates", [])
        if not candidates:
            print("[Aviso] Nenhuma resposta retornada pelo modelo.")
            break

        candidate = candidates[0]
        content = candidate.get("content", {})
        parts = content.get("parts", [])

        texto_gerado, function_calls = extrair_texto_e_tool_calls(candidate)
        tamanho_texto = len(texto_gerado)

        # Métricas do turno
        print(f"Prompt tokens: {metricas['prompt']}")
        print(f"Completion tokens: {metricas['completion']}")
        print(f"Total tokens: {metricas['total']}")
        if usage.get("cachedContentTokenCount") is not None:
            print(f"Cached content tokens: {metricas['cached']}")
        print(f"Tamanho do texto gerado: {tamanho_texto} caracteres")

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
                        "parts": [montar_function_response(func_name, resultado_tool)]
                    })
                else:
                    print(f"[Tool Error] Função desconhecida: '{func_name}'")
                    contents.append({
                        "role": "function",
                        "parts": [
                            montar_function_response(
                                func_name, {"erro": f"Ferramenta desconhecida: {func_name}"}
                            )
                        ]
                    })
        else:
            # Resposta final de texto
            concluido = True
            print(f"\n[Resposta Final do Modelo]:\n{texto_gerado.strip()}")
            break

    if not concluido:
        print("\n[ATENCAO] Nao concluido: max_turns atingido sem resposta final")

    # Resumo final com estimativa de custo considerando desconto de cache
    custo_estimado = calcular_custo(
        prompt_total=total_prompt_tokens,
        cached_total=total_cached_tokens,
        completion_total=total_completion_tokens,
        precos=PRECOS_PADRAO
    )

    print("\n" + "=" * 60)
    print("RESUMO DA EXECUÇÃO")
    print("=" * 60)
    print(f"Turnos utilizados: {turnos_usados} de {max_turns}")
    print(f"Total Prompt Tokens: {total_prompt_tokens}")
    print(f"Total Cached Tokens: {total_cached_tokens}")
    print(f"Total Completion Tokens: {total_completion_tokens}")
    print(f"Total Geral de Tokens: {total_prompt_tokens + total_completion_tokens}")
    print(f"Custo estimado da execução: ${custo_estimado:.6f} USD")
    print(f"Modelo final: {modelo_ativo}")
    print("=" * 60)
