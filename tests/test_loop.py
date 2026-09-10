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
    resultado = executar_loop(tarefa="liste os arquivos", provider=provider, max_turns=3)
    historico = resultado.historico

    # Verificações de métricas estruturadas
    assert "turnos_usados" in resultado.metricas
    assert resultado.metricas["turnos_usados"] == 2
    assert resultado.metricas["custo_real"] > 0
    assert resultado.metricas["tarefa"] == "liste os arquivos"

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


def test_loop_executa_ler_arquivo_com_truncamento(monkeypatch, capsys):
    conteudo_muito_longo = "B" * 5000

    def fake_ler_arquivo(caminho: str, base_dir=None):
        return {
            "sucesso": True,
            "conteudo": conteudo_muito_longo,
            "tamanho_bytes": 5000
        }

    monkeypatch.setitem(
        __import__("harness.loop", fromlist=["TOOL_REGISTRY"]).TOOL_REGISTRY,
        "ler_arquivo",
        fake_ler_arquivo
    )

    resp1 = ProviderResponse(
        text="",
        tool_calls=[{"id": "call_ler", "name": "ler_arquivo", "args": {"caminho": "arquivo.txt"}}],
        usage={"prompt": 50, "completion": 10, "total": 60, "cached": 0},
        modelo="fake-model"
    )
    resp2 = ProviderResponse(
        text="Arquivo lido com sucesso.",
        tool_calls=[],
        usage={"prompt": 100, "completion": 20, "total": 120, "cached": 0},
        modelo="fake-model"
    )

    provider = FakeProvider(respostas=[resp1, resp2])
    resultado = executar_loop(tarefa="leia o arquivo", provider=provider, max_turns=3)
    historico = resultado.historico

    assert len(historico) == 4
    tool_msg = historico[2]
    assert tool_msg["role"] == "tool"
    assert tool_msg["name"] == "ler_arquivo"
    # Verifica que o truncamento foi aplicado no conteúdo armazenado no histórico
    conteudo_salvo = tool_msg["resultado"]["conteudo"]
    assert len(conteudo_salvo) < 5000
    assert "[... truncado: 5000 caracteres]" in conteudo_salvo


def test_loop_regressao_max_turns_multi_passo(monkeypatch, capsys):
    """
    Teste de regressão: tarefas multi-passo (3 tool calls sequenciais + resposta final).
    - Com max_turns=8, conclui com 7 mensagens neutras e NÃO exibe aviso de não concluído.
    - Com max_turns=3 no mesmo cenário, estoura os turnos e exibe o aviso.
    """
    # Mock dos handlers no registry para isolamento sem efeitos colaterais
    registry = __import__("harness.loop", fromlist=["TOOL_REGISTRY"]).TOOL_REGISTRY
    monkeypatch.setitem(registry, "executar_comando", lambda comando: {"stdout": "ok", "stderr": "", "codigo_saida": 0})
    monkeypatch.setitem(registry, "ler_arquivo", lambda caminho: {"sucesso": True, "conteudo": "print('ok')", "tamanho_bytes": 11})
    monkeypatch.setitem(registry, "buscar_no_projeto", lambda padrao, extensao=None: {"sucesso": True, "total": 1, "limite_atingido": False, "resultados": ["w2_teste.py:1:print('ok')"]})

    # Respostas mock do modelo: 3 chamadas de ferramentas sequenciais seguidas da síntese
    resp_tool1 = ProviderResponse(
        text="",
        tool_calls=[{"id": "call_1", "name": "executar_comando", "args": {"comando": "python w2_teste.py"}}],
        usage={"prompt": 50, "completion": 15, "total": 65, "cached": 0},
        modelo="fake-model"
    )
    resp_tool2 = ProviderResponse(
        text="",
        tool_calls=[{"id": "call_2", "name": "ler_arquivo", "args": {"caminho": "w2_teste.py"}}],
        usage={"prompt": 70, "completion": 15, "total": 85, "cached": 0},
        modelo="fake-model"
    )
    resp_tool3 = ProviderResponse(
        text="",
        tool_calls=[{"id": "call_3", "name": "buscar_no_projeto", "args": {"padrao": "harness"}}],
        usage={"prompt": 90, "completion": 15, "total": 105, "cached": 0},
        modelo="fake-model"
    )
    resp_final = ProviderResponse(
        text="Tarefa concluída com sucesso: arquivo criado, executado e padrão encontrado.",
        tool_calls=[],
        usage={"prompt": 120, "completion": 30, "total": 150, "cached": 0},
        modelo="fake-model"
    )

    # 1. Execução com max_turns=8 (deve concluir com sucesso)
    provider_8 = FakeProvider(respostas=[resp_tool1, resp_tool2, resp_tool3, resp_final])
    resultado_8 = executar_loop(tarefa="tarefa multi-passo", provider=provider_8, max_turns=8)
    historico_8 = resultado_8.historico
    captured_8 = capsys.readouterr()

    # Histórico de 8 mensagens: user + 3x(model + tool) + model final (1 + 6 + 1 = 8)
    assert len(historico_8) == 8
    assert historico_8[0]["role"] == "user"
    assert historico_8[1]["role"] == "model" and historico_8[1]["tool_calls"][0]["name"] == "executar_comando"
    assert historico_8[2]["role"] == "tool" and historico_8[2]["name"] == "executar_comando"
    assert historico_8[3]["role"] == "model" and historico_8[3]["tool_calls"][0]["name"] == "ler_arquivo"
    assert historico_8[4]["role"] == "tool" and historico_8[4]["name"] == "ler_arquivo"
    assert historico_8[5]["role"] == "model" and historico_8[5]["tool_calls"][0]["name"] == "buscar_no_projeto"
    assert historico_8[6]["role"] == "tool" and historico_8[6]["name"] == "buscar_no_projeto"
    assert historico_8[7]["role"] == "model" and historico_8[7]["text"].startswith("Tarefa concluída")
    assert "[ATENCAO] Nao concluido: max_turns atingido sem resposta final" not in captured_8.out
    assert "Turnos utilizados: 4 de 8" in captured_8.out

    # 2. Execução com max_turns=3 no mesmo cenário (deve estourar o limite e mostrar o aviso)
    provider_3 = FakeProvider(respostas=[resp_tool1, resp_tool2, resp_tool3, resp_final])
    resultado_3 = executar_loop(tarefa="tarefa multi-passo", provider=provider_3, max_turns=3)
    historico_3 = resultado_3.historico
    captured_3 = capsys.readouterr()

    assert len(historico_3) == 7  # user + 3x(model + tool) = 1 + 6 = 7, sem o model final
    assert "[ATENCAO] Nao concluido: max_turns atingido sem resposta final" in captured_3.out
    assert "Turnos utilizados: 3 de 3" in captured_3.out


def test_loop_resposta_vazia_sem_tool_calls_nao_marca_concluido(capsys):
    resp_vazia = ProviderResponse(
        text="",
        tool_calls=[],
        usage={"prompt": 20, "completion": 0, "total": 20, "cached": 0},
        modelo="fake-model",
        finish_reason="SAFETY",
        aviso="Bloqueio de segurança"
    )
    provider = FakeProvider(respostas=[resp_vazia])
    resultado = executar_loop(tarefa="teste bloqueio", provider=provider, max_turns=3)
    captured = capsys.readouterr()

    assert "[AVISO] resposta vazia (possível bloqueio: SAFETY)" in captured.out
    assert "[ATENCAO] Nao concluido: max_turns atingido sem resposta final" in captured.out
    assert len(resultado.historico) == 1
    assert resultado.historico[0]["role"] == "user"



