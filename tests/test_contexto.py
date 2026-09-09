"""Testes unitários e de integração para o gerenciamento de contexto, estimativa e poda."""

from pathlib import Path
from typing import Any, Dict, List
import pytest

from harness.contexto import (
    PREFIXO_CONTEXTO,
    estimar_tokens,
    estimar_tokens_historico,
    estimar_tokens_mensagem,
    montar_head,
    podar_historico,
)
from harness.loop import executar_loop
from harness.providers import Provider, ProviderResponse


class FakeContextoProvider(Provider):
    """Provedor mock simples para testar passagem de contexto no loop."""

    def __init__(self, respostas: List[ProviderResponse]):
        self.nome = "fake_contexto"
        self.modelo_ativo = "fake-model"
        self.precos = {"input": 0.30, "output": 2.50, "cache": 0.03}
        self.respostas = respostas
        self.mensagens_recebidas: List[List[Dict[str, Any]]] = []

    def gerar(self, mensagens: List[Dict[str, Any]], system_prompt: str) -> ProviderResponse:
        self.mensagens_recebidas.append(list(mensagens))
        if not self.respostas:
            raise RuntimeError("Sem mais respostas no FakeContextoProvider")
        return self.respostas.pop(0)

    def tools_schema(self) -> List[Dict[str, Any]]:
        return []


def test_estimar_tokens():
    # String vazia deve retornar no mínimo 1
    assert estimar_tokens("") == 1
    assert estimar_tokens(None) == 1

    # String com poucos caracteres
    assert estimar_tokens("abc") == 1
    assert estimar_tokens("1234") == 1
    assert estimar_tokens("12345678") == 2

    # 400 caracteres -> 100 tokens
    texto_400 = "a" * 400
    assert estimar_tokens(texto_400) == 100


def test_estimar_tokens_mensagem_e_historico():
    msg1 = {"role": "user", "text": "a" * 40}  # 10 tokens
    msg2 = {
        "role": "model",
        "text": "b" * 40,  # 10 tokens
        "tool_calls": [{"id": "1", "name": "foo", "args": {"x": 1}}]
    }
    tokens_msg1 = estimar_tokens_mensagem(msg1)
    tokens_msg2 = estimar_tokens_mensagem(msg2)

    assert tokens_msg1 == 10
    assert tokens_msg2 > 10  # texto + json das tool calls

    total = estimar_tokens_historico([msg1, msg2])
    assert total == tokens_msg1 + tokens_msg2


def test_montar_head_deterministico():
    sys_prompt = "Você é um assistente."
    contexto = "# Documentação de regras de negócio\nRegra 1: manter integridade."

    # Sem contexto retorna apenas system prompt
    assert montar_head(sys_prompt, None) == sys_prompt
    assert montar_head(sys_prompt, "") == sys_prompt

    # Com contexto adiciona separador canônico
    head1 = montar_head(sys_prompt, contexto)
    assert head1 == f"{sys_prompt}{PREFIXO_CONTEXTO}{contexto}"

    # Determinismo estrito: 10 execuções produzem bytes idênticos
    for _ in range(10):
        assert montar_head(sys_prompt, contexto) == head1


def test_podar_historico_abaixo_do_teto():
    mensagens = [
        {"role": "user", "text": "Mensagem curta 1"},
        {"role": "model", "text": "Resposta curta 1"},
    ]
    # Com teto alto, o histórico deve permanecer inalterado
    podado = podar_historico(mensagens, teto_tokens=10_000, max_turnos_manter=8)
    assert podado == mensagens


def test_podar_historico_remocao_em_blocos_preserva_head_e_tail():
    # Monta um histórico com:
    # - 2 mensagens user iniciais (head: contexto + tarefa)
    # - 5 turnos de ação do meio (model + tools) com textos longos
    # - 2 turnos recentes (tail)
    head_contexto = {"role": "user", "text": "HEAD_CONTEXTO: " + ("c" * 200)}
    head_tarefa = {"role": "user", "text": "HEAD_TAREFA: " + ("t" * 200)}

    mensagens = [head_contexto, head_tarefa]

    # Turnos antigos 1 a 4 (cada turno tem 1 model + 2 tools com texto grande)
    for turno_id in range(1, 5):
        m_model = {
            "role": "model",
            "text": f"TURNO_{turno_id}_MODEL: " + ("x" * 400),
            "tool_calls": [{"id": f"call_{turno_id}_a", "name": "executar_comando"}]
        }
        m_tool_a = {
            "role": "tool",
            "tool_call_id": f"call_{turno_id}_a",
            "name": "executar_comando",
            "resultado": {"stdout": f"saida_{turno_id}_a: " + ("y" * 400)}
        }
        m_tool_b = {
            "role": "tool",
            "tool_call_id": f"call_{turno_id}_b",
            "name": "executar_comando",
            "resultado": {"stdout": f"saida_{turno_id}_b: " + ("z" * 400)}
        }
        mensagens.extend([m_model, m_tool_a, m_tool_b])

    # 2 turnos recentes (tail)
    for tail_id in range(5, 7):
        m_model = {"role": "model", "text": f"TAIL_{tail_id}_MODEL"}
        mensagens.append(m_model)

    # Total de mensagens antes da poda: 2 (head) + 4*3 (meio) + 2 (tail) = 16 mensagens
    assert len(mensagens) == 16

    # Exigir poda definindo teto menor que o total, mas mantendo tail de 2 turnos
    tokens_total = estimar_tokens_historico(mensagens)
    # Teto que força poda de alguns turnos antigos
    teto = tokens_total // 2

    podado = podar_historico(mensagens, teto_tokens=teto, max_turnos_manter=2)

    # 1. Head deve estar 100% preservado no início
    assert podado[0] == head_contexto
    assert podado[1] == head_tarefa

    # 2. Tail deve estar preservado no final
    assert podado[-1]["text"] == "TAIL_6_MODEL"
    assert podado[-2]["text"] == "TAIL_5_MODEL"

    # 3. Integridade dos blocos: nenhuma mensagem role 'tool' pode existir sem seu 'model' anterior
    for idx, msg in enumerate(podado):
        if msg["role"] == "tool":
            msg_anterior = podado[idx - 1]
            assert msg_anterior["role"] in ("model", "tool"), (
                f"Mensagem tool na posição {idx} ficou órfã!"
            )


def test_executar_loop_com_contexto_projeto(capsys):
    contexto = "REGRAS DO PROJETO: Não use print sem formatação."
    tarefa = "criar script de teste"

    resp_final = ProviderResponse(
        text="Script criado de acordo com as regras.",
        tool_calls=[],
        usage={"prompt": 40, "completion": 10, "total": 50, "cached": 0},
        modelo="fake-model"
    )

    provider = FakeContextoProvider(respostas=[resp_final])

    historico = executar_loop(
        tarefa=tarefa,
        provider=provider,
        max_turns=3,
        contexto_projeto=contexto
    )

    captured = capsys.readouterr().out

    # Verifica banner
    assert "Contexto do projeto:" in captured
    assert "tokens estimados (head)" in captured

    # Verifica resumo
    assert "Tokens estimados do historico final:" in captured

    # Verifica mensagens passadas ao provider
    chamadas = provider.mensagens_recebidas
    assert len(chamadas) == 1
    mensagens_primeiro_turno = chamadas[0]

    # Primeira mensagem: contexto
    assert mensagens_primeiro_turno[0]["role"] == "user"
    assert "=== CONTEXTO DO PROJETO ===" in mensagens_primeiro_turno[0]["text"]
    assert contexto in mensagens_primeiro_turno[0]["text"]

    # Segunda mensagem: tarefa
    assert mensagens_primeiro_turno[1]["role"] == "user"
    assert mensagens_primeiro_turno[1]["text"] == tarefa

    # Terceira mensagem (resposta): model
    assert historico[-1]["role"] == "model"
    assert historico[-1]["text"] == "Script criado de acordo com as regras."


def test_executar_loop_sem_contexto_retrocompatibilidade(capsys):
    tarefa = "listar arquivos"
    resp_final = ProviderResponse(
        text="Arquivos ok.",
        tool_calls=[],
        usage={"prompt": 20, "completion": 5, "total": 25, "cached": 0},
        modelo="fake-model"
    )

    provider = FakeContextoProvider(respostas=[resp_final])

    historico = executar_loop(
        tarefa=tarefa,
        provider=provider,
        max_turns=2,
        contexto_projeto=None
    )

    captured = capsys.readouterr().out
    assert "Contexto do projeto:" not in captured
    assert "Tokens estimados do historico final:" in captured

    chamadas = provider.mensagens_recebidas
    assert len(chamadas[0]) == 1
    assert chamadas[0][0]["role"] == "user"
    assert chamadas[0][0]["text"] == tarefa


def test_cli_contexto_arquivo_inexistente(monkeypatch, capsys):
    from harness.__main__ import main
    monkeypatch.setattr("sys.argv", ["harness", "--contexto", "arquivo_que_nao_existe_123.md"])

    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1

    _, err = capsys.readouterr()
    assert "Arquivo de contexto não encontrado" in err


def test_cli_contexto_arquivo_excede_limite(monkeypatch, capsys, tmp_path):
    from harness.__main__ import main
    arquivo_gigante = tmp_path / "gigante.txt"
    # Cria arquivo com 205 KB (> 200 KB)
    arquivo_gigante.write_bytes(b"x" * (205 * 1024))

    monkeypatch.setattr("sys.argv", ["harness", "--contexto", str(arquivo_gigante)])

    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1

    _, err = capsys.readouterr()
    assert "excede o limite máximo de 200 KB" in err


def test_cli_contexto_valido_passa_para_loop(monkeypatch, tmp_path):
    from harness.__main__ import main
    arquivo_ctx = tmp_path / "contexto.md"
    arquivo_ctx.write_text("# Contexto de Teste", encoding="utf-8")

    loop_args = {}

    def fake_executar_loop(tarefa, provider, max_turns, contexto_projeto=None):
        loop_args["tarefa"] = tarefa
        loop_args["contexto_projeto"] = contexto_projeto
        return []

    monkeypatch.setattr("harness.__main__.obter_api_key", lambda p: "fake_key")
    monkeypatch.setattr("harness.__main__.criar_provider", lambda **kwargs: "fake_provider")
    monkeypatch.setattr("harness.__main__.executar_loop", fake_executar_loop)
    monkeypatch.setattr("sys.argv", ["harness", "--contexto", str(arquivo_ctx), "--tarefa", "tarefa de teste"])

    main()

    assert loop_args["tarefa"] == "tarefa de teste"
    assert loop_args["contexto_projeto"] == "# Contexto de Teste"

