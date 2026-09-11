"""Módulo de benchmark do Agent Harness — avaliação comparativa de cache ON/OFF e da política de poda."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from harness import __version__
from harness.config import janela_tarifaria, verificar_idade_precos
from harness.contexto import gerar_contexto_repo
from harness.errors import HarnessError
from harness.loop import (
    LoopResult,
    executar_loop,
    garantir_saida_utf8,
)
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

# Condições medidas. A poda sempre foi aplicada, o que confundia o efeito da política de
# poda com o do cache; a terceira condição separa os dois.
# ATENÇÃO (declarado, não inferido): SEM_PODA = cache OFF + poda OFF. A base de comparação
# dela é a condição OFF, e o que ela isola é exclusivamente o efeito da poda do histórico.
CONDICOES_BENCH: Tuple[Dict[str, Any], ...] = (
    {"nome": "ON", "cache_habilitado": True, "poda_habilitada": True},
    {"nome": "OFF", "cache_habilitado": False, "poda_habilitada": True},
    {"nome": "SEM_PODA", "cache_habilitado": False, "poda_habilitada": False},
)

DESCRICAO_CONDICOES: Dict[str, str] = {
    "ON": "cache ON + poda ON (linha de base)",
    "OFF": "cache OFF + poda ON (isola o efeito do cache)",
    "SEM_PODA": "cache OFF + poda OFF (isola o efeito da politica de poda; a base e a condicao OFF)",
}

REPETICOES_PADRAO = 5
REPETICOES_MINIMAS = 3

NOMES_CONDICOES: Tuple[str, ...] = tuple(c["nome"] for c in CONDICOES_BENCH)


def selecionar_condicoes(nomes: Optional[Sequence[str]] = None) -> Tuple[Dict[str, Any], ...]:
    """
    Função pura que seleciona as condições a medir pelo nome, preservando a ordem canônica.
    Sem nomes, devolve todas as três. Nome desconhecido é ERRO, não aviso: uma condição a menos
    na coleta é uma célula a menos no resultado, e isso não pode passar despercebido.
    """
    if not nomes:
        return CONDICOES_BENCH

    pedidos = [str(n).strip().upper() for n in nomes if str(n).strip()]
    if not pedidos:
        raise ValueError("nenhuma condicao informada em --condicoes.")

    desconhecidas = [n for n in pedidos if n not in NOMES_CONDICOES]
    if desconhecidas:
        raise ValueError(
            f"condicao invalida: {', '.join(desconhecidas)}. "
            f"Validas: {', '.join(NOMES_CONDICOES)}."
        )

    return tuple(c for c in CONDICOES_BENCH if c["nome"] in pedidos)

# Cada célula fecha com N execuções NÃO abortadas. Aborto (0 turnos ou erro de conexão/API) é
# falha de instrumento, não do modelo, então a execução é reposta. O teto existe para que uma
# queda de rede não vire coleta infinita: estourou, a coleta para e avisa.
MAX_REPOSICOES_POR_CELULA = 2

# Campos numéricos agregados por célula (tarefa x condição)
CAMPOS_AGREGADOS = ("custo_real", "latencia", "turnos", "prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens")


def _mediana(valores: List[float]) -> float:
    """Função pura: mediana de uma lista de números (média dos dois centrais quando par)."""
    if not valores:
        return 0.0
    ordenados = sorted(valores)
    n = len(ordenados)
    meio = n // 2
    if n % 2 == 1:
        return float(ordenados[meio])
    return (ordenados[meio - 1] + ordenados[meio]) / 2.0


def _amplitude(valores: List[float]) -> float:
    """Função pura: amplitude (máximo - mínimo) de uma lista de números."""
    if not valores:
        return 0.0
    return float(max(valores) - min(valores))


# Marcadores de que a execução nem chegou a ser medida (erro de conexão ou de API), e não de
# que o modelo tentou e não cumpriu a tarefa. Aborto não entra no denominador da taxa.
PADROES_ABORTO = (
    "erro de conexão", "erro de conexao", "erro na api", "http ",
    "timeout", "timed out", "connection", "urlopen", "getaddrinfo",
    "cadeia de fallback", "erro inesperado na chamada", "ssl", "proxy",
)


def classificar_falha(turnos: int, erro: Optional[str], sucesso: bool) -> Optional[str]:
    """
    Função pura que separa aborto de falha de tarefa.
    - 'aborto': nada foi medido (0 turnos) ou a chamada morreu em conexão/API — o dado não
      existe, então a execução não pode contar como tentativa do modelo;
    - 'tarefa': houve turnos e mesmo assim a validação não passou — o modelo tentou e não cumpriu;
    - None quando a execução é válida.
    """
    if sucesso:
        return None
    if turnos <= 0:
        return "aborto"
    texto_erro = (erro or "").lower()
    if texto_erro and any(padrao in texto_erro for padrao in PADROES_ABORTO):
        return "aborto"
    return "tarefa"


def parametros_proprios_do_provider(provider: Provider) -> Dict[str, Any]:
    """
    Parâmetros de geração próprios do provider, quando ele os declara — no Gemini são
    `thinking_level` e `max_output_tokens`, em campos próprios. Devolve {} para quem não expõe
    o hook (caminho OpenAI-compatível, que usa `reasoning_effort`/`max_tokens`).
    """
    leitor = getattr(provider, "parametros_de_geracao_efetivos", None)
    return dict(leitor()) if callable(leitor) else {}


def obter_commit_repo(base_dir: Optional[Union[str, Path]] = None) -> str:
    """Retorna o SHA do commit do repositório em base_dir ('desconhecido' se não for um repo git)."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(base_dir) if base_dir else None,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return "desconhecido"
    sha = (proc.stdout or "").strip()
    return sha if proc.returncode == 0 and sha else "desconhecido"


def montar_cabecalho(
    provider: Provider,
    repeticoes: int,
    cobertura: str,
    base_dir: Optional[Union[str, Path]] = None,
    momento: Optional[datetime] = None,
    reposicoes_por_celula: Optional[Dict[str, int]] = None,
    motivo_parada: Optional[str] = None,
    condicoes_medidas: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """
    Cabeçalho de origem da rodada gravado no topo de todo arquivo de resultado.
    Registra data, driver e versão, modelo efetivo, esforço e teto de saída, janela tarifária,
    tarifa usada, cobertura da conta, número de repetições, política de reposição e commit.
    """
    momento = momento or datetime.now(timezone.utc)
    reposicoes = dict(reposicoes_por_celula or {})
    proprios = parametros_proprios_do_provider(provider)
    # Só as condições realmente medidas entram no cabeçalho: um desenho de 6 células declarado
    # como 9 seria origem falsa no artefato.
    nomes_medidos = list(condicoes_medidas) if condicoes_medidas else list(NOMES_CONDICOES)
    return {
        "data": momento.isoformat(),
        "driver": provider.nome,
        # Versão do harness que implementa o driver (não há SDK externo: o corpo é montado à mão)
        "driver_versao": __version__,
        "modelo_efetivo": provider.modelo_ativo,
        # Cada caminho declara os seus: o Gemini não tem `reasoning_effort`/`max_tokens`, e
        # reaproveitar os nomes registraria um campo que a API dele não aplicou.
        "reasoning_effort": getattr(provider, "reasoning_effort", None),
        "max_tokens": getattr(provider, "max_tokens", None),
        "thinking_level": proprios.get("thinking_level"),
        "max_output_tokens": proprios.get("max_output_tokens"),
        "thinking_recusado": proprios.get("thinking_recusado"),
        "janela_tarifaria": janela_tarifaria(momento),
        "tarifa_por_1m_tokens": dict(provider.precos),
        "cobertura": cobertura,
        "repeticoes": repeticoes,
        "max_reposicoes_por_celula": MAX_REPOSICOES_POR_CELULA,
        "reposicoes_por_celula": reposicoes,
        "total_reposicoes": sum(reposicoes.values()),
        "motivo_parada": motivo_parada,
        "commit": obter_commit_repo(base_dir),
        "condicoes_medidas": nomes_medidos,
        "condicoes": {nome: DESCRICAO_CONDICOES[nome] for nome in nomes_medidos},
    }


def agregar_por_celula(execucoes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Função pura que agrega as execuções cruas por célula tarefa x condição.
    Reporta mediana e amplitude de custo, latência, turnos e tokens, além da taxa de sucesso (n de N).
    """
    celulas: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    ordem: List[Tuple[str, str]] = []
    for execucao in execucoes:
        chave = (execucao["tarefa_id"], execucao.get("condicao") or execucao.get("cache", ""))
        if chave not in celulas:
            celulas[chave] = []
            ordem.append(chave)
        celulas[chave].append(execucao)

    agregado: List[Dict[str, Any]] = []
    for chave in ordem:
        lista = celulas[chave]
        n = len(lista)
        sucessos = sum(1 for e in lista if e.get("sucesso"))
        # Mesmo classificador usado na coleta; o campo tipo_falha é só o registro do que ele decidiu
        tipos = [
            e.get("tipo_falha") or classificar_falha(e.get("turnos", 0), e.get("erro"), bool(e.get("sucesso")))
            for e in lista
        ]
        abortos = sum(1 for t in tipos if t == "aborto")
        falhas_tarefa = sum(1 for t in tipos if t == "tarefa")
        # O denominador da taxa de sucesso é quem foi de fato medido: aborto não é tentativa
        # do modelo, é falha de instrumento/coleta, e contar isso como fracasso mentiria.
        validas = n - abortos

        # Critério ESTRITO (regra antiga, arquivo de teste com 'assert'): só existe onde a regra
        # antiga existia (T2/T3). Sucesso no critério estrito = passou em tudo E passaria na regra
        # antiga. A diferença entre os dois é o que a mudança do validador comprou.
        # A aplicabilidade é decidida sobre quem NÃO abortou: aborto não tem veredito de
        # validação (o registro fica None) e não pode transformar a célula inteira em "n/a".
        validas_lista = [e for e in lista if e.get("tipo_falha") != "aborto"]
        estritos = [e.get("criterio_estrito_ok") for e in validas_lista]
        estrito_aplicavel = bool(estritos) and all(v is not None for v in estritos)
        if estrito_aplicavel:
            sucessos_estrito: Optional[int] = sum(
                1 for e in validas_lista if e.get("sucesso") and e.get("criterio_estrito_ok")
            )
            so_no_novo: Optional[int] = sum(
                1 for e in validas_lista if e.get("sucesso") and not e.get("criterio_estrito_ok")
            )
            taxa_estrito: Optional[str] = f"{sucessos_estrito}/{validas}"
        else:
            sucessos_estrito = None
            so_no_novo = None
            taxa_estrito = None

        entrada: Dict[str, Any] = {
            "tarefa_id": chave[0],
            "tarefa_nome": lista[0].get("tarefa_nome", ""),
            "condicao": chave[1],
            "n": n,
            "validas": validas,
            "abortos": abortos,
            "falhas_tarefa": falhas_tarefa,
            "sucessos": sucessos,
            "taxa_sucesso": f"{sucessos}/{validas}",
            "taxa_sucesso_pct": (sucessos / validas * 100.0) if validas else 0.0,
            "sucessos_criterio_estrito": sucessos_estrito,
            "taxa_sucesso_estrito": taxa_estrito,
            "aprovadas_so_no_criterio_novo": so_no_novo,
        }
        for campo in CAMPOS_AGREGADOS:
            valores = [float(e.get(campo, 0) or 0) for e in lista]
            entrada[campo] = {
                "mediana": _mediana(valores),
                "amplitude": _amplitude(valores),
                "min": min(valores) if valores else 0.0,
                "max": max(valores) if valores else 0.0,
            }
        agregado.append(entrada)
    return agregado


def gravar_resultados_json(
    cabecalho: Dict[str, Any],
    execucoes: List[Dict[str, Any]],
    agregado: List[Dict[str, Any]],
    base_dir: Optional[Union[str, Path]] = None,
    provider_nome: Optional[str] = None,
) -> Path:
    """
    Grava os resultados crus (nunca agregados) em JSON, com o cabeçalho de origem no topo.
    Escrita explicitamente em UTF-8 com ensure_ascii=False: o arquivo é legível e não depende
    da codificação local da máquina (que é justamente o que corrompia o texto acentuado).
    """
    base = Path(base_dir).resolve() if base_dir else Path.cwd().resolve()
    data_str = str(cabecalho.get("data", ""))[:10].replace("-", "") or "semdata"
    driver = provider_nome or cabecalho.get("driver") or "provider"
    caminho = base / f"bench_{data_str}_{driver}.json"
    payload = {
        "cabecalho": cabecalho,
        "execucoes": execucoes,
        "resumo_por_celula": agregado,
    }
    caminho.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return caminho


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


# Caminhos de falha aceitos no arquivo de teste. O enunciado pede um teste que FALHE se a
# condição não valer — por 'assert', por 'exit('/'sys.exit(' ou por 'raise'. Exigir a
# substring literal 'assert' reprovava quem cumpre o enunciado ao pé da letra.
PADROES_FALHA_NO_TESTE = (
    re.compile(r"\bassert\b"),
    re.compile(r"\bexit\s*\("),
    re.compile(r"\braise\b"),
)


def validar_arquivo_de_teste(conteudo: str, funcao_alvo: str) -> Tuple[bool, str]:
    """
    Função pura que valida o arquivo de teste gerado pelo modelo contra o enunciado:
    - o teste precisa referenciar a função alvo (não basta um arquivo qualquer);
    - o teste precisa ter um caminho de falha: imprimir não é testar.
    O stdout não é inspecionado aqui: quem exige execução bem-sucedida é o chamador.
    """
    if not conteudo or not conteudo.strip():
        return False, "arquivo de teste vazio."

    if not re.search(r"\b" + re.escape(funcao_alvo) + r"\b", conteudo):
        return False, f"o teste não referencia a função alvo '{funcao_alvo}'."

    if not any(padrao.search(conteudo) for padrao in PADROES_FALHA_NO_TESTE):
        return False, "o teste não tem caminho de falha (esperado 'assert', 'exit(' ou 'raise')."

    return True, "teste referencia a função alvo e tem caminho de falha."


def criterio_estrito_de_teste(conteudo: str) -> bool:
    """
    Função pura com a regra ANTIGA, mantida apenas para registro: o arquivo de teste precisava
    conter a substring 'assert'. Ela não decide mais nada — existe para que o relatório possa
    mostrar a taxa de sucesso nos dois critérios em vez de escolher o mais conveniente depois.
    """
    return bool(conteudo) and "assert" in conteudo


def _conteudo_do_arquivo_de_teste(
    historico: List[Dict[str, Any]],
    nome_arquivo: str,
    caminho: Path,
) -> Tuple[str, str]:
    """Recupera o conteúdo do arquivo de teste do histórico (escrever_arquivo) ou, na falta, do disco."""
    conteudo = ""
    for m in historico:
        if m.get("role") == "model":
            for tc in m.get("tool_calls", []):
                if tc.get("name") == "escrever_arquivo":
                    args = tc.get("args", {})
                    if isinstance(args, dict) and nome_arquivo in args.get("caminho", ""):
                        conteudo = args.get("conteudo", "")

    if not conteudo and caminho.is_file():
        try:
            conteudo = caminho.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return "", f"Falha ao ler {nome_arquivo}: {e}"

    return conteudo, ""


def _validar_tarefa_com_teste(
    historico: List[Dict[str, Any]],
    base: Path,
    nome_arquivo: str,
    funcao_alvo: str,
) -> Tuple[bool, str, bool]:
    """
    Valida as tarefas que pedem um par função + teste executável. Cumprir o enunciado é:
    o teste referencia a função alvo, tem caminho de falha, e foi executado com código de
    saída 0. O stdout não-vazio deixou de ser exigido — o enunciado não o pede, e um teste
    silencioso que passa é um teste válido.

    Devolve (sucesso_no_criterio_novo, detalhe, criterio_estrito_ok), onde o terceiro item é
    a regra antiga (arquivo com a substring 'assert') avaliada sobre o mesmo conteúdo.
    """
    conteudo, erro_leitura = _conteudo_do_arquivo_de_teste(historico, nome_arquivo, base / nome_arquivo)
    estrito_ok = criterio_estrito_de_teste(conteudo)

    if erro_leitura:
        return False, erro_leitura, estrito_ok
    if not conteudo:
        return False, f"Arquivo {nome_arquivo} não encontrado no histórico nem no disco.", estrito_ok

    ok, detalhe = validar_arquivo_de_teste(conteudo, funcao_alvo)
    if not ok:
        return False, f"{nome_arquivo}: {detalhe}", estrito_ok

    for m in historico:
        if m.get("role") == "tool" and m.get("name") == "executar_comando":
            resultado = m.get("resultado", {})
            if isinstance(resultado, dict) and resultado.get("codigo_saida") == 0:
                if nome_arquivo in _comando_para_tool(historico, m):
                    return True, f"{nome_arquivo} executado com código de saída 0.", estrito_ok

    return False, f"Nenhuma execução válida de {nome_arquivo} com código de saída 0 encontrada.", estrito_ok


def validar_detalhado(
    tarefa_nome: str,
    historico: List[Dict[str, Any]],
    base_dir: Union[str, Path],
) -> Dict[str, Any]:
    """
    Validação completa usada pela coleta. Devolve o resultado no critério NOVO e também o
    critério ESTRITO (regra antiga: arquivo de teste contendo a substring 'assert'), para que
    o relatório possa mostrar os dois números em vez de escolher o mais conveniente depois.

    Retorna {"sucesso": bool, "detalhe": str, "criterio_estrito_ok": Optional[bool]}.
    criterio_estrito_ok é None onde o critério estrito não se aplica: a regra de T1 (contagem
    de módulos no mapa) não mudou, então nela os dois critérios coincidem por construção.
    """
    base = Path(base_dir)

    if tarefa_nome == "mapa":
        caminho_mapa = base / "bench_mapa.md"
        if not caminho_mapa.is_file():
            return {"sucesso": False, "detalhe": "Arquivo bench_mapa.md não foi criado no diretório base.",
                    "criterio_estrito_ok": None}

        try:
            conteudo = caminho_mapa.read_text(encoding="utf-8", errors="replace").lower()
        except Exception as e:
            return {"sucesso": False, "detalhe": f"Falha ao ler bench_mapa.md: {e}",
                    "criterio_estrito_ok": None}

        modulos_encontrados = [
            m for m in MODULOS_ESPERADOS_T1
            if re.search(r"\b" + re.escape(m) + r"\b", conteudo)
        ]
        if len(modulos_encontrados) >= 6:
            return {"sucesso": True,
                    "detalhe": f"bench_mapa.md contém {len(modulos_encontrados)} módulos identificados.",
                    "criterio_estrito_ok": None}

        return {
            "sucesso": False,
            "detalhe": f"bench_mapa.md contém apenas {len(modulos_encontrados)} módulos (mínimo exigido: 6).",
            "criterio_estrito_ok": None,
        }

    elif tarefa_nome == "geracao-com-teste":
        sucesso, detalhe, estrito_ok = _validar_tarefa_com_teste(
            historico, base, "bench_test_math.py", "soma"
        )
        return {"sucesso": sucesso, "detalhe": detalhe, "criterio_estrito_ok": estrito_ok}

    elif tarefa_nome == "spec-de-arquivo":
        sucesso, detalhe, estrito_ok = _validar_tarefa_com_teste(
            historico, base, "bench_test_contador.py", "contar_palavras"
        )
        return {"sucesso": sucesso, "detalhe": detalhe, "criterio_estrito_ok": estrito_ok}

    return {"sucesso": False, "detalhe": f"Tarefa desconhecida: '{tarefa_nome}'",
            "criterio_estrito_ok": None}


def validar(
    tarefa_nome: str,
    historico: List[Dict[str, Any]],
    base_dir: Union[str, Path]
) -> Tuple[bool, str]:
    """
    Validador puro por tarefa que inspeciona o histórico e os arquivos gerados.
    Retorna (True, detalhe) em caso de sucesso ou (False, detalhe) em caso de falha.
    Atalho para validar_detalhado no critério novo, que é o que vale.
    """
    resultado = validar_detalhado(tarefa_nome, historico, base_dir)
    return resultado["sucesso"], resultado["detalhe"]


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


def _metricas_zeradas() -> Dict[str, Any]:
    """Métricas neutras de uma execução que falhou: a falha entra no denominador, não é excluída."""
    return {
        "turnos_usados": 0,
        "prompt_tokens": 0,
        "cached_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "custo_real": 0.0,
        "custo_sem_cache": 0.0,
        "economia": 0.0,
        "economia_pct": 0.0,
        "latencia_modelo_s": 0.0,
        "latencia_tools_s": 0.0,
        "latencia_total_s": 0.0,
        "teto_contexto_observado": 0,
        "prompt_tokens_max": 0,
        "turnos_prefixo_estavel": 0,
        "turnos_totais": 0,
        "prefixo_estavel_pct": 0.0,
        "reasoning_effort": None,
        "max_tokens": None,
        "thinking_level": None,
        "max_output_tokens": None,
        "thinking_recusado": None,
    }


def rodar_benchmark(
    provider_factory: Callable[[], Provider],
    max_turns: int = 6,
    base_dir: Optional[Union[str, Path]] = None,
    repeticoes: int = REPETICOES_PADRAO,
    cobertura: str = "nao_declarada",
    condicoes: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Executa a suíte de 3 tarefas nas 3 condições (ON, OFF, SEM_PODA), com `repeticoes`
    repetições por condição, medindo latência, custos, teto de contexto e taxa de sucesso.
    Cada execução é registrada individualmente e nunca agregada antes de gravar.
    Grava os resultados crus em JSON, com o cabeçalho de origem no topo, a cada execução
    e ao final — uma coleta longa interrompida não perde o que já foi pago.
    Política de reposição: cada célula fecha com `repeticoes` execuções NÃO abortadas; aborto
    (0 turnos ou erro de conexão/API) é reposto, até o teto de MAX_REPOSICOES_POR_CELULA — ao
    estourar, a coleta para e avisa. Falha de tarefa entra no denominador como fracasso normal.
    Limpa os artefatos de cada tarefa antes e depois da execução.
    Se base_dir for fornecido, executa no diretório com garantia de restauração via try/finally.
    """
    if repeticoes < REPETICOES_MINIMAS:
        raise ValueError(
            f"repeticoes={repeticoes} invalido: o minimo e {REPETICOES_MINIMAS} "
            f"(abaixo disso a mediana nao diz nada). Use um valor >= {REPETICOES_PADRAO}."
        )

    garantir_saida_utf8()
    verificar_idade_precos()
    # Condições efetivamente medidas nesta rodada (permitir rodar só ON/OFF é o que torna a
    # perna Gemini possível sem pagar por um braço que a medição do DeepSeek já mostrou inerte)
    condicoes_medidas = selecionar_condicoes(condicoes)
    base = Path(base_dir).resolve() if base_dir else Path.cwd().resolve()
    contexto_repo = gerar_contexto_repo(base)
    execucoes: List[Dict[str, Any]] = []
    inicio_rodada = datetime.now(timezone.utc)

    # Provider de referência para o cabeçalho (uma instância por execução continua sendo criada)
    provider_ref = provider_factory()
    cabecalho = montar_cabecalho(
        provider_ref, repeticoes, cobertura, base, inicio_rodada,
        condicoes_medidas=[c["nome"] for c in condicoes_medidas],
    )

    reposicoes_por_celula: Dict[str, int] = {}
    motivo_parada: List[Optional[str]] = [None]

    def _persistir() -> Path:
        """
        Grava os resultados crus com o que já existe. Chamado também a cada execução, e não
        só no fim: uma coleta longa interrompida não pode custar o que já foi pago.
        """
        cabecalho["modelo_efetivo"] = getattr(provider_ref, "modelo_ativo", cabecalho["modelo_efetivo"])
        cabecalho["reposicoes_por_celula"] = dict(reposicoes_por_celula)
        cabecalho["total_reposicoes"] = sum(reposicoes_por_celula.values())
        cabecalho["motivo_parada"] = motivo_parada[0]
        return gravar_resultados_json(cabecalho, execucoes, agregar_por_celula(execucoes), base, provider_ref.nome)

    def _executar_uma(
        tarefa: Dict[str, Any],
        condicao: Dict[str, Any],
        repeticao: int,
        rodada: int,
    ) -> Dict[str, Any]:
        """Executa UMA vez (tarefa x condição) e devolve o registro cru, com o tipo de falha classificado."""
        nonlocal provider_ref
        t_id = tarefa["id"]
        t_nome = tarefa["nome"]
        instrucao = tarefa["instrucao"]
        artefatos = tarefa["artefatos"]
        nome_condicao = condicao["nome"]
        limpar_artefatos(base, artefatos)

        print()
        print("#" * 60)
        print(
            f"BENCHMARK: {t_id} ({t_nome}) | Condicao: {nome_condicao} | "
            f"repeticao {repeticao}/{repeticoes} (rodada {rodada})"
        )
        print(
            f"# cache_habilitado={condicao['cache_habilitado']} "
            f"poda_habilitada={condicao['poda_habilitada']}"
        )
        print("#" * 60)

        inicio_execucao = datetime.now(timezone.utc)
        t0 = time.monotonic()
        erro: Optional[str] = None
        sucesso = False
        detalhe = ""
        estrito_ok: Optional[bool] = None
        met: Dict[str, Any] = _metricas_zeradas()
        provider_instancia = provider_ref
        try:
            provider_instancia = provider_factory()
            provider_ref = provider_instancia  # o cabeçalho registra o modelo efetivamente usado
            loop_res: LoopResult = executar_loop(
                tarefa=instrucao,
                provider=provider_instancia,
                max_turns=max_turns,
                contexto_projeto=contexto_repo,
                cache_habilitado=condicao["cache_habilitado"],
                poda_habilitada=condicao["poda_habilitada"],
            )
            validacao = validar_detalhado(t_nome, loop_res.historico, base)
            sucesso = validacao["sucesso"]
            detalhe = validacao["detalhe"]
            # Registrado junto: quantas execuções só passaram porque o critério mudou
            estrito_ok = validacao["criterio_estrito_ok"]
            met = loop_res.metricas
        except HarnessError as e:
            erro = str(e)
        except Exception as e:
            erro = f"{type(e).__name__}: {e}"

        latencia = time.monotonic() - t0
        limpar_artefatos(base, artefatos)

        if erro and not detalhe:
            detalhe = f"Falha na execucao: {erro}"

        tipo_falha = classificar_falha(met["turnos_usados"], erro, sucesso)
        if erro:
            print(f"[FALHA/{tipo_falha.upper()}] {t_id} | {nome_condicao} | repeticao {repeticao}: {erro}", file=sys.stderr)

        return {
            "tarefa_id": t_id,
            "tarefa_nome": t_nome,
            "condicao": nome_condicao,
            "cache": nome_condicao,
            "cache_habilitado": condicao["cache_habilitado"],
            "poda_habilitada": condicao["poda_habilitada"],
            "repeticao": repeticao,
            "rodada": rodada,
            "inicio_utc": inicio_execucao.isoformat(),
            "janela_tarifaria": janela_tarifaria(inicio_execucao),
            "modelo": getattr(provider_instancia, "modelo_ativo", None),
            "reasoning_effort": met["reasoning_effort"],
            "max_tokens": met["max_tokens"],
            "thinking_level": met["thinking_level"],
            "max_output_tokens": met["max_output_tokens"],
            "thinking_recusado": met["thinking_recusado"],
            "turnos": met["turnos_usados"],
            "prompt_tokens": met["prompt_tokens"],
            "prompt_tokens_max": met["prompt_tokens_max"],
            "cached_tokens": met["cached_tokens"],
            "completion_tokens": met["completion_tokens"],
            "total_tokens": met["total_tokens"],
            "custo_real": met["custo_real"],
            "custo_sem_cache": met["custo_sem_cache"],
            "economia": met["economia"],
            "economia_pct": met["economia_pct"],
            "latencia": latencia,
            "latencia_modelo_s": met["latencia_modelo_s"],
            "latencia_tools_s": met["latencia_tools_s"],
            "latencia_loop_s": met["latencia_total_s"],
            "teto_contexto_observado": met["teto_contexto_observado"],
            "turnos_prefixo_estavel": met["turnos_prefixo_estavel"],
            "turnos_totais": met["turnos_totais"],
            "prefixo_estavel_pct": met["prefixo_estavel_pct"],
            "sucesso": sucesso,
            "tipo_falha": tipo_falha,
            "criterio_estrito_ok": estrito_ok,
            "detalhe": detalhe,
            "erro": erro,
        }

    print("=" * 60)
    print(f"CONDICOES MEDIDAS — {repeticoes} repeticoes por condicao")
    for condicao in condicoes_medidas:
        print(f"  - {condicao['nome']}: {DESCRICAO_CONDICOES[condicao['nome']]}")
    for nome in DESCRICAO_CONDICOES:
        if nome not in [c["nome"] for c in condicoes_medidas]:
            print(f"  - {nome}: FORA desta rodada")
    print(f"Politica de reposicao: celula fecha com {repeticoes} execucoes nao abortadas; "
          f"teto de {MAX_REPOSICOES_POR_CELULA} reposicoes por celula")
    print("=" * 60)

    cwd_original = Path.cwd()
    if base_dir:
        os.chdir(base)

    try:
        for idx, tarefa in enumerate(TAREFAS_BENCH):
            if motivo_parada[0] is not None:
                break

            t_id = tarefa["id"]
            # Estado por célula: cada uma só fecha com `repeticoes` execuções NÃO abortadas
            estado = {c["nome"]: {"validas": 0, "reposicoes": 0} for c in condicoes_medidas}
            rodada = 0

            while any(e["validas"] < repeticoes for e in estado.values()) and motivo_parada[0] is None:
                rodada += 1
                # Rotaciona a ordem das condições a cada rodada para mitigar viés de ordem/aquecimento
                deslocamento = (idx + rodada - 1) % len(condicoes_medidas)
                ordem = condicoes_medidas[deslocamento:] + condicoes_medidas[:deslocamento]

                for condicao in ordem:
                    nome_condicao = condicao["nome"]
                    situacao = estado[nome_condicao]
                    if situacao["validas"] >= repeticoes:
                        continue

                    execucao = _executar_uma(tarefa, condicao, situacao["validas"] + 1, rodada)
                    execucoes.append(execucao)

                    if execucao["tipo_falha"] == "aborto":
                        if situacao["reposicoes"] >= MAX_REPOSICOES_POR_CELULA:
                            motivo_parada[0] = (
                                f"celula {t_id}/{nome_condicao} estourou o teto de "
                                f"{MAX_REPOSICOES_POR_CELULA} reposicoes "
                                f"({situacao['reposicoes']} ja feitas): a coleta foi interrompida "
                                f"para nao mascarar falha sistematica de conexao/API."
                            )
                            print(f"[PARADA] {motivo_parada[0]}", file=sys.stderr)
                            _persistir()
                            break
                        situacao["reposicoes"] += 1
                    else:
                        situacao["validas"] += 1

                    reposicoes_por_celula[f"{t_id}|{nome_condicao}"] = situacao["reposicoes"]
                    _persistir()
    finally:
        if base_dir:
            os.chdir(cwd_original)

    caminho_json = _persistir()
    print(f"\nResultados crus ({len(execucoes)} execucoes) gravados em: {caminho_json}")

    return execucoes


ROTULOS_CONDICOES = {
    "ON": "TOTAL CACHE ON",
    "OFF": "TOTAL CACHE OFF",
    "SEM_PODA": "TOTAL SEM PODA",
}


def _condicao_de(r: Dict[str, Any]) -> str:
    """Condição de um registro, aceitando também o campo legado 'cache'."""
    return r.get("condicao") or r.get("cache", "")


def imprimir_resumo_celulas(agregado: List[Dict[str, Any]]) -> str:
    """
    Gera e imprime o resumo por célula tarefa x condição: mediana e amplitude de custo,
    latência, turnos e tokens, mais a taxa de sucesso (n de N).
    """
    linhas: List[str] = [
        "| Tarefa | Condicao | Validas | Abortos | Falhas tarefa | Sucesso novo | Sucesso estrito | "
        "So no criterio novo | Turnos med. (amp.) | Custo med. $ (amp.) | Latencia med. s (amp.) | "
        "Prompt med. (amp.) | Completion med. (amp.) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in agregado:
        # A taxa de sucesso é sobre as válidas (não abortadas); aborto é falha de instrumento.
        # O critério estrito aparece ao lado do novo, e não no lugar dele: escolher depois seria
        # exatamente o que esta rodada existe para impedir.
        taxa_estrito = c["taxa_sucesso_estrito"] if c["taxa_sucesso_estrito"] is not None else "n/a"
        so_novo = c["aprovadas_so_no_criterio_novo"] if c["aprovadas_so_no_criterio_novo"] is not None else "n/a"
        linhas.append(
            f"| {c['tarefa_id']} ({c['tarefa_nome']}) | {c['condicao']} | {c['validas']} | {c['abortos']} | "
            f"{c['falhas_tarefa']} | {c['taxa_sucesso']} | {taxa_estrito} | {so_novo} | "
            f"{c['turnos']['mediana']:.1f} ({c['turnos']['amplitude']:.0f}) | "
            f"${c['custo_real']['mediana']:.6f} (${c['custo_real']['amplitude']:.6f}) | "
            f"{c['latencia']['mediana']:.2f} ({c['latencia']['amplitude']:.2f}) | "
            f"{c['prompt_tokens']['mediana']:.0f} ({c['prompt_tokens']['amplitude']:.0f}) | "
            f"{c['completion_tokens']['mediana']:.0f} ({c['completion_tokens']['amplitude']:.0f}) |"
        )

    tabela_str = "\n".join(linhas)
    print("\n" + "=" * 60)
    print("RESUMO POR CELULA (TAREFA x CONDICAO) — mediana (amplitude)")
    print("=" * 60)
    print(tabela_str)
    print("=" * 60 + "\n")
    return tabela_str


def imprimir_tabela(resultados: List[Dict[str, Any]]) -> str:
    """Gera e imprime a tabela de resultados do benchmark estilo artigo da Google."""
    linhas: List[str] = [
        "| Tarefa | Cache | Turnos | Prompt | Cached | Custo $ | Economia | Sucesso |",
        "|---|---|---|---|---|---|---|---|",
    ]

    totais: Dict[str, Dict[str, Any]] = {}

    for r in resultados:
        t_label = f"{r['tarefa_id']} ({r['tarefa_nome']})"
        cache_str = _condicao_de(r)
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

        bucket = totais.setdefault(cache_str, {
            "turnos": 0,
            "prompt": 0,
            "cached": 0,
            "custo_real": 0.0,
            "custo_sem_cache": 0.0,
            "sucessos": 0,
            "total": 0,
        })
        bucket["turnos"] += turnos
        bucket["prompt"] += prompt
        bucket["cached"] += cached
        bucket["custo_real"] += r["custo_real"]
        bucket["custo_sem_cache"] += r["custo_sem_cache"]
        bucket["total"] += 1
        if r["sucesso"]:
            bucket["sucessos"] += 1

    # Linhas de total (uma por condição medida)
    for condicao, b in totais.items():
        if b["total"] > 0:
            label = ROTULOS_CONDICOES.get(condicao, f"TOTAL {condicao}")
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
                f"| **{label}** | {condicao} | {b['turnos']} | {b['prompt']} | {b['cached']} | {custo_str} | {econ_str} | {b['sucessos']}/{b['total']} |"
            )

    tabela_str = "\n".join(linhas)
    print("\n" + "=" * 60)
    print("TABELA COMPARATIVA DE BENCHMARK (GOOGLE ARTICLE STYLE)")
    print("=" * 60)
    print(tabela_str)
    print("=" * 60 + "\n")
    return tabela_str
