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
    resultado = executar_loop(tarefa="fique em loop", provider=provider, max_turns=3)

    captured = capsys.readouterr()
    assert len(resultado.historico) == 7
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


def test_loop_type_error_interno_propaga_harness_error(monkeypatch):
    def fake_tool_com_bug(**kwargs):
        # TypeError interno do corpo da função (e não da assinatura de chamada)
        return 10 + "string_invalida"

    monkeypatch.setitem(
        __import__("harness.loop", fromlist=["TOOL_REGISTRY"]).TOOL_REGISTRY,
        "tool_com_bug",
        fake_tool_com_bug
    )

    resp_call = ProviderResponse(
        text="",
        tool_calls=[{"id": "call_bug", "name": "tool_com_bug", "args": {"x": 1}}],
        usage={"prompt": 20, "completion": 5, "total": 25, "cached": 0},
        modelo="fake-model"
    )
    provider = FakeProvider(respostas=[resp_call])

    with pytest.raises(HarnessError) as exc_info:
        executar_loop(tarefa="executar bug", provider=provider, max_turns=3)

    assert "Erro interno na execução da tool 'tool_com_bug'" in str(exc_info.value)
    assert "unsupported operand type" in str(exc_info.value)


def test_loop_resposta_com_aviso_truncamento_nao_marca_concluido(capsys):
    resp_truncada = ProviderResponse(
        text="Resposta que foi interrompida pelo limite...",
        tool_calls=[],
        usage={"prompt": 30, "completion": 50, "total": 80, "cached": 0},
        modelo="fake-model",
        finish_reason="length",
        aviso="Aviso de parada da API OpenAI/DeepSeek: length"
    )
    provider = FakeProvider(respostas=[resp_truncada])
    resultado = executar_loop(tarefa="tarefa que estoura length", provider=provider, max_turns=3)
    captured = capsys.readouterr()

    # Verifica que o aviso é impresso e a tarefa NÃO é considerada finalizada com sucesso
    assert "[AVISO] Aviso de parada da API OpenAI/DeepSeek: length" in captured.out
    assert "[ATENCAO] Nao concluido: max_turns atingido sem resposta final" in captured.out
    assert "[Resposta Final do Modelo]" not in captured.out
    # A mensagem truncada não é anexada ao histórico como resposta final
    assert len(resultado.historico) == 1
    assert resultado.historico[0]["role"] == "user"


def test_loop_rejeita_kwargs_fora_do_schema():
    # 1. Teste com executar_comando recebendo argumento não declarado no schema (ex: base_dir)
    resp1 = ProviderResponse(
        text="",
        tool_calls=[{
            "id": "call_invalida_cmd",
            "name": "executar_comando",
            "args": {"comando": "dir", "base_dir": "/tentativa/escape"}
        }],
        usage={"prompt": 40, "completion": 20, "total": 60, "cached": 0},
        modelo="fake-model"
    )
    resp2 = ProviderResponse(
        text="Erro tratado com sucesso.",
        tool_calls=[],
        usage={"prompt": 50, "completion": 10, "total": 60, "cached": 0},
        modelo="fake-model"
    )

    provider = FakeProvider(respostas=[resp1, resp2])
    resultado = executar_loop(tarefa="teste schema kwargs", provider=provider, max_turns=3)

    tool_msg = next(m for m in resultado.historico if m.get("role") == "tool" and m.get("tool_call_id") == "call_invalida_cmd")
    res_tool = tool_msg["resultado"]
    assert res_tool["codigo_saida"] == -1
    assert "Argumento não permitido pelo schema da ferramenta 'executar_comando'" in res_tool["stderr"]
    assert "base_dir" in res_tool["stderr"]

    # 2. Teste com ler_arquivo recebendo propriedade inexistente
    resp_ler1 = ProviderResponse(
        text="",
        tool_calls=[{
            "id": "call_invalida_ler",
            "name": "ler_arquivo",
            "args": {"caminho": "app.py", "opcao_extra": True}
        }],
        usage={"prompt": 40, "completion": 20, "total": 60, "cached": 0},
        modelo="fake-model"
    )
    resp_ler2 = ProviderResponse(
        text="Erro tratado.",
        tool_calls=[],
        usage={"prompt": 50, "completion": 10, "total": 60, "cached": 0},
        modelo="fake-model"
    )

    provider_ler = FakeProvider(respostas=[resp_ler1, resp_ler2])
    resultado_ler = executar_loop(tarefa="teste ler_arquivo kwargs", provider=provider_ler, max_turns=3)

    tool_msg_ler = next(m for m in resultado_ler.historico if m.get("role") == "tool" and m.get("tool_call_id") == "call_invalida_ler")
    res_tool_ler = tool_msg_ler["resultado"]
    assert res_tool_ler["sucesso"] is False
    assert "Argumento não permitido pelo schema da ferramenta 'ler_arquivo'" in res_tool_ler["erro"]
    assert "opcao_extra" in res_tool_ler["erro"]


def test_loop_fallback_posicional_emite_aviso_stderr(monkeypatch, capsys):
    # Simula tool que só aceita argumento posicional (não aceita kwargs 'comando=...')
    def fake_tool_posicional(cmd):
        return {"stdout": f"executado: {cmd}", "stderr": "", "codigo_saida": 0}

    monkeypatch.setitem(
        __import__("harness.loop", fromlist=["TOOL_REGISTRY"]).TOOL_REGISTRY,
        "executar_comando",
        fake_tool_posicional
    )

    resp1 = ProviderResponse(
        text="",
        tool_calls=[{"id": "call_pos", "name": "executar_comando", "args": {"comando": "dir"}}],
        usage={"prompt": 50, "completion": 10, "total": 60, "cached": 0},
        modelo="fake-model"
    )
    resp2 = ProviderResponse(
        text="Concluido.",
        tool_calls=[],
        usage={"prompt": 60, "completion": 10, "total": 70, "cached": 0},
        modelo="fake-model"
    )

    provider = FakeProvider(respostas=[resp1, resp2])
    resultado = executar_loop(tarefa="teste fallback posicional", provider=provider, max_turns=3)

    # Verifica que o fallback posicional funcionou
    tool_msg = next(m for m in resultado.historico if m.get("role") == "tool" and m.get("tool_call_id") == "call_pos")
    assert tool_msg["resultado"]["stdout"] == "executado: dir"

    # Verifica que o aviso explícito foi emitido em stderr
    captured = capsys.readouterr()
    assert "[AVISO] Fallback posicional acionado para a tool 'executar_comando' com argumento 'comando'='dir'" in captured.err








# ============================================================================
# Rodada E1: poda ligável, telemetria de prefixo, latência decomposta
# ============================================================================

def _resposta_tool(call_id: str) -> ProviderResponse:
    return ProviderResponse(
        text="",
        tool_calls=[{"id": call_id, "name": "executar_comando", "args": {"comando": "dir"}}],
        usage={"prompt": 10, "completion": 5, "total": 15, "cached": 0},
        modelo="fake-model",
    )


def _resposta_final(texto: str = "Final") -> ProviderResponse:
    return ProviderResponse(
        text=texto,
        tool_calls=[],
        usage={"prompt": 10, "completion": 5, "total": 15, "cached": 0},
        modelo="fake-model",
    )


def test_metricas_registram_esforco_e_teto_efetivos(monkeypatch):
    provider = FakeProvider([_resposta_final()])
    res = executar_loop(tarefa="t", provider=provider, max_turns=1)
    assert res.metricas["reasoning_effort"] == "high"
    assert res.metricas["max_tokens"] == 65536

    # O override por ambiente vale também para o valor registrado na métrica
    monkeypatch.setenv("HARNESS_REASONING_EFFORT", "max")
    monkeypatch.setenv("HARNESS_MAX_TOKENS", "128000")
    provider_override = FakeProvider([_resposta_final()])
    res_override = executar_loop(tarefa="t", provider=provider_override, max_turns=1)
    assert res_override.metricas["reasoning_effort"] == "max"
    assert res_override.metricas["max_tokens"] == 128000


def test_poda_ligavel_muda_o_historico_enviado(monkeypatch):
    import harness.loop as loop_mod

    def fake_tool(comando: str):
        return {"stdout": "x" * 400, "stderr": "", "codigo_saida": 0}

    monkeypatch.setitem(loop_mod.TOOL_REGISTRY, "executar_comando", fake_tool)

    def fabricar_provider():
        # 7 turnos de tool + 1 final = 8 turnos, com blocos de ação acima do max_turnos_manter
        return FakeProvider([_resposta_tool(f"c{i}") for i in range(7)] + [_resposta_final()])

    provider_com_poda = fabricar_provider()
    provider_sem_poda = fabricar_provider()

    res_com_poda = executar_loop(
        tarefa="tarefa longa", provider=provider_com_poda, max_turns=8,
        cache_habilitado=True, poda_habilitada=True, teto_contexto_tokens=120,
    )
    res_sem_poda = executar_loop(
        tarefa="tarefa longa", provider=provider_sem_poda, max_turns=8,
        cache_habilitado=True, poda_habilitada=False, teto_contexto_tokens=120,
    )

    enviados_com_poda = [len(m) for m in provider_com_poda.chamadas]
    enviados_sem_poda = [len(m) for m in provider_sem_poda.chamadas]

    # Sem poda o histórico só cresce; com poda o último envio é menor que o sem poda
    assert enviados_sem_poda == sorted(enviados_sem_poda)
    assert enviados_com_poda[-1] < enviados_sem_poda[-1]

    assert res_com_poda.metricas["poda_habilitada"] is True
    assert res_sem_poda.metricas["poda_habilitada"] is False
    # O teto observado é registrado nas duas condições e é maior sem poda
    assert res_sem_poda.metricas["teto_contexto_observado"] > res_com_poda.metricas["teto_contexto_observado"]
    assert res_com_poda.metricas["teto_contexto_tokens"] == 120


def test_telemetria_de_prefixo_por_turno_on_e_off():
    import time as _time

    class ProviderLento(FakeProvider):
        """Sleep garante timestamps distintos no marcador de cache OFF (prefix-breaker)."""

        def gerar(self, mensagens, system_prompt):
            _time.sleep(0.003)
            return super().gerar(mensagens, system_prompt)

    provider_on = ProviderLento([_resposta_tool("c1"), _resposta_final()])
    res_on = executar_loop(
        tarefa="t", provider=provider_on, max_turns=2,
        contexto_projeto="CONTEXTO ESTAVEL", cache_habilitado=True,
    )
    telemetria_on = res_on.metricas["telemetria_prefixo"]
    assert len(telemetria_on) == 2
    assert [t["prefixo_estavel"] for t in telemetria_on] == [True, True]
    assert telemetria_on[0]["sha256_head"] == telemetria_on[1]["sha256_head"]
    assert res_on.metricas["turnos_prefixo_estavel"] == 2
    assert res_on.metricas["turnos_totais"] == 2
    assert res_on.metricas["prefixo_estavel_pct"] == 100.0

    provider_off = ProviderLento([_resposta_tool("c2"), _resposta_final()])
    res_off = executar_loop(
        tarefa="t", provider=provider_off, max_turns=2,
        contexto_projeto="CONTEXTO ESTAVEL", cache_habilitado=False,
    )
    telemetria_off = res_off.metricas["telemetria_prefixo"]
    assert len(telemetria_off) == 2
    assert [t["prefixo_estavel"] for t in telemetria_off] == [False, False]
    assert telemetria_off[0]["sha256_head"] != telemetria_off[1]["sha256_head"]
    assert res_off.metricas["turnos_prefixo_estavel"] == 0
    assert res_off.metricas["prefixo_estavel_pct"] == 0.0


def test_latencia_decomposta_em_modelo_e_ferramenta(monkeypatch):
    import time as _time
    import harness.loop as loop_mod

    def fake_tool(comando: str):
        _time.sleep(0.01)
        return {"stdout": "ok", "stderr": "", "codigo_saida": 0}

    monkeypatch.setitem(loop_mod.TOOL_REGISTRY, "executar_comando", fake_tool)

    class ProviderLento(FakeProvider):
        def gerar(self, mensagens, system_prompt):
            _time.sleep(0.01)
            return super().gerar(mensagens, system_prompt)

    provider = ProviderLento([_resposta_tool("c1"), _resposta_final()])
    res = executar_loop(tarefa="t", provider=provider, max_turns=2)

    m = res.metricas
    assert m["latencia_modelo_s"] >= 0.01
    assert m["latencia_tools_s"] >= 0.01
    # Os dois acumuladores são não-negativos e a soma não excede a latência total do loop
    assert m["latencia_total_s"] >= m["latencia_modelo_s"] + m["latencia_tools_s"]


# ============================================================================
# Item 10: impressão hostil, codificação e reparação de texto
# ============================================================================

def test_falha_de_impressao_nao_derruba_a_execucao(monkeypatch):
    """
    Medido no piloto: a resposta do modelo com emoji/seta levantava UnicodeEncodeError no
    print e matava o processo DEPOIS de a chamada ter sido paga. A execução tem que terminar
    e as métricas têm que estar gravadas.
    """
    import sys

    texto_com_simbolos = "Resultado \u2705 \u2192 \u2260 pronto"

    class FluxoCp1252:
        """Console hostil: recusa qualquer caractere fora de cp1252, como o redirecionamento no Windows."""

        encoding = "cp1252"

        def __init__(self):
            self.escritas = 0

        def write(self, texto):
            self.escritas += 1
            texto.encode("cp1252")  # levanta UnicodeEncodeError nos símbolos
            return len(texto)

        def flush(self):
            pass

    fluxo = FluxoCp1252()
    monkeypatch.setattr(sys, "stdout", fluxo)

    # O fluxo é hostil de verdade: escrever os símbolos nele levanta
    with pytest.raises(UnicodeEncodeError):
        fluxo.write(texto_com_simbolos)

    provider = FakeProvider([
        ProviderResponse(
            text=texto_com_simbolos,
            tool_calls=[],
            usage={"prompt": 40, "completion": 7, "total": 47, "cached": 0},
            modelo="fake-model",
        )
    ])
    resultado = executar_loop(tarefa="t", provider=provider, max_turns=1)

    # A execução terminou e o dado pago está registrado
    assert resultado.metricas["turnos_usados"] == 1
    assert resultado.metricas["prompt_tokens"] == 40
    assert resultado.metricas["completion_tokens"] == 7
    assert resultado.metricas["custo_real"] > 0
    # O texto íntegro fica no histórico (só a impressão degradou)
    assert resultado.historico[-1]["text"] == texto_com_simbolos
    assert fluxo.escritas > 0


def test_garantir_saida_utf8_e_idempotente_e_marca_pythonutf8(monkeypatch):
    import os
    import sys
    from harness.loop import _FluxoSeguro, garantir_saida_utf8

    monkeypatch.delenv("PYTHONUTF8", raising=False)
    garantir_saida_utf8()
    assert os.environ["PYTHONUTF8"] == "1"

    primeira_envoltura = sys.stdout
    garantir_saida_utf8()
    assert sys.stdout is primeira_envoltura  # não envolve duas vezes
    assert isinstance(sys.stdout, _FluxoSeguro)
