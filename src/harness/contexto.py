import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union
from harness.config import DIRS_IGNORADOS, MAX_TURNOS_MANTER_PODA
from harness.tools import caminho_protegido, resolver_caminho_seguro

PREFIXO_CONTEXTO = "\n\n=== CONTEXTO DO PROJETO ===\n"


def sha256_head(head_texto: str) -> str:
    """
    Função pura que calcula o hash SHA-256 do head inteiro e retorna os 16 primeiros caracteres hexadecimais.
    Permite verificar a invariância de prefixo (prefix invariance) de forma compacta e auditável.
    """
    return hashlib.sha256(head_texto.encode("utf-8")).hexdigest()[:16]


def gerar_contexto_repo(
    base_dir: Union[str, Path],
    limite_tokens: int = 30000,
    extensoes: Iterable[str] = (".py", ".md", ".toml", ".txt"),
) -> str:
    """
    Função pura que gera uma representação textual determinística do repositório.
    Varre o diretório ignorando DIRS_IGNORADOS e caminhos protegidos segundo resolver_caminho_seguro,
    ordena arquivos alfabeticamente pelo caminho relativo, e concatena arquivos inteiros
    até atingir o limite_tokens sem partir nenhum arquivo ao meio.
    Pula arquivos binários (com byte nulo) e arquivos maiores que 1 MB.
    """
    base_path = Path(base_dir).resolve()
    ext_tuple = tuple(extensoes)
    arquivos_candidatos: List[Tuple[str, Path]] = []

    for root, dirs, files in os.walk(base_path):
        dirs_validos = []
        for d in dirs:
            if d in DIRS_IGNORADOS or d.endswith(".egg-info"):
                continue
            p_dir = Path(root) / d
            try:
                resolver_caminho_seguro(p_dir, base_dir=base_path, operacao="leitura")
                dirs_validos.append(d)
            except ValueError:
                continue
        dirs[:] = sorted(dirs_validos)

        for file in files:
            p = Path(root) / file
            try:
                alvo_seguro, relativo = resolver_caminho_seguro(
                    p, base_dir=base_path, operacao="leitura", retornar_relativo=True
                )
            except ValueError:
                continue

            caminho_rel = relativo.as_posix()

            if any(file.endswith(ext) for ext in ext_tuple):
                arquivos_candidatos.append((caminho_rel, alvo_seguro))

    # Ordenação estrita por caminho relativo garante determinismo
    arquivos_candidatos.sort(key=lambda x: x[0])

    acumulado = ""
    for caminho_rel, p in arquivos_candidatos:
        try:
            if p.stat().st_size > 1024 * 1024:  # > 1 MB
                continue
            with open(p, "rb") as f_check:
                amostra = f_check.read(1024)
                if b"\x00" in amostra:
                    continue
            conteudo = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        bloco = f"\n===== ARQUIVO: {caminho_rel} =====\n{conteudo}"
        novo_acumulado = acumulado + bloco
        if estimar_tokens(novo_acumulado) > limite_tokens:
            continue
        acumulado = novo_acumulado

    return acumulado


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
    max_turnos_manter: int = MAX_TURNOS_MANTER_PODA
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
    # Se só existem mensagens user, não há turnos de ação para podar
    if idx_primeiro_turno_acao == len(blocos):
        if tokens_total > teto_tokens:
            print(
                f"[AVISO] contexto head+tail excede o teto de {teto_tokens} tokens ({tokens_total}) — reduza o contexto_projeto",
                file=sys.stderr
            )
        return list(mensagens)

    head_blocos = blocos[:idx_primeiro_turno_acao]
    turnos_acao = blocos[idx_primeiro_turno_acao:]

    # Se a quantidade de turnos de ação for menor ou igual a max_turnos_manter,
    # não é seguro podar o tail recente
    if len(turnos_acao) <= max_turnos_manter:
        if tokens_total > teto_tokens:
            print(
                f"[AVISO] contexto head+tail excede o teto de {teto_tokens} tokens ({tokens_total}) — reduza o contexto_projeto",
                file=sys.stderr
            )
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

    tokens_finais = estimar_tokens_historico(resultado_final)
    if tokens_finais > teto_tokens:
        print(
            f"[AVISO] contexto head+tail excede o teto de {teto_tokens} tokens ({tokens_finais}) — reduza o contexto_projeto",
            file=sys.stderr
        )

    return resultado_final

