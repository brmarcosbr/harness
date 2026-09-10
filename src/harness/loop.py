from dataclasses import dataclass
import os
import time
from typing import Any, Dict, List, Optional
from harness.config import (
    MAX_TURNS,
    MAX_TURNOS_MANTER_PODA,
    SYSTEM_PROMPT,
    TETO_CONTEXTO_TOKENS,
)
from harness.contexto import (
    PREFIXO_CONTEXTO,
    estimar_tokens,
    estimar_tokens_historico,
    montar_head,
    podar_historico,
    sha256_head,
)
from harness.errors import HarnessError
from harness.providers import Provider
from harness.tools import TOOL_REGISTRY, executar_comando, truncar_saida
from harness.usage import calcular_custo


@dataclass
class LoopResult:
    """Resultado estruturado da execução do loop do Agent Harness."""
    historico: List[Dict[str, Any]]
    metricas: Dict[str, Any]


def _processar_e_truncar_resultado(resultado: Any) -> Any:
    """Aplica truncamento de saída em strings de resultado de ferramentas."""
    if isinstance(resultado, dict):
        res_truncado = {}
        for k, v in resultado.items():
            if isinstance(v, str):
                res_truncado[k] = truncar_saida(v)
            elif isinstance(v, list):
                # Se for lista de resultados (ex: buscar_no_projeto)
                res_truncado[k] = [truncar_saida(item) if isinstance(item, str) else item for item in v]
            else:
                res_truncado[k] = v
        return res_truncado
    elif isinstance(resultado, str):
        return truncar_saida(resultado)
    return resultado


def executar_loop(
    tarefa: str,
    provider: Provider,
    max_turns: int = MAX_TURNS,
    contexto_projeto: Optional[str] = None,
    cache_habilitado: bool = True,
) -> LoopResult:
    """
    Executa o loop de harness multi-turno com o Provider configurado.
    Utiliza formato neutro de mensagens internamente.
    Retorna LoopResult contendo o histórico e métricas consolidadas.
    """
    print("=" * 60)
    print("AGENT HARNESS")
    print(f"Provider: {provider.nome.upper()} | Modelo: {provider.modelo_ativo}")
    print("=" * 60)
    print(f"Tarefa: {tarefa}")
    if contexto_projeto:
        print(f"Contexto do projeto: {estimar_tokens(contexto_projeto)} tokens estimados (head)")
    if cache_habilitado:
        head_texto = montar_head(SYSTEM_PROMPT, contexto_projeto)
        print(f"Cache de contexto: ON (head sha256: {sha256_head(head_texto)})")
    else:
        print("Cache de contexto: OFF (prefixo instavel - simulando harness ingenuo)")
    print(f"Diretório atual: {os.getcwd()}")
    if os.name != "nt":
        print("[AVISO] ambiente nao-Windows: a blocklist e o system prompt assumem cmd.exe; comandos Linux destrutivos NAO sao bloqueados")
    print("-" * 60)

    if contexto_projeto:
        primeira_msg_base = f"{PREFIXO_CONTEXTO}{contexto_projeto}"
        mensagens: List[Dict[str, Any]] = [
            {
                "role": "user",
                "text": primeira_msg_base,
            },
            {
                "role": "user",
                "text": tarefa,
            },
        ]
    else:
        primeira_msg_base = tarefa
        mensagens: List[Dict[str, Any]] = [
            {
                "role": "user",
                "text": primeira_msg_base,
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

        # Poda do histórico se exceder o teto de tokens, preservando head e tail
        mensagens = podar_historico(
            mensagens,
            teto_tokens=TETO_CONTEXTO_TOKENS,
            max_turnos_manter=MAX_TURNOS_MANTER_PODA,
        )

        # Se o cache estiver desligado, injeta token mutável na primeira mensagem user
        # a cada chamada para quebrar o prefixo estável (prefix-breaking trap)
        if not cache_habilitado:
            agora = time.time()
            mensagens[0] = {
                **mensagens[0],
                "text": f"<!-- cache_off: {agora} -->\n{primeira_msg_base}\n<!-- cache_off: {agora} -->"
            }

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
                    print(f"[Tool Exec] Executando '{func_name}' com args: {func_args}")
                    try:
                        # Passa func_args como kwargs se for dict
                        if isinstance(func_args, dict):
                            resultado_raw = tool_func(**func_args)
                        else:
                            resultado_raw = tool_func(func_args)
                    except TypeError as e:
                        msg_err = str(e).lower()
                        termos_assinatura = ("unexpected keyword", "missing", "required", "takes")
                        if any(termo in msg_err for termo in termos_assinatura):
                            # Fallback se a assinatura esperar comando posicional
                            if "comando" in func_args and len(func_args) == 1:
                                resultado_raw = tool_func(func_args["comando"])
                            else:
                                resultado_raw = {"sucesso": False, "erro": f"Argumentos inválidos para a função {func_name}: {func_args}"}
                        else:
                            raise HarnessError(f"Erro interno na execução da tool '{func_name}': {e}") from e
                    except Exception as e:
                        resultado_raw = {"sucesso": False, "erro": f"Erro na execução da tool '{func_name}': {e}"}

                    # Trunca saídas da tool para controle de contexto
                    resultado_tool = _processar_e_truncar_resultado(resultado_raw)

                    # Print descritivo da saída
                    if isinstance(resultado_tool, dict):
                        if "codigo_saida" in resultado_tool:
                            print(
                                f"[Tool Output] Código: {resultado_tool['codigo_saida']} | "
                                f"Stdout: {len(str(resultado_tool.get('stdout', '')))} chars | "
                                f"Stderr: {len(str(resultado_tool.get('stderr', '')))} chars"
                            )
                        else:
                            sucesso_str = "OK" if resultado_tool.get("sucesso", True) else "FALHA"
                            print(f"[Tool Output] Status: {sucesso_str} | Chaves: {list(resultado_tool.keys())}")

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
            if not texto_gerado:
                finish_reason = getattr(resp, "finish_reason", None) or "DESCONHECIDO"
                print(f"\n[AVISO] resposta vazia (possível bloqueio: {finish_reason})")
                break

            if getattr(resp, "aviso", None):
                print(f"\n[AVISO] {resp.aviso}")
                break

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
    custo_real = calcular_custo(
        prompt_total=total_prompt_tokens,
        cached_total=total_cached_tokens,
        completion_total=total_completion_tokens,
        precos=provider.precos
    )
    custo_sem_cache = calcular_custo(
        prompt_total=total_prompt_tokens,
        cached_total=0,
        completion_total=total_completion_tokens,
        precos=provider.precos
    )
    if total_cached_tokens > 0 and custo_sem_cache > 0:
        economia = custo_sem_cache - custo_real
        porcentagem = (economia / custo_sem_cache) * 100.0
    else:
        economia = 0.0
        porcentagem = 0.0

    print("\n" + "=" * 60)
    print("RESUMO DA EXECUÇÃO")
    print("=" * 60)
    print(f"Turnos utilizados: {turnos_usados} de {max_turns}")
    print(f"Total Prompt Tokens: {total_prompt_tokens}")
    print(f"Total Cached Tokens: {total_cached_tokens}")
    print(f"Total Completion Tokens: {total_completion_tokens}")
    print(f"Total Geral de Tokens: {total_prompt_tokens + total_completion_tokens}")
    print(f"Tokens estimados do historico final: {estimar_tokens_historico(mensagens)}")
    print(f"Custo real (com cache): ${custo_real:.6f} USD")
    print(f"Custo se sem cache: ${custo_sem_cache:.6f} USD | Economia: ${economia:.6f} USD ({porcentagem:.1f}%)")
    print(f"Modelo final: {provider.modelo_ativo}")
    print("=" * 60)

    metricas_resultado = {
        "turnos_usados": turnos_usados,
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "cached_tokens": total_cached_tokens,
        "total_tokens": total_prompt_tokens + total_completion_tokens,
        "custo_real": custo_real,
        "custo_sem_cache": custo_sem_cache,
        "economia": economia,
        "economia_pct": porcentagem,
        "cache_habilitado": cache_habilitado,
        "tarefa": tarefa,
        "modelo": provider.modelo_ativo,
    }

    return LoopResult(historico=mensagens, metricas=metricas_resultado)
