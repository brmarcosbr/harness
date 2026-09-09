"""Módulo de gerenciamento de contexto, estimativa de tokens e poda determinística do histórico."""

import json
from typing import Any, Dict, List, Optional

PREFIXO_CONTEXTO = "\n\n=== CONTEXTO DO PROJETO ===\n"


def estimar_tokens(texto: Any) -> int:
    """
    Função pura que estima a contagem de tokens de forma local e determinística sem dependências externas.
    Utiliza a aproximação padrão de 1 token para cada 4 caracteres (mínimo de 1 token para qualquer entrada).
    """
    if not isinstance(texto, str):
        if texto is None:
            texto = ""
        else:
            texto = str(texto)

    # len(texto) // 4 com piso mínimo de 1
    return max(1, len(texto) // 4)


def estimar_tokens_mensagem(msg: Dict[str, Any]) -> int:
    """Estima tokens de uma única mensagem neutra (texto, tool_calls ou resultado)."""
    tokens = estimar_tokens(msg.get("text", ""))

    if tool_calls := msg.get("tool_calls"):
        tokens += estimar_tokens(json.dumps(tool_calls))

    if resultado := msg.get("resultado"):
        tokens += estimar_tokens(json.dumps(resultado))

    return tokens


def estimar_tokens_historico(mensagens: List[Dict[str, Any]]) -> int:
    """Calcula a soma das estimativas de tokens de todas as mensagens do histórico."""
    return sum(estimar_tokens_mensagem(m) for m in mensagens)


def montar_head(system_prompt: str, contexto_projeto: Optional[str] = None) -> str:
    """
    Monta o bloco de head estável e determinístico (byte-for-byte idêntico em execuções repetidas).
    system_prompt + PREFIXO_CONTEXTO + contexto_projeto (se houver).
    Não adiciona timestamps, hashes dinâmicos ou elementos variáveis.
    Se contexto_projeto for None ou vazio, retorna exclusivamente o system_prompt.
    """
    if not contexto_projeto:
        return system_prompt

    return f"{system_prompt}{PREFIXO_CONTEXTO}{contexto_projeto}"


def _agrupar_turnos(mensagens: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """
    Agrupa mensagens em blocos completos de turnos:
    - Mensagens iniciais 'user' formam seus próprios blocos individuais.
    - Cada bloco de turno subsequente é formado por:
      mensagem role 'model' + todas as mensagens role 'tool' associadas até o próximo 'model' ou 'user'.
    """
    blocos: List[List[Dict[str, Any]]] = []
    i = 0
    n = len(mensagens)

    while i < n:
        msg = mensagens[i]
        role = msg.get("role")

        if role == "user":
            blocos.append([msg])
            i += 1
        elif role == "model":
            bloco = [msg]
            i += 1
            while i < n and mensagens[i].get("role") == "tool":
                bloco.append(mensagens[i])
                i += 1
            blocos.append(bloco)
        elif role == "tool":
            # Caso anômalo: tool isolada sem model anterior
            bloco = [msg]
            i += 1
            while i < n and mensagens[i].get("role") == "tool":
                bloco.append(mensagens[i])
                i += 1
            blocos.append(bloco)
        else:
            blocos.append([msg])
            i += 1

    return blocos


def podar_historico(
    mensagens: List[Dict[str, Any]],
    teto_tokens: int,
    max_turnos_manter: int = 8
) -> List[Dict[str, Any]]:
    """
    Poda o histórico removendo blocos de turnos completos antigos do meio quando o total estimado > teto_tokens.
    Garante:
    - As primeiras mensagens (mensagens user iniciais de contexto e tarefa) NUNCA são removidas.
    - As últimas max_turnos_manter mensagens/turnos (o tail com observações recentes) NUNCA são removidas.
    - Turnos intermediários são removidos EM BLOCOS COMPLETOS (model + tools associadas), garantindo
      paridade de tool_call_id (OpenAI) e functionCall/functionResponse (Gemini).
    """
    if not mensagens:
        return []

    tokens_total = estimar_tokens_historico(mensagens)
    if tokens_total <= teto_tokens:
        return list(mensagens)

    blocos = _agrupar_turnos(mensagens)

    # Determina o head: mensagens user do início (contexto e/ou tarefa)
    idx_primeiro_turno_acao = 0
    for idx, bloco in enumerate(blocos):
        if any(m.get("role") in ("model", "tool") for m in bloco):
            idx_primeiro_turno_acao = idx
            break
    else:
        # Se só existem mensagens user, não há turnos de ação para podar
        return list(mensagens)

    head_blocos = blocos[:idx_primeiro_turno_acao]
    turnos_acao = blocos[idx_primeiro_turno_acao:]

    # Se a quantidade de turnos de ação for menor ou igual a max_turnos_manter,
    # não é seguro podar o tail recente
    if len(turnos_acao) <= max_turnos_manter:
        return list(mensagens)

    # Candidatos a poda: turnos entre o head e os últimos max_turnos_manter
    qtd_candidatos = len(turnos_acao) - max_turnos_manter
    candidatos = turnos_acao[:qtd_candidatos]
    tail_blocos = turnos_acao[qtd_candidatos:]

    # Remove blocos mais antigos dos candidatos um a um até caber no teto ou esgotar candidatos
    candidatos_restantes = list(candidatos)
    while candidatos_restantes:
        mensagens_atuais: List[Dict[str, Any]] = []
        for b in head_blocos + candidatos_restantes + tail_blocos:
            mensagens_atuais.extend(b)

        if estimar_tokens_historico(mensagens_atuais) <= teto_tokens:
            return mensagens_atuais

        # Remove o bloco mais antigo do meio
        candidatos_restantes.pop(0)

    # Retorna o que restou (head_blocos + tail_blocos)
    resultado_final: List[Dict[str, Any]] = []
    for b in head_blocos + tail_blocos:
        resultado_final.extend(b)
    return resultado_final

