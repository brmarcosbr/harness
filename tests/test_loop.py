"""Testes unitários para o loop de execução multi-turno com FakeProvider (sem rede)."""

from typing import Any, Dict, List
import pytest
from harness.errors import HarnessError
from harness.loop import executar_loop
from harness.providers import Provider, ProviderResponse


class FakeProvider(Provider):
    """Provedor mock para testar o loop sem dependência de rede."""

    def __init__(self, respostas: List[ProviderResponse], nome: str = "fake", modelo: str = "fake-model"):
        self.nome = nome
        self.modelo_ativo = modelo
        self.precos = {"input": 0.30, "output": 2.50, "cache": 0.03}
        self.respostas = respostas
        self.chamadas: List[List[Dict[str, Any]]] = []

    def gerar(self, mensagens: List[Dict[str, Any]], system_prompt: str) -> ProviderResponse:
        self.chamadas.append(mensagens)
        if not self.respostas:
            raise HarnessError("FakeProvider sem mais respostas configuradas.")
        resp = self.respostas.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp

    def tools_schema(self) -> List[Dict[str, Any]]:
        return []


def test_loop_fluxo_completo_com_tool(monkeypatch, capsys):
    # Mock de executar_comando para retorno determinístico
    def fake_executar_comando(comando: str):
        return {
            "stdout": "arquivo_teste.txt\n",
            "stderr": "",
            "codigo_saida": 0
        }

    monkeypatch.setattr("harness.loop.executar_comando", fake_executar_comando)
    # Atualiza também o registry já carregado
    monkeypatch.setitem(
        __import__("harness.loop", fromlist=["TOOL_REGISTRY"]).TOOL_REGISTRY,
        "executar_comando",
        fake_executar_comando
    )

    # Respostas do mock:
    # 1. Tool call para executar_comando "dir"
    resp1 = ProviderResponse(
        text="",
        tool_calls=[{"id": "call_1", "name": "executar_comando", "args": {"comando": "dir"}}],
        usage={"prompt": 50, "completion": 15, "total": 65, "cached": 0},
        modelo="fake-model"
    )
    # 2. Resposta textual final
    resp2 = ProviderResponse(
        text="Os arquivos foram listados com sucesso.",
        tool_calls=[],
        usage={"prompt": 80, "completion": 25, "total": 105, "cached": 10},
        modelo="fake-model"
    )

    provider = FakeProvider(respostas=[resp1, resp2])
    historico = executar_loop(tarefa="liste os arquivos", provider=provider, max_turns=3)

    # Verificações do histórico (4 mensagens neutras)
    assert len(historico) == 4
    assert historico[0] == {"role": "user", "text": "liste os arquivos"}
    assert historico[1] == {
        "role": "model",
        "text": "",
        "tool_calls": [{"id": "call_1", "name": "executar_comando", "args": {"comando": "dir"}}]
    }
    assert historico[2] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "name": "executar_comando",
        "resultado": {"stdout": "arquivo_teste.txt\n", "stderr": "", "codigo_saida": 0}
    }
    assert historico[3] == {
        "role": "model",
        "text": "Os arquivos foram listados com sucesso.",
        "tool_calls": []
    }

    # Verificações da saída no terminal
    captured = capsys.readouterr()
    assert "AGENT HARNESS" in captured.out
    assert "Provider: FAKE | Modelo: fake-model" in captured.out
    assert "RESUMO DA EXECUÇÃO" in captured.out
    assert "Turnos utilizados: 2 de 3" in captured.out
    assert "Os arquivos foram listados com sucesso." in captured.out


def test_loop_max_turns_atingido(monkeypatch, capsys):
    monkeypatch.setitem(
        __import__("harness.loop", fromlist=["TOOL_REGISTRY"]).TOOL_REGISTRY,
        "executar_comando",
        lambda cmd: {"stdout": "ok", "stderr": "", "codigo_saida": 0}
    )

    # Provider sempre retorna tool call sem nunca enviar texto final
    resp_loop = ProviderResponse(
        text="",
        tool_calls=[{"id": "call_loop", "name": "executar_comando", "args": {"comando": "dir"}}],
        usage={"prompt": 10, "completion": 5, "total": 15, "cached": 0},
        modelo="fake-model"
    )

    provider = FakeProvider(respostas=[resp_loop, resp_loop, resp_loop])
    historico = executar_loop(tarefa="fique em loop", provider=provider, max_turns=3)

    captured = capsys.readouterr()
    assert "[ATENCAO] Nao concluido: max_turns atingido sem resposta final" in captured.out
    assert "Turnos utilizados: 3 de 3" in captured.out


def test_loop_provider_lanca_harness_error():
    class ErroProvider(Provider):
        nome = "erro"
        modelo_ativo = "erro-model"
        precos = {"input": 0.30, "output": 2.50, "cache": 0.03}

        def gerar(self, mensagens, system_prompt):
            raise HarnessError("Erro simulado de conexão ou API")

        def tools_schema(self):
            return []

    provider = ErroProvider()
    with pytest.raises(HarnessError) as exc_info:
        executar_loop(tarefa="teste erro", provider=provider)
    assert "Erro simulado de conexão ou API" in str(exc_info.value)

