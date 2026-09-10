"""Testes unitários para o módulo de benchmark (W5) sem chamadas de rede."""

from pathlib import Path
from typing import Any, Dict, List
import pytest

from harness.bench import (
    imprimir_tabela,
    limpar_artefatos,
    rodar_benchmark,
    validar,
)
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
    assert "não encontrado ou não contém asserção" in detalhe

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
                    "args": {"caminho": "bench_test_contador.py", "conteudo": "assert True\nprint('Testes passaram')"}
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


def test_validar_t2_e_t3_exige_assert_e_stdout_nao_vazio(tmp_path):
    # --- T2: geracao-com-teste ---
    arquivo_math = tmp_path / "bench_test_math.py"

    # 1. Sem assert no arquivo
    arquivo_math.write_text("print('resultado: 5')\n", encoding="utf-8")
    hist_t2_ok_cmd = [
        {
            "role": "model",
            "tool_calls": [{"id": "c1", "name": "executar_comando", "args": {"comando": "python bench_test_math.py"}}],
        },
        {
            "role": "tool",
            "name": "executar_comando",
            "tool_call_id": "c1",
            "resultado": {"codigo_saida": 0, "stdout": "resultado: 5", "stderr": ""},
        },
    ]
    valido, detalhe = validar("geracao-com-teste", hist_t2_ok_cmd, tmp_path)
    assert valido is False
    assert "não contém asserção ('assert')" in detalhe

    # 2. Com assert mas stdout vazio
    arquivo_math.write_text("assert 2 + 3 == 5\n", encoding="utf-8")
    hist_t2_empty_stdout = [
        {
            "role": "model",
            "tool_calls": [{"id": "c1", "name": "executar_comando", "args": {"comando": "python bench_test_math.py"}}],
        },
        {
            "role": "tool",
            "name": "executar_comando",
            "tool_call_id": "c1",
            "resultado": {"codigo_saida": 0, "stdout": "   ", "stderr": ""},
        },
    ]
    valido, detalhe = validar("geracao-com-teste", hist_t2_empty_stdout, tmp_path)
    assert valido is False
    assert "stdout não-vazio" in detalhe

    # 3. Com assert e stdout preenchido -> Sucesso
    valido, detalhe = validar("geracao-com-teste", hist_t2_ok_cmd, tmp_path)
    assert valido is True
    assert "stdout não-vazio" in detalhe

    # --- T3: spec-de-arquivo ---
    arquivo_contador = tmp_path / "bench_test_contador.py"

    # 1. Sem assert no arquivo
    arquivo_contador.write_text("print('testes ok')\n", encoding="utf-8")
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
    valido, detalhe = validar("spec-de-arquivo", hist_t3_ok_cmd, tmp_path)
    assert valido is False
    assert "não contém asserção ('assert')" in detalhe

    # 2. Com assert e stdout preenchido -> Sucesso
    arquivo_contador.write_text("assert True\nprint('testes ok')\n", encoding="utf-8")
    valido, detalhe = validar("spec-de-arquivo", hist_t3_ok_cmd, tmp_path)
    assert valido is True
    assert "stdout não-vazio" in detalhe


def test_rodar_benchmark_respeita_base_dir_e_restaura_cwd(tmp_path):
    cwd_inicial = Path.cwd().resolve()

    def fake_factory():
        return FakeBenchProvider([
            ProviderResponse(text="Final", tool_calls=[], usage={"prompt": 50, "completion": 10, "total": 60, "cached": 0}, modelo="mock")
        ])

    resultados = rodar_benchmark(fake_factory, max_turns=1, base_dir=tmp_path)
    cwd_final = Path.cwd().resolve()

    assert cwd_inicial == cwd_final
    assert len(resultados) == 6  # 3 tarefas x 2 regimes (ON, OFF)


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

    # 1. Arquivo no disco sem assert -> Inválido
    arq_math.write_text("print('1 test passed')\n", encoding="utf-8")
    valido, detalhe = validar("geracao-com-teste", hist_cmd, tmp_path)
    assert valido is False
    assert "não contém asserção ('assert')" in detalhe

    # 2. Arquivo no disco com assert -> Válido
    arq_math.write_text("assert True\nprint('1 test passed')\n", encoding="utf-8")
    valido, detalhe = validar("geracao-com-teste", hist_cmd, tmp_path)
    assert valido is True
    assert "stdout não-vazio" in detalhe


def test_rodar_benchmark_ordem_alternada_on_off(tmp_path):
    """Verifica que o benchmark alterna a ordem ON/OFF entre tarefas consecutivas."""
    def fake_factory():
        return FakeBenchProvider([
            ProviderResponse(text="Final", tool_calls=[], usage={"prompt": 50, "completion": 10, "total": 60, "cached": 0}, modelo="mock")
        ])

    resultados = rodar_benchmark(fake_factory, max_turns=1, base_dir=tmp_path)
    # T1: ON, OFF
    assert resultados[0]["tarefa_id"] == "T1" and resultados[0]["cache"] == "ON"
    assert resultados[1]["tarefa_id"] == "T1" and resultados[1]["cache"] == "OFF"
    # T2: OFF, ON
    assert resultados[2]["tarefa_id"] == "T2" and resultados[2]["cache"] == "OFF"
    assert resultados[3]["tarefa_id"] == "T2" and resultados[3]["cache"] == "ON"
    # T3: ON, OFF
    assert resultados[4]["tarefa_id"] == "T3" and resultados[4]["cache"] == "ON"
    assert resultados[5]["tarefa_id"] == "T3" and resultados[5]["cache"] == "OFF"




