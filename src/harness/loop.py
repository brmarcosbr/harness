"""Módulo do loop multi-turno do Agent Harness."""

import os
from typing import Any, Callable, Dict, List, Optional
from harness.config import (
    MAX_TURNS,
    SYSTEM_PROMPT,
)
from harness.errors import HarnessError
from harness.providers import Provider
from harness.tools import executar_comando
from harness.usage import calcular_custo

TOOL_REGISTRY: Dict[str, Callable[[str], Dict[str, Any]]] = {
    "executar_comando": executar_comando
}


def executar_loop(
    tarefa: str,
    provider: Provider,
    max_turns: int = MAX_TURNS,
) -> List[Dict[str, Any]]:
    """
    Executa o loop de harness multi-turno com o Provider configurado.
    Utiliza formato neutro de mensagens internamente.
    Retorna o histórico neutro de mensagens ao final.
    """
    print("=" * 60)
    print("AGENT HARNESS")
    print(f"Provider: {provider.nome.upper()} | Modelo: {provider.modelo_ativo}")
    print("=" * 60)
    print(f"Tarefa: {tarefa}")
    print(f"Diretório atual: {os.getcwd()}")
    print("-" * 60)

    mensagens: List[Dict[str, Any]] = [
        {
            "role": "user",
            "text": tarefa
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

        # Chamada ao modelo através do provider
        try:
            resp = provider.gerar(mensagens=mensagens, system_prompt=SYSTEM_PROMPT)
        except HarnessError:
            raise
        except Exception as e:
            raise HarnessError(f"Falha na chamada do provider {provider.nome}: {e}") from e

        # Extração de métricas de tokens
        metricas = resp.usage
        total_prompt_tokens += metricas.get("prompt", 0)
        total_completion_tokens += metricas.get("completion", 0)
        total_cached_tokens += metricas.get("cached", 0)

        texto_gerado = resp.text
        tool_calls = resp.tool_calls
        tamanho_texto = len(texto_gerado)

        # Métricas do turno
        print(f"Prompt tokens: {metricas.get('prompt', 0)}")
        print(f"Completion tokens: {metricas.get('completion', 0)}")
        print(f"Total tokens: {metricas.get('total', 0)}")
        if metricas.get("cached", 0) > 0:
            print(f"Cached content tokens: {metricas['cached']}")
        print(f"Tamanho do texto gerado: {tamanho_texto} caracteres")

        if tool_calls:
            # Anexa a resposta do modelo (com as tool calls) ao histórico neutro
            mensagens.append({
                "role": "model",
                "text": texto_gerado,
                "tool_calls": tool_calls
            })

            # Executa cada chamada de ferramenta e anexa os resultados neutros
            for tc in tool_calls:
                call_id = tc.get("id", "")
                func_name = tc.get("name", "")
                func_args = tc.get("args", {})

                print(f"\n[Tool Call] Função: '{func_name}'")
                tool_func = TOOL_REGISTRY.get(func_name)

                if tool_func is not None:
                    comando = func_args.get("comando", "")
                    print(f"[Tool Exec] Executando comando: {comando}")
                    resultado_tool = tool_func(comando)
                    print(
                        f"[Tool Output] Código: {resultado_tool['codigo_saida']} | "
                        f"Stdout: {len(resultado_tool['stdout'])} chars | "
                        f"Stderr: {len(resultado_tool['stderr'])} chars"
                    )

                    # Anexa resposta da ferramenta ao histórico neutro
                    mensagens.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": func_name,
                        "resultado": resultado_tool
                    })
                else:
                    print(f"[Tool Error] Função desconhecida: '{func_name}'")
                    mensagens.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": func_name,
                        "resultado": {"erro": f"Ferramenta desconhecida: {func_name}"}
                    })
        else:
            # Anexa resposta final ao histórico e encerra
            mensagens.append({
                "role": "model",
                "text": texto_gerado,
                "tool_calls": []
            })
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
        precos=provider.precos
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
    print(f"Modelo final: {provider.modelo_ativo}")
    print("=" * 60)

    return mensagens
