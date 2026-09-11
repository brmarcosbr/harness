"""Testes unitários para o módulo de benchmark (W5) sem chamadas de rede."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Dict, List
import pytest

from harness.bench import (
    CONDICOES_BENCH,
    REPETICOES_MINIMAS,
    agregar_por_celula,
    imprimir_resumo_celulas,
    imprimir_tabela,
    limpar_artefatos,
    montar_cabecalho,
    rodar_benchmark,
    validar,
)
from harness.config import janela_tarifaria
from harness.errors import HarnessError
from harness.loop import LoopResult
from harness.providers import Provider, ProviderResponse


class FakeBenchProvider(Provider):
    """Provedor mock para testar a suíte de benchmark sem rede."""

    def __init__(self, respostas: List[ProviderResponse]):
        self.nome = "fake_bench"
        self.modelo_ativo = "fake-bench-model"
        self.precos = {"input": 0.30, "output": 2.50, "cache": 0.03}
        self.respostas = list(respostas)

    def gerar(self, mensagens: List[Dict[str, Any]], system_prompt: str) -> ProviderResponse:
        if not self.respostas:
            # Resposta neutra padrão caso faltem respostas
            return ProviderResponse(
                text="Final mock",
                tool_calls=[],
                usage={"prompt": 100, "completion": 20, "total": 120, "cached": 0},
                modelo=self.modelo_ativo,
            )
        return self.respostas.pop(0)

    def tools_schema(self) -> List[Dict[str, Any]]:
        return []


def test_validar_t1_mapa(tmp_path):
    # 1. Sem arquivo deve falhar
    valido, detalhe = validar("mapa", [], tmp_path)
    assert valido is False
    assert "não foi criado" in detalhe

    # 2. Com arquivo contendo menos de 6 módulos deve falhar
    (tmp_path / "bench_mapa.md").write_text("# Mapa\n- config\n- tools\n", encoding="utf-8")
    valido, detalhe = validar("mapa", [], tmp_path)
    assert valido is False
    assert "mínimo exigido: 6" in detalhe

    # 3. Com arquivo contendo 7 módulos válidos deve passar
    conteudo_valido = (
        "# Mapa do Harness\n"
        "- config: configurações\n"
        "- contexto: montagem de contexto\n"
        "- env: variáveis de ambiente\n"
        "- loop: loop multi-turno\n"
        "- providers: adaptadores de modelos\n"
        "- tools: ferramentas do sistema\n"
        "- usage: cálculo de custos\n"
    )
    (tmp_path / "bench_mapa.md").write_text(conteudo_valido, encoding="utf-8")
    valido, detalhe = validar("mapa", [], tmp_path)
    assert valido is True
    assert "módulos identificados" in detalhe


def test_validar_t2_geracao_com_teste():
    # 1. Histórico sem arquivo de teste deve falhar
    valido, detalhe = validar("geracao-com-teste", [], ".")
    assert valido is False
    assert "não encontrado no histórico nem no disco" in detalhe

    # 2. Histórico com código de saída != 0 deve falhar
    hist_falha = [
        {
            "role": "model",
            "tool_calls": [
                {
                    "id": "call_w0",
                    "name": "escrever_arquivo",
                    "args": {"caminho": "bench_test_math.py", "conteudo": "assert soma(1, 2) == 3\n"}
                },
                {"id": "call_1", "name": "executar_comando", "args": {"comando": "python bench_test_math.py"}}
            ],
        },
        {
            "role": "tool",
            "name": "escrever_arquivo",
            "tool_call_id": "call_w0",
            "resultado": {"sucesso": True},
        },
        {
            "role": "tool",
            "name": "executar_comando",
            "tool_call_id": "call_1",
            "resultado": {"codigo_saida": 1, "stdout": "", "stderr": "AssertionError"},
        },
    ]
    valido, detalhe = validar("geracao-com-teste", hist_falha, ".")
    assert valido is False
    assert "Nenhuma execução válida" in detalhe

    # 3. Histórico com código de saída 0 e teste contendo assert deve passar
    hist_sucesso = [
        {
            "role": "model",
            "tool_calls": [
                {
                    "id": "call_w",
                    "name": "escrever_arquivo",
                    "args": {"caminho": "bench_test_math.py", "conteudo": "assert soma(2, 3) == 5\nprint('OK')"}
                },
                {"id": "call_1", "name": "executar_comando", "args": {"comando": "python bench_test_math.py"}}
            ],
        },
        {
            "role": "tool",
            "name": "escrever_arquivo",
            "tool_call_id": "call_w",
            "resultado": {"sucesso": True},
        },
        {
            "role": "tool",
            "name": "executar_comando",
            "tool_call_id": "call_1",
            "resultado": {"codigo_saida": 0, "stdout": "OK", "stderr": ""},
        },
    ]
    valido, detalhe = validar("geracao-com-teste", hist_sucesso, ".")
    assert valido is True
    assert "código de saída 0" in detalhe


def test_validar_t3_spec_de_arquivo():
    hist_sucesso = [
        {
            "role": "model",
            "tool_calls": [
                {
                    "id": "call_cw",
                    "name": "escrever_arquivo",
                    "args": {"caminho": "bench_test_contador.py", "conteudo": "assert contar_palavras('a b c') == 3\nprint('Testes passaram')"}
                },
                {"id": "call_c", "name": "executar_comando", "args": {"comando": "python bench_test_contador.py"}}
            ],
        },
        {
            "role": "tool",
            "name": "escrever_arquivo",
            "tool_call_id": "call_cw",
            "resultado": {"sucesso": True},
        },
        {
            "role": "tool",
            "name": "executar_comando",
            "tool_call_id": "call_c",
            "resultado": {"codigo_saida": 0, "stdout": "Testes passaram", "stderr": ""},
        },
    ]
    valido, detalhe = validar("spec-de-arquivo", hist_sucesso, ".")
    assert valido is True

    hist_falha = [
        {
            "role": "model",
            "tool_calls": [{"id": "call_c", "name": "executar_comando", "args": {"comando": "python outro.py"}}],
        },
        {
            "role": "tool",
            "name": "executar_comando",
            "tool_call_id": "call_c",
            "resultado": {"codigo_saida": 0, "stdout": "", "stderr": ""},
        },
    ]
    valido, detalhe = validar("spec-de-arquivo", hist_falha, ".")
    assert valido is False


def test_limpar_artefatos(tmp_path):
    arq1 = tmp_path / "bench_a.txt"
    arq2 = tmp_path / "bench_b.py"
    arq_preservado = tmp_path / "preservado.py"

    arq1.write_text("a", encoding="utf-8")
    arq2.write_text("b", encoding="utf-8")
    arq_preservado.write_text("nao apagar", encoding="utf-8")

    limpar_artefatos(tmp_path, ["bench_a.txt", "bench_b.py"])

    assert not arq1.exists()
    assert not arq2.exists()
    assert arq_preservado.exists()


def test_imprimir_tabela(capsys):
    dados_mock = [
        {
            "tarefa_id": "T1",
            "tarefa_nome": "mapa",
            "cache": "ON",
            "cache_habilitado": True,
            "turnos": 2,
            "prompt_tokens": 1000,
            "cached_tokens": 800,
            "completion_tokens": 50,
            "custo_real": 0.000300,
            "custo_sem_cache": 0.000900,
            "economia": 0.000600,
            "economia_pct": 66.7,
            "sucesso": True,
            "latencia": 1.2,
        },
        {
            "tarefa_id": "T1",
            "tarefa_nome": "mapa",
            "cache": "OFF",
            "cache_habilitado": False,
            "turnos": 2,
            "prompt_tokens": 1000,
            "cached_tokens": 0,
            "completion_tokens": 50,
            "custo_real": 0.000900,
            "custo_sem_cache": 0.000900,
            "economia": 0.0,
            "economia_pct": 0.0,
            "sucesso": True,
            "latencia": 1.5,
        },
    ]

    tabela = imprimir_tabela(dados_mock)
    captured = capsys.readouterr().out

    assert "| Tarefa | Cache | Turnos | Prompt | Cached | Custo $ | Economia | Sucesso |" in tabela
    assert "T1 (mapa)" in tabela
    assert "TOTAL CACHE ON" in tabela
    assert "TOTAL CACHE OFF" in tabela
    assert "TABELA COMPARATIVA DE BENCHMARK" in captured


def test_validar_t1_mapa_palavra_inteira_ignora_substring(tmp_path):
    # Conteúdo com "configuração" não deve casar "config"
    conteudo = (
        "- configuração do sistema\n"
        "- contexto do projeto\n"
        "- loop principal\n"
    )
    (tmp_path / "bench_mapa.md").write_text(conteudo, encoding="utf-8")
    valido, detalhe = validar("mapa", [], tmp_path)
    assert valido is False
    # Apenas contexto e loop foram identificados (2 módulos)
    assert "2 módulos" in detalhe


def test_imprimir_tabela_com_economia_negativa(capsys):
    dados_mock = [
        {
            "tarefa_id": "T1",
            "tarefa_nome": "mapa",
            "cache": "ON",
            "cache_habilitado": True,
            "turnos": 3,
            "prompt_tokens": 1200,
            "cached_tokens": 500,
            "completion_tokens": 50,
            "custo_real": 0.001200,
            "custo_sem_cache": 0.001000,
            "economia": -0.000200,
            "economia_pct": -20.0,
            "sucesso": True,
            "latencia": 1.2,
        }
    ]
    tabela = imprimir_tabela(dados_mock)
    assert "-$0.000200 (-20.0%) (REGRESSAO)" in tabela


def test_validar_t2_e_t3_aceita_assert_exit_e_raise_e_reprova_print(tmp_path):
    """
    O enunciado pede um teste que FALHE (exit != 0) se a condição não valer, não que use a
    palavra 'assert'. Quem cumpre ao pé da letra — com sys.exit — tem que passar.
    """
    # --- T2: geracao-com-teste ---
    arquivo_math = tmp_path / "bench_test_math.py"
    hist_t2_ok_cmd = [
        {
            "role": "model",
            "tool_calls": [{"id": "c1", "name": "executar_comando", "args": {"comando": "python bench_test_math.py"}}],
        },
        {
            "role": "tool",
            "name": "executar_comando",
            "tool_call_id": "c1",
            "resultado": {"codigo_saida": 0, "stdout": "", "stderr": ""},
        },
    ]

    # 1. Arquivo que só imprime: reprova (imprimir não é testar)
    arquivo_math.write_text("print(1)\n", encoding="utf-8")
    valido, detalhe = validar("geracao-com-teste", hist_t2_ok_cmd, tmp_path)
    assert valido is False
    assert "bench_test_math.py" in detalhe

    # 1b. Arquivo que usa a função mas só imprime o resultado: reprova por não ter caminho de falha
    arquivo_math.write_text("from bench_math import soma\nprint(soma(2, 3))\n", encoding="utf-8")
    valido, detalhe = validar("geracao-com-teste", hist_t2_ok_cmd, tmp_path)
    assert valido is False
    assert "não tem caminho de falha" in detalhe

    # 2. Caminho de falha por sys.exit: passa, mesmo com stdout vazio
    arquivo_math.write_text(
        "import sys\nfrom bench_math import soma\nif soma(2, 3) != 5:\n    sys.exit(1)\n",
        encoding="utf-8",
    )
    valido, detalhe = validar("geracao-com-teste", hist_t2_ok_cmd, tmp_path)
    assert valido is True
    assert "código de saída 0" in detalhe

    # 3. Caminho de falha por raise: passa
    arquivo_math.write_text(
        "from bench_math import soma\nif soma(2, 3) != 5:\n    raise AssertionError('soma errada')\n",
        encoding="utf-8",
    )
    valido, _ = validar("geracao-com-teste", hist_t2_ok_cmd, tmp_path)
    assert valido is True

    # 4. Com assert: passa
    arquivo_math.write_text("from bench_math import soma\nassert soma(2, 3) == 5\n", encoding="utf-8")
    valido, _ = validar("geracao-com-teste", hist_t2_ok_cmd, tmp_path)
    assert valido is True

    # 5. Arquivo que não referencia a função alvo: reprova
    arquivo_math.write_text("assert 2 + 3 == 5\n", encoding="utf-8")
    valido, detalhe = validar("geracao-com-teste", hist_t2_ok_cmd, tmp_path)
    assert valido is False
    assert "não referencia a função alvo 'soma'" in detalhe

    # 6. Sem código de saída 0: reprova
    hist_t2_exit1 = [
        {
            "role": "model",
            "tool_calls": [{"id": "c1", "name": "executar_comando", "args": {"comando": "python bench_test_math.py"}}],
        },
        {
            "role": "tool",
            "name": "executar_comando",
            "tool_call_id": "c1",
            "resultado": {"codigo_saida": 1, "stdout": "", "stderr": "AssertionError"},
        },
    ]
    arquivo_math.write_text("from bench_math import soma\nassert soma(2, 3) == 5\n", encoding="utf-8")
    valido, detalhe = validar("geracao-com-teste", hist_t2_exit1, tmp_path)
    assert valido is False
    assert "Nenhuma execução válida" in detalhe

    # --- T3: spec-de-arquivo ---
    arquivo_contador = tmp_path / "bench_test_contador.py"
    hist_t3_ok_cmd = [
        {
            "role": "model",
            "tool_calls": [{"id": "c2", "name": "executar_comando", "args": {"comando": "python bench_test_contador.py"}}],
        },
        {
            "role": "tool",
            "name": "executar_comando",
            "tool_call_id": "c2",
            "resultado": {"codigo_saida": 0, "stdout": "testes ok", "stderr": ""},
        },
    ]

    # 1. Arquivo que só imprime: reprova
    arquivo_contador.write_text("print('testes ok')\n", encoding="utf-8")
    valido, detalhe = validar("spec-de-arquivo", hist_t3_ok_cmd, tmp_path)
    assert valido is False
    assert "bench_test_contador.py" in detalhe

    # 2. Com assert e referência à função alvo: passa
    arquivo_contador.write_text(
        "from bench_contador import contar_palavras\nassert contar_palavras('a b c') == 3\nprint('testes ok')\n",
        encoding="utf-8",
    )
    valido, detalhe = validar("spec-de-arquivo", hist_t3_ok_cmd, tmp_path)
    assert valido is True
    assert "código de saída 0" in detalhe


def test_rodar_benchmark_respeita_base_dir_e_restaura_cwd(tmp_path):
    cwd_inicial = Path.cwd().resolve()

    def fake_factory():
        return FakeBenchProvider([
            ProviderResponse(text="Final", tool_calls=[], usage={"prompt": 50, "completion": 10, "total": 60, "cached": 0}, modelo="mock")
        ])

    resultados = rodar_benchmark(fake_factory, max_turns=1, base_dir=tmp_path, repeticoes=3)
    cwd_final = Path.cwd().resolve()

    assert cwd_inicial == cwd_final
    assert len(resultados) == 27  # 3 tarefas x 3 condições x 3 repetições


def test_validar_t2_e_t3_le_do_disco_quando_ausente_no_historico(tmp_path):
    # Quando o modelo cria o arquivo via shell ou script em vez de escrever_arquivo,
    # o validador deve ler o arquivo do disco para verificar a presença de assert.
    arq_math = tmp_path / "bench_test_math.py"
    hist_cmd = [
        {
            "role": "model",
            "tool_calls": [{"id": "cmd_1", "name": "executar_comando", "args": {"comando": "python bench_test_math.py"}}],
        },
        {
            "role": "tool",
            "name": "executar_comando",
            "tool_call_id": "cmd_1",
            "resultado": {"codigo_saida": 0, "stdout": "1 test passed", "stderr": ""},
        },
    ]

    # 1. Arquivo no disco que só imprime -> Inválido
    arq_math.write_text("print('1 test passed')\n", encoding="utf-8")
    valido, detalhe = validar("geracao-com-teste", hist_cmd, tmp_path)
    assert valido is False
    assert "bench_test_math.py" in detalhe

    # 2. Arquivo no disco com assert e referência à função alvo -> Válido
    arq_math.write_text("from bench_math import soma\nassert soma(2, 3) == 5\nprint('1 test passed')\n", encoding="utf-8")
    valido, detalhe = validar("geracao-com-teste", hist_cmd, tmp_path)
    assert valido is True
    assert "código de saída 0" in detalhe


def test_rodar_benchmark_rotaciona_ordem_das_condicoes(tmp_path):
    """Verifica que a ordem das três condições rotaciona entre repetições (viés de ordem)."""
    def fake_factory():
        return FakeBenchProvider([
            ProviderResponse(text="Final", tool_calls=[], usage={"prompt": 50, "completion": 10, "total": 60, "cached": 0}, modelo="mock")
        ])

    resultados = rodar_benchmark(fake_factory, max_turns=1, base_dir=tmp_path, repeticoes=3)
    ordem_t1 = [(r["repeticao"], r["condicao"]) for r in resultados if r["tarefa_id"] == "T1"]

    assert ordem_t1 == [
        (1, "ON"), (1, "OFF"), (1, "SEM_PODA"),
        (2, "OFF"), (2, "SEM_PODA"), (2, "ON"),
        (3, "SEM_PODA"), (3, "ON"), (3, "OFF"),
    ]






# ============================================================================
# Rodada E1: N repetições, agregação por célula, cabeçalho de origem
# ============================================================================

def _execucao(tarefa_id: str, condicao: str, repeticao: int, custo: float, latencia: float,
              turnos: int, prompt: int, sucesso: bool = True) -> Dict[str, Any]:
    return {
        "tarefa_id": tarefa_id,
        "tarefa_nome": "mapa",
        "condicao": condicao,
        "cache": condicao,
        "repeticao": repeticao,
        "custo_real": custo,
        "custo_sem_cache": custo,
        "latencia": latencia,
        "turnos": turnos,
        "prompt_tokens": prompt,
        "completion_tokens": prompt // 10,
        "total_tokens": prompt + prompt // 10,
        "cached_tokens": 0,
        "sucesso": sucesso,
    }


def test_agregar_por_celula_mediana_e_amplitude_com_valores_conhecidos():
    execucoes = [
        # Célula T1/ON com 3 execuções (N ímpar)
        _execucao("T1", "ON", 1, 0.001, 10.0, 2, 100, sucesso=True),
        _execucao("T1", "ON", 2, 0.003, 12.0, 4, 300, sucesso=True),
        _execucao("T1", "ON", 3, 0.005, 14.0, 6, 500, sucesso=False),
        # Célula T1/OFF com 4 execuções (N par: mediana é a média dos dois centrais)
        _execucao("T1", "OFF", 1, 0.002, 1.0, 1, 10),
        _execucao("T1", "OFF", 2, 0.004, 2.0, 1, 10),
        _execucao("T1", "OFF", 3, 0.006, 3.0, 1, 10),
        _execucao("T1", "OFF", 4, 0.008, 4.0, 1, 10),
    ]

    agregado = agregar_por_celula(execucoes)
    assert [c["condicao"] for c in agregado] == ["ON", "OFF"]
    on, off = agregado

    assert on["n"] == 3
    assert on["sucessos"] == 2
    assert on["taxa_sucesso"] == "2/3"
    assert on["custo_real"]["mediana"] == pytest.approx(0.003)
    assert on["custo_real"]["amplitude"] == pytest.approx(0.004)
    assert on["custo_real"]["min"] == pytest.approx(0.001)
    assert on["custo_real"]["max"] == pytest.approx(0.005)
    assert on["latencia"]["mediana"] == pytest.approx(12.0)
    assert on["latencia"]["amplitude"] == pytest.approx(4.0)
    assert on["turnos"]["mediana"] == pytest.approx(4.0)
    assert on["prompt_tokens"]["mediana"] == pytest.approx(300)
    assert on["prompt_tokens"]["amplitude"] == pytest.approx(400)
    assert on["total_tokens"]["mediana"] == pytest.approx(330)

    assert off["n"] == 4
    assert off["taxa_sucesso"] == "4/4"
    assert off["custo_real"]["mediana"] == pytest.approx(0.005)
    assert off["latencia"]["mediana"] == pytest.approx(2.5)


def test_rodar_benchmark_registra_n_execucoes_e_grava_json_cru(tmp_path):
    def fake_factory():
        return FakeBenchProvider([
            ProviderResponse(text="Final", tool_calls=[], usage={"prompt": 50, "completion": 10, "total": 60, "cached": 0}, modelo="mock")
        ])

    execucoes = rodar_benchmark(fake_factory, max_turns=1, base_dir=tmp_path, repeticoes=3, cobertura="pago")

    assert len(execucoes) == 27  # 3 tarefas x 3 condições x 3 repetições
    por_celula = {}
    for e in execucoes:
        por_celula.setdefault((e["tarefa_id"], e["condicao"]), []).append(e["repeticao"])
    # Cada célula tem exatamente N repetições, registradas uma a uma (nunca agregadas antes de gravar)
    assert len(por_celula) == 9
    assert all(sorted(reps) == [1, 2, 3] for reps in por_celula.values())

    arquivos = list(tmp_path.glob("bench_*_fake_bench.json"))
    assert len(arquivos) == 1
    payload = json.loads(arquivos[0].read_text(encoding="utf-8"))

    # O cabeçalho de origem vem no topo e os resultados crus ficam preservados
    assert list(payload.keys())[0] == "cabecalho"
    cabecalho = payload["cabecalho"]
    assert cabecalho["driver"] == "fake_bench"
    assert cabecalho["modelo_efetivo"] == "fake-bench-model"
    assert cabecalho["repeticoes"] == 3
    assert cabecalho["cobertura"] == "pago"
    assert cabecalho["janela_tarifaria"] in ("pico", "off-peak")
    assert len(payload["execucoes"]) == 27
    assert len(payload["resumo_por_celula"]) == 9
    # A condição sem poda está declarada como cache OFF + poda OFF
    assert payload["resumo_por_celula"][0]["n"] == 3


def test_resultados_sao_gravados_a_cada_execucao(tmp_path):
    """
    O arquivo é regravado a cada execução: uma coleta longa interrompida não pode custar
    o que já foi pago. Cada chamada da factory enxerga as execuções já persistidas.
    """
    execucoes_visiveis: List[int] = []

    def fake_factory():
        arquivos = list(tmp_path.glob("bench_*_fake_bench.json"))
        if arquivos:
            payload = json.loads(arquivos[0].read_text(encoding="utf-8"))
            execucoes_visiveis.append(len(payload["execucoes"]))
        else:
            execucoes_visiveis.append(0)
        return FakeBenchProvider([
            ProviderResponse(text="Final", tool_calls=[], usage={"prompt": 50, "completion": 10, "total": 60, "cached": 0}, modelo="mock")
        ])

    rodar_benchmark(fake_factory, max_turns=1, base_dir=tmp_path, repeticoes=3)

    # 1ª chamada é o provider de referência do cabeçalho (nada gravado ainda); a partir daí,
    # a execução k enxerga as k-1 anteriores já persistidas.
    assert execucoes_visiveis == [0] + list(range(27))
    payload = json.loads(next(tmp_path.glob("bench_*_fake_bench.json")).read_text(encoding="utf-8"))
    assert len(payload["execucoes"]) == 27


def test_rodar_benchmark_recusa_menos_de_tres_repeticoes(tmp_path):
    def fake_factory():
        return FakeBenchProvider([])

    with pytest.raises(ValueError) as erro:
        rodar_benchmark(fake_factory, max_turns=1, base_dir=tmp_path, repeticoes=REPETICOES_MINIMAS - 1)
    assert "minimo e 3" in str(erro.value)


def test_condicoes_do_benchmark_sao_tres_e_sem_poda_e_declarada():
    assert [c["nome"] for c in CONDICOES_BENCH] == ["ON", "OFF", "SEM_PODA"]
    sem_poda = CONDICOES_BENCH[2]
    # Declarado (não inferido): a terceira condição é cache OFF + poda desligada
    assert sem_poda["cache_habilitado"] is False
    assert sem_poda["poda_habilitada"] is False


def test_janela_tarifaria_e_funcao_pura_da_regra_oficial():
    # Sexta-feira 2026-09-11: pico nas duas janelas, off-peak em todo o resto
    sexta = datetime(2026, 9, 11)
    for hora in (1, 2, 3, 6, 7, 9):
        assert janela_tarifaria(sexta.replace(hour=hora)) == "pico"
    for hora in (0, 4, 5, 10, 12, 23):
        assert janela_tarifaria(sexta.replace(hour=hora)) == "off-peak"

    # Sábado e domingo inteiros são off-peak
    for dia in (datetime(2026, 9, 12), datetime(2026, 9, 13)):
        for hora in range(24):
            assert janela_tarifaria(dia.replace(hour=hora)) == "off-peak"

    # Segunda-feira volta ao pico
    assert janela_tarifaria(datetime(2026, 9, 14, 7)) == "pico"

    # Instante com fuso é convertido para UTC antes da classificação
    # 2026-09-11 22:00 em UTC-3 = 2026-09-12 01:00 UTC (já sábado -> off-peak)
    tz_br = timezone(timedelta(hours=-3))
    assert janela_tarifaria(datetime(2026, 9, 11, 22, 0, tzinfo=tz_br)) == "off-peak"
    # 2026-09-11 05:30 em UTC-3 = 08:30 UTC (sexta, dentro do pico)
    assert janela_tarifaria(datetime(2026, 9, 11, 5, 30, tzinfo=tz_br)) == "pico"


def test_cabecalho_de_origem_tem_todos_os_campos(tmp_path):
    provider = FakeBenchProvider([])
    cabecalho = montar_cabecalho(
        provider=provider,
        repeticoes=5,
        cobertura="credito",
        base_dir=tmp_path,
        momento=datetime(2026, 9, 11, 7, 0, tzinfo=timezone.utc),
    )

    assert cabecalho["driver"] == "fake_bench"
    assert cabecalho["driver_versao"]
    assert cabecalho["modelo_efetivo"] == "fake-bench-model"
    assert cabecalho["reasoning_effort"] == "high"
    assert cabecalho["max_tokens"] == 65536
    assert cabecalho["janela_tarifaria"] == "pico"
    assert set(cabecalho["tarifa_por_1m_tokens"]) == {"input", "output", "cache"}
    assert cabecalho["cobertura"] == "credito"
    assert cabecalho["repeticoes"] == 5
    assert "commit" in cabecalho
    assert "SEM_PODA" in cabecalho["condicoes"]


def test_imprimir_resumo_celulas(capsys):
    execucoes = [
        _execucao("T1", "ON", 1, 0.001, 10.0, 2, 100),
        _execucao("T1", "ON", 2, 0.002, 11.0, 3, 200),
        _execucao("T1", "SEM_PODA", 1, 0.009, 20.0, 5, 900, sucesso=False),
    ]
    tabela = imprimir_resumo_celulas(agregar_por_celula(execucoes))
    assert "RESUMO POR CELULA" in capsys.readouterr().out
    assert "| Tarefa | Condicao | Validas | Abortos | Falhas tarefa | Sucesso novo | Sucesso estrito |" in tabela
    assert "T1 (mapa) | ON | 2 | 0 | 0 | 2/2 | n/a | n/a" in tabela
    assert "T1 (mapa) | SEM_PODA | 1 | 0 | 1 | 0/1 | n/a | n/a" in tabela


# ============================================================================
# Item 10: aborto x falha de tarefa, política de reposição, codificação
# ============================================================================

class ProviderQueAborta(FakeBenchProvider):
    """Simula queda de conexão/API: nada chega a ser medido (0 turnos)."""

    def __init__(self, abortar: bool = True, texto: str = "Final"):
        super().__init__([])
        self.abortar = abortar
        self.texto = texto

    def gerar(self, mensagens: List[Dict[str, Any]], system_prompt: str) -> ProviderResponse:
        if self.abortar:
            raise HarnessError("Erro de conexão com a API DEEPSEEK: [Errno 11001] getaddrinfo failed")
        return ProviderResponse(
            text=self.texto,
            tool_calls=[],
            usage={"prompt": 50, "completion": 10, "total": 60, "cached": 0},
            modelo="mock",
        )


def _payload_json(tmp_path: Path) -> Dict[str, Any]:
    arquivos = list(tmp_path.glob("bench_*_fake_bench.json"))
    assert len(arquivos) == 1, f"esperado 1 JSON de resultado, encontrado {len(arquivos)}"
    return json.loads(arquivos[0].read_text(encoding="utf-8"))


def test_classificar_falha_separa_aborto_de_falha_de_tarefa():
    from harness.bench import classificar_falha

    # Aborto: nada medido (0 turnos) ou a chamada morreu em conexão/API
    assert classificar_falha(0, "Erro de conexão com a API DEEPSEEK", False) == "aborto"
    assert classificar_falha(0, None, False) == "aborto"
    assert classificar_falha(3, "Erro na API DEEPSEEK (HTTP 500)", False) == "aborto"
    assert classificar_falha(4, "Erro inesperado na chamada ao Gemini: timeout", False) == "aborto"
    # Falha de tarefa: houve turnos e a validação não passou
    assert classificar_falha(5, None, False) == "tarefa"
    assert classificar_falha(2, "Arquivo bench_test_math.py não encontrado", False) == "tarefa"
    # Sucesso não é falha
    assert classificar_falha(2, None, True) is None

    # Aborto não entra no denominador da taxa de sucesso
    agregado = agregar_por_celula([
        _execucao("T1", "ON", 1, 0.001, 1.0, 2, 100, sucesso=True),
        _execucao("T1", "ON", 2, 0.001, 1.0, 2, 100, sucesso=False),
        {**_execucao("T1", "ON", 3, 0.0, 0.0, 0, 0, sucesso=False),
         "tipo_falha": "aborto", "erro": "Erro de conexão com a API"},
    ])
    celula = agregado[0]
    assert celula["n"] == 3
    assert celula["validas"] == 2
    assert celula["abortos"] == 1
    assert celula["falhas_tarefa"] == 1
    assert celula["taxa_sucesso"] == "1/2"


def test_aborto_e_reposto_e_nao_entra_no_denominador(tmp_path):
    chamadas = {"n": 0}

    def fake_factory():
        chamadas["n"] += 1
        # A 1ª chamada é o provider de referência do cabeçalho; a 2ª é a 1ª execução (T1/ON)
        return ProviderQueAborta(abortar=(chamadas["n"] == 2))

    execucoes = rodar_benchmark(fake_factory, max_turns=1, base_dir=tmp_path, repeticoes=3)

    abortadas = [e for e in execucoes if e["tipo_falha"] == "aborto"]
    assert len(abortadas) == 1
    assert abortadas[0]["turnos"] == 0
    assert "conex" in abortadas[0]["erro"].lower()

    # A célula abortada foi reposta e fechou com N execuções não abortadas
    celula = [c for c in _payload_json(tmp_path)["resumo_por_celula"] if c["abortos"]][0]
    assert celula["abortos"] == 1
    assert celula["validas"] == 3
    assert celula["taxa_sucesso"].endswith("/3")

    payload = _payload_json(tmp_path)
    assert payload["cabecalho"]["total_reposicoes"] == 1
    assert payload["cabecalho"]["motivo_parada"] is None
    assert len(execucoes) == 28  # 27 válidas + 1 aborto reposto


def test_teto_de_reposicoes_interrompe_a_coleta(tmp_path):
    def fake_factory():
        return ProviderQueAborta(abortar=True)

    execucoes = rodar_benchmark(fake_factory, max_turns=1, base_dir=tmp_path, repeticoes=3)

    # Rodadas 1 e 2: as 3 células de T1 abortam e são repostas (2 reposições cada).
    # Rodada 3: a primeira célula estoura o teto e a coleta PARA — chega-se a 7 registros.
    assert len(execucoes) == 7
    assert all(e["tipo_falha"] == "aborto" for e in execucoes)

    payload = _payload_json(tmp_path)
    motivo = payload["cabecalho"]["motivo_parada"]
    assert motivo is not None
    # A coleta para na primeira célula que estoura o teto (a ordem rotaciona a cada rodada)
    assert "T1/" in motivo and "estourou o teto" in motivo
    assert "reposicoes" in motivo
    assert payload["cabecalho"]["total_reposicoes"] == 6  # 2 por célula, nas 3 células de T1

    # A célula que estourou o teto acumulou 1 tentativa + 2 reposições abortadas
    id_parada = motivo.split("celula ")[1].split(" estourou")[0]  # ex.: "T1/SEM_PODA"
    celula_parada = [
        c for c in payload["resumo_por_celula"] if f"{c['tarefa_id']}/{c['condicao']}" == id_parada
    ][0]
    assert celula_parada["abortos"] == 3
    assert celula_parada["validas"] == 0
    assert celula_parada["taxa_sucesso"] == "0/0"  # nada medido: não há denominador honesto
    assert all(c["validas"] == 0 for c in payload["resumo_por_celula"])


def test_mensagens_com_acento_ficam_legiveis_no_json(tmp_path):
    chamadas = {"n": 0}

    def fake_factory():
        chamadas["n"] += 1
        return ProviderQueAborta(abortar=(chamadas["n"] == 2))

    rodar_benchmark(fake_factory, max_turns=1, base_dir=tmp_path, repeticoes=3)

    conteudo = next(tmp_path.glob("bench_*_fake_bench.json")).read_text(encoding="utf-8")
    payload = json.loads(conteudo)

    abortada = [e for e in payload["execucoes"] if e["tipo_falha"] == "aborto"][0]
    assert "conexão" in abortada["erro"]

    # Nada de dupla codificação no arquivo (UTF-8 lido como latin-1/cp1252)
    mojibake_conexao = "conex" + chr(0xC3) + chr(0xA3) + "o"
    assert mojibake_conexao not in conteudo
    for registro in payload["execucoes"]:
        assert "Ã" not in (registro["detalhe"] or "")

    # Os textos de detalhe (validadores) também saem legíveis
    detalhes = " ".join(e["detalhe"] for e in payload["execucoes"])
    assert "não encontrado no histórico nem no disco" in detalhes


# ============================================================================
# Item 11.1: os dois critérios convivem no registro e no resumo
# ============================================================================

def _hist_com_execucao_ok(nome_arquivo: str, call_id: str = "c1") -> List[Dict[str, Any]]:
    return [
        {
            "role": "model",
            "tool_calls": [{"id": call_id, "name": "executar_comando", "args": {"comando": f"python {nome_arquivo}"}}],
        },
        {
            "role": "tool",
            "name": "executar_comando",
            "tool_call_id": call_id,
            "resultado": {"codigo_saida": 0, "stdout": "", "stderr": ""},
        },
    ]


def test_criterio_estrito_convive_com_o_novo(tmp_path):
    """
    O critério antigo (arquivo com a substring 'assert') fica REGISTRADO ao lado do novo —
    como a regra mudou depois de observar o piloto, escolher o número depois seria o defeito.
    """
    from harness.bench import validar_detalhado

    arquivo = tmp_path / "bench_test_math.py"
    hist = _hist_com_execucao_ok("bench_test_math.py")

    # 1. Caminho de falha por assert: aprovado nos dois critérios
    arquivo.write_text("from bench_math import soma\nassert soma(2, 3) == 5\n", encoding="utf-8")
    resultado = validar_detalhado("geracao-com-teste", hist, tmp_path)
    assert resultado["sucesso"] is True
    assert resultado["criterio_estrito_ok"] is True

    # 2. Caminho de falha por sys.exit: aprovado no novo, reprovado no estrito
    arquivo.write_text(
        "import sys\nfrom bench_math import soma\nif soma(2, 3) != 5:\n    sys.exit(1)\n",
        encoding="utf-8",
    )
    resultado = validar_detalhado("geracao-com-teste", hist, tmp_path)
    assert resultado["sucesso"] is True
    assert resultado["criterio_estrito_ok"] is False

    # 3. Caminho de falha por raise: idem
    arquivo.write_text(
        "from bench_math import soma\nif soma(2, 3) != 5:\n    raise SystemExit(1)\n",
        encoding="utf-8",
    )
    resultado = validar_detalhado("geracao-com-teste", hist, tmp_path)
    assert resultado["sucesso"] is True
    assert resultado["criterio_estrito_ok"] is False

    # 4. T1 não tem arquivo de teste: o critério estrito não se aplica (None), não é "reprovado"
    (tmp_path / "bench_mapa.md").write_text(
        "# Mapa\n- config\n- contexto\n- env\n- loop\n- providers\n- tools\n- usage\n",
        encoding="utf-8",
    )
    resultado = validar_detalhado("mapa", [], tmp_path)
    assert resultado["sucesso"] is True
    assert resultado["criterio_estrito_ok"] is None


def test_resumo_reporta_as_duas_taxas_e_o_registro_grava_o_booleano(tmp_path):
    # 1. Agregação pura: duas execuções válidas, uma delas só passa pelo critério novo;
    #    o aborto no fim não pode zerar a taxa estrita da célula
    agregado = agregar_por_celula([
        {**_execucao("T2", "ON", 1, 0.001, 1.0, 3, 100, sucesso=True), "criterio_estrito_ok": True},
        {**_execucao("T2", "ON", 2, 0.001, 1.0, 3, 100, sucesso=True), "criterio_estrito_ok": False},
        {**_execucao("T2", "ON", 3, 0.001, 1.0, 3, 100, sucesso=False), "criterio_estrito_ok": True},
        {**_execucao("T2", "ON", 3, 0.0, 0.0, 0, 0, sucesso=False),
         "tipo_falha": "aborto", "criterio_estrito_ok": None, "erro": "Erro de conexão"},
    ])
    celula = agregado[0]
    assert celula["n"] == 4
    assert celula["abortos"] == 1
    assert celula["validas"] == 3
    assert celula["taxa_sucesso"] == "2/3"
    assert celula["taxa_sucesso_estrito"] == "1/3"
    assert celula["sucessos_criterio_estrito"] == 1
    assert celula["aprovadas_so_no_criterio_novo"] == 1

    # 2. Coleta real: cada registro grava o booleano (None em T1, que não tem arquivo de teste)
    def fake_factory():
        return FakeBenchProvider([
            ProviderResponse(text="Final", tool_calls=[], usage={"prompt": 50, "completion": 10, "total": 60, "cached": 0}, modelo="mock")
        ])

    rodar_benchmark(fake_factory, max_turns=1, base_dir=tmp_path, repeticoes=3)
    payload = _payload_json(tmp_path)
    for registro in payload["execucoes"]:
        assert "criterio_estrito_ok" in registro
        if registro["tarefa_id"] == "T1":
            assert registro["criterio_estrito_ok"] is None
        else:
            assert registro["criterio_estrito_ok"] is False  # nenhum arquivo de teste foi criado

    celulas_t1 = [c for c in payload["resumo_por_celula"] if c["tarefa_id"] == "T1"]
    assert all(c["taxa_sucesso_estrito"] is None for c in celulas_t1)
