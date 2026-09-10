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
    # 1. Histórico sem chamada de execução deve falhar
    valido, detalhe = validar("geracao-com-teste", [], ".")
    assert valido is False
    assert "Nenhuma execução" in detalhe

    # 2. Histórico com código de saída != 0 deve falhar
    hist_falha = [
        {
            "role": "model",
            "tool_calls": [{"id": "call_1", "name": "executar_comando", "args": {"comando": "python bench_test_math.py"}}],
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

    # 3. Histórico com código de saída 0 deve passar
    hist_sucesso = [
        {
            "role": "model",
            "tool_calls": [{"id": "call_1", "name": "executar_comando", "args": {"comando": "python bench_test_math.py"}}],
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
            "tool_calls": [{"id": "call_c", "name": "executar_comando", "args": {"comando": "python bench_test_contador.py"}}],
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

