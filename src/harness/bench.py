"""Módulo de benchmark do Agent Harness — avaliação comparativa de cache ON/OFF."""

import os
from pathlib import Path
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from harness.contexto import gerar_contexto_repo
from harness.loop import LoopResult, executar_loop
from harness.providers import Provider

TAREFAS_BENCH = [
    {
        "id": "T1",
        "nome": "mapa",
        "instrucao": (
            "com base no contexto do repo, crie bench_mapa.md listando cada modulo de "
            "src/harness com uma frase sobre sua responsabilidade. NAO modifique arquivos existentes."
        ),
        "artefatos": ["bench_mapa.md"],
    },
    {
        "id": "T2",
        "nome": "geracao-com-teste",
        "instrucao": (
            "crie bench_math.py com funcao soma(a, b) e bench_test_math.py que importa soma e "
            "falha (exit != 0) se soma(2, 3) != 5; depois rode: python bench_test_math.py. "
            "Se o teste passou, encerre."
        ),
        "artefatos": ["bench_math.py", "bench_test_math.py"],
    },
    {
        "id": "T3",
        "nome": "spec-de-arquivo",
        "instrucao": (
            "crie bench_contador.py com funcao contar_palavras(t) que retorna o numero de "
            "palavras de t, e bench_test_contador.py que verifica contar_palavras('a b c') == 3 e "
            "conta palavras de uma frase com 5 palavras; rode: python bench_test_contador.py."
        ),
        "artefatos": ["bench_contador.py", "bench_test_contador.py"],
    },
]

MODULOS_ESPERADOS_T1 = [
    "config",
    "contexto",
    "env",
    "errors",
    "loop",
    "providers",
    "tools",
    "usage",
    "__main__",
]


def _comando_para_tool(historico: List[Dict[str, Any]], tool_msg: Dict[str, Any]) -> str:
    """Recupera o comando associado a uma mensagem role 'tool' inspecionando os model tool_calls."""
    tc_id = tool_msg.get("tool_call_id")
    for m in historico:
        if m.get("role") == "model":
            for tc in m.get("tool_calls", []):
                if tc.get("id") == tc_id:
                    args = tc.get("args", {})
                    if isinstance(args, dict):
                        return args.get("comando", "")
                    elif isinstance(args, str):
                        return args
    return ""


def validar(
    tarefa_nome: str,
    historico: List[Dict[str, Any]],
    base_dir: Union[str, Path]
) -> Tuple[bool, str]:
    """
    Validador puro por tarefa que inspeciona o histórico e os arquivos gerados.
    Retorna (True, detalhe) em caso de sucesso ou (False, detalhe) em caso de falha.
    """
    base = Path(base_dir)

    if tarefa_nome == "mapa":
        caminho_mapa = base / "bench_mapa.md"
        if not caminho_mapa.is_file():
            return False, "Arquivo bench_mapa.md não foi criado no diretório base."

        try:
            conteudo = caminho_mapa.read_text(encoding="utf-8", errors="replace").lower()
        except Exception as e:
            return False, f"Falha ao ler bench_mapa.md: {e}"

        modulos_encontrados = [
            m for m in MODULOS_ESPERADOS_T1
            if re.search(r"\b" + re.escape(m) + r"\b", conteudo)
        ]
        if len(modulos_encontrados) >= 6:
            return True, f"bench_mapa.md contém {len(modulos_encontrados)} módulos identificados."
        return (
            False,
            f"bench_mapa.md contém apenas {len(modulos_encontrados)} módulos (mínimo exigido: 6)."
        )

    elif tarefa_nome == "geracao-com-teste":
        caminho_teste = base / "bench_test_math.py"
        conteudo_teste = ""
        if caminho_teste.is_file():
            try:
                conteudo_teste = caminho_teste.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                return False, f"Falha ao ler bench_test_math.py: {e}"
        else:
            for m in historico:
                if m.get("role") == "model":
                    for tc in m.get("tool_calls", []):
                        if tc.get("name") == "escrever_arquivo":
                            args = tc.get("args", {})
                            if isinstance(args, dict) and "bench_test_math.py" in args.get("caminho", ""):
                                conteudo_teste = args.get("conteudo", "")

        if conteudo_teste and "assert" not in conteudo_teste:
            return False, "Arquivo bench_test_math.py não contém asserção ('assert')."

        for m in historico:
            if m.get("role") == "tool" and m.get("name") == "executar_comando":
                resultado = m.get("resultado", {})
                if isinstance(resultado, dict) and resultado.get("codigo_saida") == 0:
                    stdout = str(resultado.get("stdout", "")).strip()
                    if not stdout:
                        continue
                    cmd = _comando_para_tool(historico, m)
                    if "bench_test_math.py" in cmd:
                        return True, "bench_test_math.py executado com código de saída 0 e stdout não-vazio."
        return False, "Nenhuma execução válida de bench_test_math.py com código de saída 0 e stdout não-vazio encontrada."

    elif tarefa_nome == "spec-de-arquivo":
        caminho_teste = base / "bench_test_contador.py"
        conteudo_teste = ""
        if caminho_teste.is_file():
            try:
                conteudo_teste = caminho_teste.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                return False, f"Falha ao ler bench_test_contador.py: {e}"
        else:
            for m in historico:
                if m.get("role") == "model":
                    for tc in m.get("tool_calls", []):
                        if tc.get("name") == "escrever_arquivo":
                            args = tc.get("args", {})
                            if isinstance(args, dict) and "bench_test_contador.py" in args.get("caminho", ""):
                                conteudo_teste = args.get("conteudo", "")

        if conteudo_teste and "assert" not in conteudo_teste:
            return False, "Arquivo bench_test_contador.py não contém asserção ('assert')."

        for m in historico:
            if m.get("role") == "tool" and m.get("name") == "executar_comando":
                resultado = m.get("resultado", {})
                if isinstance(resultado, dict) and resultado.get("codigo_saida") == 0:
                    stdout = str(resultado.get("stdout", "")).strip()
                    if not stdout:
                        continue
                    cmd = _comando_para_tool(historico, m)
                    if "bench_test_contador.py" in cmd:
                        return True, "bench_test_contador.py executado com código de saída 0 e stdout não-vazio."
        return False, "Nenhuma execução válida de bench_test_contador.py com código de saída 0 e stdout não-vazio encontrada."

    return False, f"Tarefa desconhecida: '{tarefa_nome}'"


def limpar_artefatos(base_dir: Union[str, Path], artefatos: List[str]) -> None:
    """Remove exclusivamente a lista de arquivos conhecidos passados no diretório base."""
    base = Path(base_dir)
    for art in artefatos:
        p = base / art
        try:
            if p.is_file():
                p.unlink()
        except OSError:
            pass


def rodar_benchmark(
    provider_factory: Callable[[], Provider],
    max_turns: int = 6,
    base_dir: Optional[Union[str, Path]] = None,
) -> List[Dict[str, Any]]:
    """
    Executa a suíte de 3 tarefas com cache ON e cache OFF, medindo latência, custos e taxa de sucesso.
    Limpa os artefatos de cada tarefa antes e depois da execução.
    Se base_dir for fornecido, executa no diretório com garantia de restauração via try/finally.
    """
    base = Path(base_dir).resolve() if base_dir else Path.cwd().resolve()
    contexto_repo = gerar_contexto_repo(base)
    resultados: List[Dict[str, Any]] = []

    cwd_original = Path.cwd()
    if base_dir:
        os.chdir(base)

    try:
        for tarefa in TAREFAS_BENCH:
            t_id = tarefa["id"]
            t_nome = tarefa["nome"]
            instrucao = tarefa["instrucao"]
            artefatos = tarefa["artefatos"]

            for regime, cache_flag in [("ON", True), ("OFF", False)]:
                # Limpeza preventiva
                limpar_artefatos(base, artefatos)

                print()
                print("#" * 60)
                print(f"BENCHMARK: {t_id} ({t_nome}) | Cache: {regime}")
                print("#" * 60)

                t0 = time.monotonic()
                provider_instancia = provider_factory()
                loop_res: LoopResult = executar_loop(
                    tarefa=instrucao,
                    provider=provider_instancia,
                    max_turns=max_turns,
                    contexto_projeto=contexto_repo,
                    cache_habilitado=cache_flag,
                )
                latencia = time.monotonic() - t0

                sucesso, detalhe = validar(t_nome, loop_res.historico, base)
                limpar_artefatos(base, artefatos)

                met = loop_res.metricas
                resultados.append({
                    "tarefa_id": t_id,
                    "tarefa_nome": t_nome,
                    "cache": regime,
                    "cache_habilitado": cache_flag,
                    "turnos": met["turnos_usados"],
                    "prompt_tokens": met["prompt_tokens"],
                    "cached_tokens": met["cached_tokens"],
                    "completion_tokens": met["completion_tokens"],
                    "custo_real": met["custo_real"],
                    "custo_sem_cache": met["custo_sem_cache"],
                    "economia": met["economia"],
                    "economia_pct": met["economia_pct"],
                    "sucesso": sucesso,
                    "detalhe": detalhe,
                    "latencia": latencia,
                })
    finally:
        if base_dir:
            os.chdir(cwd_original)

    return resultados


def imprimir_tabela(resultados: List[Dict[str, Any]]) -> str:
    """Gera e imprime a tabela de resultados do benchmark estilo artigo da Google."""
    linhas: List[str] = [
        "| Tarefa | Cache | Turnos | Prompt | Cached | Custo $ | Economia | Sucesso |",
        "|---|---|---|---|---|---|---|---|",
    ]

    total_on = {
        "turnos": 0,
        "prompt": 0,
        "cached": 0,
        "custo_real": 0.0,
        "custo_sem_cache": 0.0,
        "sucessos": 0,
        "total": 0,
    }
    total_off = {
        "turnos": 0,
        "prompt": 0,
        "cached": 0,
        "custo_real": 0.0,
        "custo_sem_cache": 0.0,
        "sucessos": 0,
        "total": 0,
    }

    for r in resultados:
        t_label = f"{r['tarefa_id']} ({r['tarefa_nome']})"
        cache_str = r["cache"]
        turnos = r["turnos"]
        prompt = r["prompt_tokens"]
        cached = r["cached_tokens"]
        custo = f"${r['custo_real']:.6f}"
        if r["economia"] > 0:
            economia_str = f"${r['economia']:.6f} ({r['economia_pct']:.1f}%)"
        elif r["economia"] < 0:
            economia_str = f"-${abs(r['economia']):.6f} ({r['economia_pct']:.1f}%) (REGRESSAO)"
        else:
            economia_str = "$0.000000 (0.0%)"
        sucesso_str = "SIM" if r["sucesso"] else "NÃO"

        linhas.append(
            f"| {t_label} | {cache_str} | {turnos} | {prompt} | {cached} | {custo} | {economia_str} | {sucesso_str} |"
        )

        bucket = total_on if r["cache_habilitado"] else total_off
        bucket["turnos"] += turnos
        bucket["prompt"] += prompt
        bucket["cached"] += cached
        bucket["custo_real"] += r["custo_real"]
        bucket["custo_sem_cache"] += r["custo_sem_cache"]
        bucket["total"] += 1
        if r["sucesso"]:
            bucket["sucessos"] += 1

    # Linhas de total
    for label, b in [("TOTAL CACHE ON", total_on), ("TOTAL CACHE OFF", total_off)]:
        if b["total"] > 0:
            econ_total = b["custo_sem_cache"] - b["custo_real"]
            econ_pct = (econ_total / b["custo_sem_cache"] * 100.0) if b["custo_sem_cache"] > 0 else 0.0
            if econ_total < 0:
                econ_str = f"-${abs(econ_total):.6f} ({econ_pct:.1f}%) (REGRESSAO)"
            elif econ_total == 0 or b["cached"] == 0:
                econ_str = "$0.000000 (0.0%)"
            else:
                econ_str = f"${econ_total:.6f} ({econ_pct:.1f}%)"
            custo_str = f"${b['custo_real']:.6f}"
            linhas.append(
                f"| **{label}** | {'ON' if 'ON' in label else 'OFF'} | {b['turnos']} | {b['prompt']} | {b['cached']} | {custo_str} | {econ_str} | {b['sucessos']}/{b['total']} |"
            )

    tabela_str = "\n".join(linhas)
    print("\n" + "=" * 60)
    print("TABELA COMPARATIVA DE BENCHMARK (GOOGLE ARTICLE STYLE)")
    print("=" * 60)
    print(tabela_str)
    print("=" * 60 + "\n")
    return tabela_str
