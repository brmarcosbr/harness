from dataclasses import dataclass
import os
import sys
import time
from typing import Any, Dict, List, Optional
from harness.config import (
    MAX_TURNS,
    MAX_TURNOS_MANTER_PODA,
    SYSTEM_PROMPT,
    TETO_CONTEXTO_TOKENS,
    verificar_idade_precos,
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
from harness.tools import TOOL_REGISTRY, TOOLS, executar_comando, truncar_saida
from harness.usage import calcular_custo


class _FluxoSeguro:
    """
    Envolve stdout/stderr para que nenhuma escrita derrube a execução.

    No Windows, quando a saída é redirecionada para arquivo ou pipe, o Python codifica com a
    codificação local (cp1252): uma resposta do modelo com emoji ou seta levantava
    UnicodeEncodeError DEPOIS de a chamada já ter sido paga. Aqui o que não for codificável
    degrada para o que a codificação aceita, em vez de propagar a exceção.
    """

    __slots__ = ("_fluxo",)

    def __init__(self, fluxo: Any) -> None:
        self._fluxo = fluxo

    def write(self, texto: str) -> int:
        try:
            return self._fluxo.write(texto)
        except UnicodeEncodeError:
            codificacao = getattr(self._fluxo, "encoding", None) or "ascii"
            try:
                return self._fluxo.write(
                    texto.encode(codificacao, errors="replace").decode(codificacao, errors="replace")
                )
            except (OSError, ValueError, UnicodeError):
                return 0
        except (OSError, ValueError):
            return 0

    def __getattr__(self, nome: str) -> Any:
        return getattr(self._fluxo, nome)


def garantir_saida_utf8() -> None:
    """
    Deixa o processo à prova de terminal hostil. Idempotente.

    1. Reconfigura stdout/stderr para UTF-8 com errors="replace": a saída redirecionada deixa
       de ser codificada na codificação local, que é o que quebrava a execução e o que fazia
       o texto acentuado sair ilegível.
    2. Envolve os dois fluxos em _FluxoSeguro: escrita que ainda assim não codificar degrada
       em vez de levantar.
    3. Marca PYTHONUTF8=1 no ambiente para os subprocessos das tools. A variável não retroage
       sobre este processo já iniciado — vale para os filhos, que é exatamente onde o modelo
       roda `python bench_test_math.py` e onde um print com emoji derrubaria o teste dele.
    """
    os.environ.setdefault("PYTHONUTF8", "1")

    for atributo in ("stdout", "stderr"):
        fluxo = getattr(sys, atributo, None)
        if fluxo is None or isinstance(fluxo, _FluxoSeguro):
            continue

        reconfigurar = getattr(fluxo, "reconfigure", None)
        if reconfigurar is not None:
            try:
                reconfigurar(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass

        try:
            setattr(sys, atributo, _FluxoSeguro(fluxo))
        except (AttributeError, TypeError):
            pass


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


def _head_enviado(system_prompt: str, mensagens: List[Dict[str, Any]]) -> str:
    """
    Prefixo efetivamente enviado ao endpoint: system prompt + primeira mensagem do histórico.
    É o trecho que o provedor reaproveita como prefixo cacheável — se ele mudar de um turno
    para o outro, todo o cache do prefixo é invalidado (prefix-breaking).
    """
    if not mensagens:
        return system_prompt
    return f"{system_prompt}\n{mensagens[0].get('text', '')}"


def executar_loop(
    tarefa: str,
    provider: Provider,
    max_turns: int = MAX_TURNS,
    contexto_projeto: Optional[str] = None,
    cache_habilitado: bool = True,
    poda_habilitada: bool = True,
    teto_contexto_tokens: int = TETO_CONTEXTO_TOKENS,
) -> LoopResult:
    """
    Executa o loop de harness multi-turno com o Provider configurado.
    Utiliza formato neutro de mensagens internamente.
    Retorna LoopResult contendo o histórico e métricas consolidadas.

    poda_habilitada=False desliga a poda do histórico: o prompt cresce livremente até o teto
    de contexto do modelo e o teto efetivamente observado é registrado nas métricas.
    teto_contexto_tokens permite parametrizar o teto da poda (útil em teste e na comparação
    entre condições do benchmark).
    """
    garantir_saida_utf8()

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
    print(
        f"Poda de historico: {'ON' if poda_habilitada else 'OFF (sem poda - teto observado sera registrado)'} "
        f"| teto: {teto_contexto_tokens} tokens"
    )
    print(f"Diretório atual: {os.getcwd()}")
    if os.name != "nt":
        print("[AVISO] ambiente nao-Windows: a whitelist estrita protege multiplataforma contra comandos destrutivos; handlers nativos emulados")
    verificar_idade_precos()
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
    # Custo acumulado turno a turno (ver acumulação logo após a resposta do provider)
    custo_real = 0.0
    custo_sem_cache = 0.0

    # Telemetria de execução: latência decomposta, teto observado e invariância de prefixo
    instante_inicio_loop = time.monotonic()
    tempo_modelo_total = 0.0
    tempo_ferramentas_total = 0.0
    teto_contexto_observado = 0
    prompt_tokens_max = 0
    telemetria_prefixo: List[Dict[str, Any]] = []
    sha_head_canonico = sha256_head(_head_enviado(SYSTEM_PROMPT, mensagens))
    sha_head_anterior: Optional[str] = None
    turnos_prefixo_estavel = 0

    for turno in range(1, max_turns + 1):
        turnos_usados = turno
        print(f"\n>>> TURNO {turno} / {max_turns}")

        # Poda do histórico se exceder o teto de tokens, preservando head e tail
        if poda_habilitada:
            mensagens = podar_historico(
                mensagens,
                teto_tokens=teto_contexto_tokens,
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

        # Telemetria de prefixo: hash do head enviado e comparação com o turno anterior.
        # No primeiro turno não há anterior: a referência é o head canônico (sem o marcador
        # de cache OFF), que é o prefixo que a API cachearia se o harness fosse ingênuo.
        sha_head_turno = sha256_head(_head_enviado(SYSTEM_PROMPT, mensagens))
        referencia = sha_head_anterior if sha_head_anterior is not None else sha_head_canonico
        prefixo_estavel = sha_head_turno == referencia
        if prefixo_estavel:
            turnos_prefixo_estavel += 1
        telemetria_prefixo.append({
            "turno": turno,
            "sha256_head": sha_head_turno,
            "prefixo_estavel": prefixo_estavel,
        })
        sha_head_anterior = sha_head_turno

        # Teto observado: maior histórico efetivamente enviado (relevante na condição sem poda)
        tokens_enviados = estimar_tokens_historico(mensagens)
        teto_contexto_observado = max(teto_contexto_observado, tokens_enviados)

        # Chamada ao modelo através do provider (tempo de modelo acumulado separadamente)
        t_modelo_inicio = time.monotonic()
        try:
            try:
                resp = provider.gerar(mensagens=mensagens, system_prompt=SYSTEM_PROMPT)
            except HarnessError:
                raise
            except Exception as e:
                raise HarnessError(f"Falha na chamada do provider {provider.nome}: {e}") from e
        finally:
            tempo_modelo_total += time.monotonic() - t_modelo_inicio

        # Acumulação de token E custo ANTES de qualquer impressão do turno: a chamada já foi
        # paga, então nada do que aconteça na escrita da saída pode perder esse dado.
        metricas = resp.usage
        total_prompt_tokens += metricas.get("prompt", 0)
        total_completion_tokens += metricas.get("completion", 0)
        total_cached_tokens += metricas.get("cached", 0)
        prompt_tokens_max = max(prompt_tokens_max, metricas.get("prompt", 0))
        custo_real = calcular_custo(
            prompt_total=total_prompt_tokens,
            cached_total=total_cached_tokens,
            completion_total=total_completion_tokens,
            precos=provider.precos,
        )
        custo_sem_cache = calcular_custo(
            prompt_total=total_prompt_tokens,
            cached_total=0,
            completion_total=total_completion_tokens,
            precos=provider.precos,
        )

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
                    # Validação de kwargs contra o schema declarado em TOOLS
                    tool_def = next((t for t in TOOLS if t.get("name") == func_name), None)
                    propriedades_permitidas = None
                    if tool_def and isinstance(tool_def.get("parameters"), dict):
                        props = tool_def["parameters"].get("properties")
                        if isinstance(props, dict):
                            propriedades_permitidas = set(props.keys())

                    chaves_invalidas = set()
                    if propriedades_permitidas is not None and isinstance(func_args, dict):
                        chaves_invalidas = set(func_args.keys()) - propriedades_permitidas

                    if chaves_invalidas:
                        msg_invalida = (
                            f"Argumento não permitido pelo schema da ferramenta '{func_name}': "
                            f"{', '.join(sorted(chaves_invalidas))}. "
                            f"Propriedades permitidas: {sorted(propriedades_permitidas)}"
                        )
                        print(f"[Tool Error] {msg_invalida}")
                        if func_name == "executar_comando":
                            resultado_raw = {"stdout": "", "stderr": msg_invalida, "codigo_saida": -1}
                        else:
                            resultado_raw = {"sucesso": False, "erro": msg_invalida}
                    else:
                        print(f"[Tool Exec] Executando '{func_name}' com args: {func_args}")
                        t_tool_inicio = time.monotonic()
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
                                    sys.stderr.write(
                                        f"[AVISO] Fallback posicional acionado para a tool '{func_name}' com argumento 'comando'={func_args['comando']!r}. "
                                        f"Verifique o schema declarado ou a configuração do provider.\n"
                                    )
                                    resultado_raw = tool_func(func_args["comando"])
                                else:
                                    resultado_raw = {"sucesso": False, "erro": f"Argumentos inválidos para a função {func_name}: {func_args}"}
                            else:
                                raise HarnessError(f"Erro interno na execução da tool '{func_name}': {e}") from e
                        except Exception as e:
                            resultado_raw = {"sucesso": False, "erro": f"Erro na execução da tool '{func_name}': {e}"}
                        finally:
                            tempo_ferramentas_total += time.monotonic() - t_tool_inicio

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

    # Resumo final com estimativa de custo considerando desconto de cache.
    # custo_real/custo_sem_cache já vêm acumulados turno a turno (antes de cada impressão).
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

    latencia_total = time.monotonic() - instante_inicio_loop
    effort_efetivo = getattr(provider, "reasoning_effort", None)
    max_tokens_efetivo = getattr(provider, "max_tokens", None)
    prefixo_pct = (turnos_prefixo_estavel / turnos_usados * 100.0) if turnos_usados else 0.0

    print(f"Esforco de raciocinio: {effort_efetivo} | Teto de saida: {max_tokens_efetivo} tokens")
    print(f"Poda de historico: {'ON' if poda_habilitada else 'OFF'} | teto observado: {teto_contexto_observado} tokens estimados")
    print(f"Latencia: total {latencia_total:.2f}s | modelo {tempo_modelo_total:.2f}s | ferramentas {tempo_ferramentas_total:.2f}s")
    print(f"Prefixos estaveis: {turnos_prefixo_estavel}/{turnos_usados} turnos ({prefixo_pct:.1f}%)")
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
        "poda_habilitada": poda_habilitada,
        "teto_contexto_tokens": teto_contexto_tokens,
        "teto_contexto_observado": teto_contexto_observado,
        "prompt_tokens_max": prompt_tokens_max,
        "latencia_modelo_s": tempo_modelo_total,
        "latencia_tools_s": tempo_ferramentas_total,
        "latencia_total_s": latencia_total,
        "reasoning_effort": effort_efetivo,
        "max_tokens": max_tokens_efetivo,
        "turnos_prefixo_estavel": turnos_prefixo_estavel,
        "turnos_totais": turnos_usados,
        "prefixo_estavel_pct": prefixo_pct,
        "telemetria_prefixo": telemetria_prefixo,
        "tarefa": tarefa,
        "modelo": provider.modelo_ativo,
    }

    return LoopResult(historico=mensagens, metricas=metricas_resultado)
