"""Módulo de ferramentas (tools) e registry canônico executáveis pelo Agent Harness."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Callable, Dict, List, Literal, Optional, Set, Tuple, Union, overload
import harness.config as config
from harness.config import (
    COMMAND_TIMEOUT_SECONDS,
    DIRS_IGNORADOS,
    CAMINHOS_PROTEGIDOS,
    COMANDOS_PERMITIDOS,
)
from harness.env import CHAVES_CARREGADAS_ENV

# Padrões bloqueados de comandos destrutivos de sistema no Windows / cmd.exe
PADROES_BLOQUEADOS = [
    r"\bformat\b",
    r"\bdiskpart\b",
    r"\bshutdown\b",
    r"\brd\s+/[sq]\b|\brd\b.*/[sq]",
    r"\brmdir\s+/[sq]\b|\brmdir\b.*/[sq]",
    r"\brm\s+-r[f]?\b|\brm\s+-[a-z]*r[a-z]*f\b",
    r"\breg\s+delete\b",
    r"\bdel\s+/[sfq]\b|\bdel\b.*/[sfq]",
    r"\berase\s+/[sfq]\b|\berase\b.*/[sfq]",
    r"\bcipher\s+/w\b",
    r"\btaskkill\s+/[fF]\s+/[iI][mM]\b|\btaskkill\b.*(?=.*\/[fF])(?=.*\/[iI][mM])",
    # Wildcards destrutivos: del/erase/rmdir/rd com *
    r"\b(del|erase|rmdir|rd)\b[^&|;]*\*",
    # git clean com -f ou --force
    r"\bgit\s+clean\b[^&|;]*(?:-[a-zA-Z0-9]*f|--force)",
    # type nul > (truncamento de arquivo)
    r"\btype\s+nul\s*>",
    # PowerShell destrutivo: Remove-Item com -Recurse / -Force, ri -r -fo, del -Recurse, etc.
    r"\bremove-item\b[^&|;]*(?:-(?:r|recurse|force|fo))\b",
    r"\bri\s+[^&|;]*(?:-(?:r|recurse|force|fo))\b",
    r"\b(del|erase|rd|rmdir)\s+[^&|;]*-recurse\b",
    r"\bformat-volume\b",
    r"\bstop-computer\b",
    r"\bclear-disk\b",
]

LIMITE_LEITURA_ARQUIVO_BYTES = 200 * 1024  # 200 KB
LIMITE_ESCRITA_ARQUIVO_BYTES = 1024 * 1024  # 1 MB
LIMITE_BUSCA_ARQUIVO_BYTES = 1024 * 1024    # 1 MB
MAX_BUSCA_RESULTADOS = 50
LIMITE_TRUNCAMENTO_SAIDA = 4000

DIRS_IGNORADOS_BUSCA = DIRS_IGNORADOS

# Conjuntos mantidos para inspeção secundária e defesa em profundidade (vestígio pré-whitelist)
LEITURA_COMANDOS = {"type", "cat", "more", "findstr", "head", "tail", "get-content", "gc"}
ESCRITA_COMANDOS = {"copy", "move", "xcopy", "robocopy", "mklink", "attrib", "icacls"}


def caminho_protegido(caminho: Union[str, Path], modo: str = "leitura") -> bool:
    """
    Função pura que avalia se um caminho (relativo ou absoluto) atinge
    um recurso protegido segundo CAMINHOS_PROTEGIDOS.
    Modos suportados: 'leitura' ou 'escrita'.
    """
    c_str = str(caminho).replace("\\", "/").strip()
    if not c_str:
        return False

    # Trata sintaxe git de refs (ex.: HEAD:.env ou refs/heads/main:.env)
    if ":" in c_str and not (len(c_str) >= 2 and c_str[1] == ":" and c_str[0].isalpha()):
        partes_git = c_str.split(":", 1)
        if len(partes_git) == 2 and partes_git[1]:
            c_str = partes_git[1].strip()

    # Remove prefixo './' se houver
    if c_str.startswith("./"):
        c_str = c_str[2:]

    partes = [p.lower() for p in c_str.split("/") if p and p != "."]
    if not partes:
        return False

    caminhos_cfg = getattr(config, "CAMINHOS_PROTEGIDOS", CAMINHOS_PROTEGIDOS)
    bloqueio_total = [p.lower() for p in caminhos_cfg.get("bloqueio_total", [])]
    somente_escrita = [p.lower() for p in caminhos_cfg.get("somente_escrita", [])]

    # Checa bloqueio total (leitura e escrita)
    for bp in bloqueio_total:
        for p in partes:
            if bp == ".env":
                if p.endswith((".example", ".sample", ".template")):
                    continue
                if p == ".env" or p.startswith(".env.") or p.startswith(".env_"):
                    return True
            elif bp == ".git":
                if p == ".git":
                    return True
            else:
                if p == bp:
                    return True

    # Checa somente escrita
    if modo == "escrita":
        for sp in somente_escrita:
            for p in partes:
                if p == sp:
                    return True

    return False


def _destinos_redirecionamento(comando: str) -> List[str]:
    """
    Função pura que extrai todos os destinos de redirecionamento '>' ou '>>' no comando,
    inclusive em comandos encadeados (&, &&, |, ;), suportando descritores numéricos (1>, 2>).
    Trata aspas simples e duplas no caminho e ignora redirecionamentos de descritor como '2>&1'.
    """
    destinos: List[str] = []
    padrao = r"(?:^|[^&|;])(?:\b[0-9])?(?:>>|>)(?!\s*&)\s*(?:\"([^\"]+)\"|'([^']+)'|([^\s>&|;]+))"
    for match in re.finditer(padrao, comando):
        destino = match.group(1) or match.group(2) or match.group(3)
        if destino:
            destinos.append(destino.strip())
    return destinos


def _destino_redirecionamento(comando: str) -> Optional[str]:
    """
    Função pura que extrai o primeiro destino de um redirecionamento '>' ou '>>' no comando.
    Mantida para retrocompatibilidade.
    """
    destinos = _destinos_redirecionamento(comando)
    return destinos[0] if destinos else None


def _destino_redirecionamento_e_protegido(destino: str) -> bool:
    """Verifica se o destino de redirecionamento atinge arquivo/pasta protegida via caminho_protegido."""
    return caminho_protegido(destino, modo="escrita")


def _desmembrar_wrappers(comando: str) -> List[str]:
    """
    Desmembra wrappers como cmd /c "...", cmd /k "...", powershell -c "...", powershell -Command "...".
    Retorna lista contendo o comando original e quaisquer comandos internos extraídos.
    """
    comandos = [comando]
    padroes_wrapper = [
        r"(?:cmd|cmd\.exe)\s+/[ck]\s+(.*)$",
        r"(?:powershell|powershell\.exe|pwsh|pwsh\.exe)\s+-(?:c|command)\s+(.*)$",
    ]
    for p in padroes_wrapper:
        m = re.search(p, comando.strip(), re.IGNORECASE)
        if m:
            interno = m.group(1).strip()
            if (interno.startswith('"') and interno.endswith('"')) or (interno.startswith("'") and interno.endswith("'")):
                interno = interno[1:-1].strip()
            if interno and interno not in comandos:
                comandos.append(interno)
    return comandos


def comando_toca_protegido(comando: str) -> Optional[str]:
    """
    Função pura que analisa se o comando tenta ler ou escrever em caminhos protegidos.
    Cobre:
    - LEITURA: type, cat, more, findstr, head, tail, Get-Content, gc, git show HEAD:.env, git diff .env
    - ESCRITA: copy, move, xcopy, robocopy, mklink, attrib, icacls
    - Redirecionamento de entrada: < arquivo_protegido
    - Normalização de wrappers (cmd /c, powershell -c, etc.) e detecção direta de .env e .git
    Retorna a identificação da infração ou None caso o comando seja seguro.
    """
    # Checagem de redirecionamento de entrada (< arquivo)
    for m in re.finditer(r"(?:^|[^<])<\s*(?:\"([^\"]+)\"|'([^']+)'|([^\s>&|;]+))", comando):
        src = m.group(1) or m.group(2) or m.group(3)
        if src and caminho_protegido(src.strip().strip("\"'"), modo="leitura"):
            return f"redirecionamento_origem_protegida: {src.strip()}"

    todos_comandos = _desmembrar_wrappers(comando)

    for cmd_str in todos_comandos:
        subcomandos = re.split(r"&&|\|\||[&|;]", cmd_str)
        for sub in subcomandos:
            sub = sub.strip()
            if not sub:
                continue
            raw_tokens = re.findall(r'"([^"]+)"|\'([^\']+)\'|(\S+)', sub)
            tokens = [t[0] or t[1] or t[2] for t in raw_tokens if any(t)]
            if not tokens:
                continue

            # Remove aspas externas de todos os tokens
            tokens = [t.strip("\"'") for t in tokens]

            # Ignora wrappers comuns como cmd /c ou powershell -c
            idx = 0
            if tokens[idx].lower() in ("cmd", "cmd.exe") and len(tokens) > idx + 2:
                if tokens[idx + 1].lower() in ("/c", "/k"):
                    idx += 2
            elif tokens[idx].lower() in ("powershell", "powershell.exe", "pwsh", "pwsh.exe") and len(tokens) > idx + 2:
                if tokens[idx + 1].lower() in ("-c", "-command"):
                    idx += 2

            if idx >= len(tokens):
                continue

            cmd_nome = tokens[idx].lower()
            args = tokens[idx + 1:]

            # echo puro não acessa nem modifica caminhos (redirecionamentos são avaliados à parte)
            if cmd_nome == "echo":
                continue

            args_para_varrer = list(args)
            if cmd_nome == "findstr":
                # O primeiro argumento posicional (não-flag) é o padrão de busca, não um arquivo
                idx_padrao = None
                for i, a in enumerate(args_para_varrer):
                    if not a.startswith(("/", "-")):
                        idx_padrao = i
                        break
                if idx_padrao is not None:
                    args_para_varrer.pop(idx_padrao)

            # Varrer tokens de argumentos para detectar caminhos protegidos (.env, .git, etc.)
            for a in args_para_varrer:
                if (a.startswith("-") or a.startswith("/")) and not (":" in a and not a.startswith("--")):
                    continue
                cand = a.split(":", 1)[1] if (":" in a and not a.startswith("-")) else a
                cand_limpo = cand.strip("\"'")
                if caminho_protegido(cand_limpo, modo="leitura") or caminho_protegido(cand_limpo, modo="escrita"):
                    return f"comando_toca_protegido: {cmd_nome} {a}"

            # Git show / git diff
            if cmd_nome == "git" and args:
                sub_git = args[0].lower()
                if sub_git in ("show", "diff"):
                    for a in args[1:]:
                        if a.startswith("-") and not (":" in a and not a.startswith("--")):
                            continue
                        cand = a.split(":", 1)[1] if (":" in a and not a.startswith("-")) else a
                        if caminho_protegido(cand, modo="leitura"):
                            return f"comando_toca_protegido: git {sub_git} {a}"

            # Comandos de leitura
            elif cmd_nome in LEITURA_COMANDOS:
                for a in args_para_varrer:
                    if a.startswith("/") or (a.startswith("-") and len(a) > 1 and not a.startswith("--")):
                        continue
                    if caminho_protegido(a, modo="leitura"):
                        return f"comando_toca_protegido: {cmd_nome} {a}"

            # Comandos de escrita
            elif cmd_nome in ESCRITA_COMANDOS:
                for a in args:
                    if a.startswith("/") or a.startswith("-"):
                        continue
                    if caminho_protegido(a, modo="escrita") or caminho_protegido(a, modo="leitura"):
                        return f"comando_toca_protegido: {cmd_nome} {a}"

    return None


def comando_bloqueado(comando: str) -> Optional[str]:
    """
    Função pura que avalia se o comando contém padrões destrutivos de sistema no cmd.exe
    ou tenta ler/escrever em arquivos ou destinos protegidos, inclusive dentro de wrappers.
    Retorna a string do padrão que casou ou None caso seja permitido.
    """
    todos_comandos = _desmembrar_wrappers(comando)
    for cmd in todos_comandos:
        cmd_lower = cmd.strip().lower()
        for padrao in PADROES_BLOQUEADOS:
            if re.search(padrao, cmd_lower):
                return padrao

        destinos = _destinos_redirecionamento(cmd)
        for dest in destinos:
            if _destino_redirecionamento_e_protegido(dest.strip("\"'")):
                return r"redirecionamento_destino_protegido"

        toca_prot = comando_toca_protegido(cmd)
        if toca_prot:
            return toca_prot

    return None


def truncar_saida(texto: Any, limite: int = LIMITE_TRUNCAMENTO_SAIDA) -> str:
    """
    Função pura que trunca strings longas para não estourar contexto do modelo.
    Se len(texto) > limite, trunca e adiciona sufixo informativo.
    """
    if not isinstance(texto, str):
        texto = str(texto) if texto is not None else ""

    if len(texto) > limite:
        return f"{texto[:limite]}\n[... truncado: {len(texto)} caracteres]"
    return texto


@overload
def resolver_caminho_seguro(
    caminho: Union[str, Path],
    base_dir: Optional[Path] = None,
    operacao: str = "leitura",
    retornar_relativo: Literal[False] = False,
) -> Path: ...


@overload
def resolver_caminho_seguro(
    caminho: Union[str, Path],
    base_dir: Optional[Path] = None,
    operacao: str = "leitura",
    retornar_relativo: Literal[True] = True,
) -> Tuple[Path, Path]: ...


def resolver_caminho_seguro(
    caminho: Union[str, Path],
    base_dir: Optional[Path] = None,
    operacao: str = "leitura",
    retornar_relativo: bool = False,
) -> Union[Path, Tuple[Path, Path]]:
    """
    Valida e resolve o caminho relativo ao base_dir (default cwd).
    Levanta ValueError se tentar path traversal para fora do base_dir,
    se o alvo resolvido escapar da raiz via symlink/junction,
    ou se tentar acessar caminhos protegidos do projeto via caminho_protegido.
    Se retornar_relativo=True, retorna tupla (alvo, relativo).
    """
    raiz = (base_dir or Path.cwd()).resolve()
    caminho_obj = Path(caminho)
    if caminho_obj.is_absolute():
        alvo = caminho_obj.resolve()
    else:
        alvo = (raiz / caminho_obj).resolve()

    try:
        relativo = alvo.relative_to(raiz)
    except ValueError:
        raise ValueError(f"Caminho fora do diretório do projeto: {caminho}")

    if (
        caminho_protegido(relativo, modo=operacao)
        or caminho_protegido(caminho, modo=operacao)
        or caminho_protegido(alvo, modo=operacao)
    ):
        raise ValueError(f"caminho protegido: {caminho}")

    if retornar_relativo:
        return alvo, relativo
    return alvo


_resolver_caminho_seguro = resolver_caminho_seguro


def obter_env_saneado(chaves_ocultas: Optional[Set[str]] = None) -> Dict[str, str]:
    """
    Função pura que retorna cópia de os.environ omitindo variáveis que contenham termos
    sensíveis em qualquer token delimitado por '_' (case-insensitive):
    KEY, SECRET, TOKEN, PASSWORD, PASS, PASSWD, CREDENTIAL, CREDENTIALS, DSN, PRIVATE,
    URL, URI, CONN, AUTH, PWD,
    ou que iniciem por prefixos sensíveis (ex: PASSPHRASE, PASSKEY, PASSWORD, PASSWD)
    ou terminem com sufixos sensíveis conhecidos (ex: APIKEY, CONNECTION_STRING),
    bem como qualquer chave carregada do arquivo .env (CHAVES_CARREGADAS_ENV) ou informada em chaves_ocultas.
    Preserva variáveis de sistema essenciais (PATH, HOME, LANG, USER, etc.).
    """
    env_copia = os.environ.copy()
    chaves_proibidas_extra = set(chaves_ocultas) if chaves_ocultas else set()
    termos_sensiveis = {
        "KEY", "SECRET", "TOKEN", "PASSWORD", "PASS", "PASSWD",
        "CREDENTIAL", "CREDENTIALS", "DSN", "PRIVATE",
        "URL", "URI", "CONN", "AUTH", "PWD"
    }
    prefixos_sensiveis = ("PASSPHRASE", "PASSKEY", "PASSWORD", "PASSWD")
    sufixos_sensiveis = ("APIKEY", "PASSWORD", "SECRET", "TOKEN", "PASSWD", "CONNECTION_STRING")
    variaveis_preservadas = {
        "PATH", "HOME", "HOMEPATH", "HOMEDRIVE", "LANG", "USER",
        "USERNAME", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"
    }

    for k in list(env_copia.keys()):
        k_upper = k.upper()
        if k_upper in variaveis_preservadas and k not in CHAVES_CARREGADAS_ENV and k not in chaves_proibidas_extra:
            continue
        segmentos = set(k_upper.split("_"))
        if (
            segmentos & termos_sensiveis
            or any(k_upper.startswith(p) for p in prefixos_sensiveis)
            or any(k_upper.endswith(s) for s in sufixos_sensiveis)
        ):
            env_copia.pop(k, None)
        elif k in CHAVES_CARREGADAS_ENV or k in chaves_proibidas_extra:
            env_copia.pop(k, None)

    return env_copia


def _tokenizar(comando: str) -> List[str]:
    """
    Função pura que divide o comando por espaços respeitando aspas simples e duplas,
    reconhecendo aspas escapadas (\\" e \\') como caracteres literais.
    O conteúdo entre aspas torna-se um único token sem as aspas envolventes.
    Não expande variáveis nem interpreta metacaracteres (>, &, | são literais).
    Levanta ValueError caso haja aspas não fechadas (desbalanceadas).
    """
    tokens: List[str] = []
    atual: List[str] = []
    em_aspas: Optional[str] = None
    teve_aspas: bool = False
    escapado: bool = False

    for c in comando.strip():
        if escapado:
            if c in ('"', "'", "\\"):
                atual.append(c)
            else:
                atual.append("\\")
                atual.append(c)
            escapado = False
            continue

        if c == "\\":
            escapado = True
            continue

        if em_aspas:
            if c == em_aspas:
                em_aspas = None
            else:
                atual.append(c)
        else:
            if c in ('"', "'"):
                em_aspas = c
                teve_aspas = True
            elif c.isspace():
                if atual or teve_aspas:
                    tokens.append("".join(atual))
                    atual = []
                    teve_aspas = False
            else:
                atual.append(c)

    if escapado:
        atual.append("\\")

    if em_aspas:
        raise ValueError(f"Aspas não fechadas (desbalanceadas) no comando: {comando}")
    if atual or teve_aspas:
        tokens.append("".join(atual))

    return tokens


def validar_comando_whitelist(
    args: List[str],
    base_dir: Optional[Path] = None
) -> Optional[str]:
    """
    Função pura que valida executável e argumentos contra a política de whitelist (W7).
    Retorna None se o comando for permitido, ou uma mensagem de erro detalhada caso bloqueado.
    """
    if not args:
        return "Comando vazio."

    comandos_permitidos = getattr(config, "COMANDOS_PERMITIDOS", COMANDOS_PERMITIDOS)
    executavel = args[0].lower()
    if executavel not in comandos_permitidos:
        return (
            f"Comando bloqueado pela política de segurança: '{args[0]}' não permitido. Este harness executa por whitelist.\n"
            f"Permitidos: dir, type, python <arquivo>.py, git status|diff|log|show|ls-files, findstr, where, echo.\n"
            f"Para manipular arquivos use ler_arquivo / escrever_arquivo / buscar_no_projeto."
        )

    raiz = (base_dir or Path.cwd()).resolve()

    if executavel == "python":
        if len(args) < 2:
            return "Comando python requer um arquivo .py como argumento: 'python <arquivo>.py'."
        alvo_py = args[1]
        if alvo_py.startswith("-"):
            return f"Flags de execução do Python não são permitidas: '{alvo_py}'."
        if not alvo_py.lower().endswith(".py"):
            return f"O alvo de execução do Python deve ser um arquivo .py: '{alvo_py}'."

        partes = alvo_py.replace("\\", "/").split("/")
        if ".." in partes:
            return f"Caminho não pode conter '..' (fora do projeto): '{alvo_py}'."
        if Path(alvo_py).is_absolute() or (len(alvo_py) >= 2 and alvo_py[1] == ":") or alvo_py.startswith(("/", "\\")):
            return f"Caminho absoluto não permitido para script Python: '{alvo_py}'."
        if caminho_protegido(alvo_py, modo="leitura"):
            return f"Acesso a caminho protegido bloqueado: '{alvo_py}'."

        try:
            arquivo_alvo = resolver_caminho_seguro(alvo_py, base_dir=raiz, operacao="leitura")
        except ValueError:
            return f"Arquivo fora do diretório do projeto: '{alvo_py}'."

        if not arquivo_alvo.is_file():
            return f"Arquivo Python não encontrado: '{alvo_py}'."

        # Validação de argumentos passados ao script (args[2:])
        for a in args[2:]:
            valor_para_checar = a.split("=", 1)[1] if ("=" in a and a.startswith("-")) else a

            if a.startswith("-") and "=" not in a:
                continue

            if (
                Path(valor_para_checar).is_absolute()
                or (len(valor_para_checar) >= 2 and valor_para_checar[1] == ":")
                or valor_para_checar.startswith(("/", "\\"))
            ):
                return f"Caminho absoluto não permitido em argumento do Python: '{a}'."

            partes_arg = valor_para_checar.replace("\\", "/").split("/")
            if ".." in partes_arg:
                return f"Caminho não pode conter '..' (fora do projeto): '{a}'."

            if caminho_protegido(valor_para_checar, modo="leitura"):
                return f"Acesso a caminho protegido bloqueado em argumento do Python: '{a}'."

            try:
                alvo_arg = resolver_caminho_seguro(valor_para_checar, base_dir=raiz, operacao="leitura")
            except ValueError:
                return f"Caminho fora do diretório do projeto em argumento do Python: '{a}'."

    elif executavel == "git":
        if len(args) < 2:
            return "Subcomando git ausente. Permitidos: status, diff, log, show, ls-files."
        subcmd = args[1].lower()
        subcomandos_leitura = {"status", "diff", "log", "show", "ls-files"}
        if subcmd not in subcomandos_leitura:
            return f"Subcomando git não permitido: '{args[1]}'. Permitidos: status, diff, log, show, ls-files."

        flags_perigosas = (
            "--no-index", "--ext-diff", "--exec-path", "--paginate",
            "--work-tree", "--git-dir", "--namespace", "--config-env",
            "--upload-pack", "--receive-pack",
            "--no-prefix", "--src-prefix", "--dst-prefix",
            "--diff-merges",
        )
        flags_patch = ("-p", "-u", "--patch", "--textconv", "-c", "--cc")

        for a in args[2:]:
            a_lower = a.lower()
            if (
                a_lower in flags_perigosas
                or any(a_lower.startswith(f"{f}=") or a_lower == f for f in flags_perigosas)
                or a_lower == "--orderfile"
                or a_lower.startswith("--orderfile=")
                or a == "-O"
                or a.startswith("-O=")
                or (a.startswith("-O") and len(a) > 2 and not a[2].isspace())
            ):
                return f"Flag perigosa não permitida no git: '{a}'."

            if (
                a_lower in flags_patch
                or any(a_lower.startswith(f"{f}=") or a_lower == f for f in flags_patch)
            ):
                return f"Flag de exibição de conteúdo/patch não permitida no git: '{a}'."

            if a_lower.startswith("--output") or a_lower.startswith("-o=") or a_lower == "-o":
                return f"Redirecionamento de saída via flag git bloqueado: '{a}'."

            if Path(a).is_absolute() or (len(a) >= 2 and a[1] == ":") or a.startswith(("/", "\\")):
                return f"Caminho absoluto não permitido no git: '{a}'."

            partes = a.replace("\\", "/").split("/")
            if ".." in partes:
                return f"Caminho não pode conter '..' (fora do projeto): '{a}'."

        if subcmd == "diff":
            flags_resumo_diff = {"--stat", "--name-only", "--name-status", "--no-patch", "-s"}
            tem_resumo_diff = any(
                a.lower() in flags_resumo_diff or any(a.lower().startswith(f"{f}=") for f in flags_resumo_diff)
                for a in args[2:]
            )
            if not tem_resumo_diff:
                return (
                    "Comando 'git diff' sem flag de resumo bloqueado. "
                    "Use flags de resumo (--stat, --name-only, --name-status, --no-patch, -s)."
                )

        if subcmd == "show":
            flags_resumo = {"--stat", "--name-only", "--name-status", "--no-patch", "-s"}
            tem_resumo = any(
                a.lower() in flags_resumo or any(a.lower().startswith(f"{f}=") for f in flags_resumo)
                for a in args[2:]
            )
            # O '--' isolado não conta como caminho específico; apenas referências commit:caminho
            tem_caminho_especifico = any(
                (":" in a and not a.startswith("-"))
                for a in args[2:]
            )
            if not tem_resumo and not tem_caminho_especifico:
                return (
                    f"Comando 'git show' sem arquivo específico requer flags de resumo "
                    f"(--stat, --name-only, --name-status, --no-patch, -s): 'git show {' '.join(args[2:])}'."
                )

        for a in args[2:]:

            # Se for revisão com caminho (ex: HEAD:.env ou commit:caminho)
            if ":" in a and not a.startswith("-"):
                rev_partes = a.split(":", 1)
                caminho_rev = rev_partes[1]
                if Path(caminho_rev).is_absolute() or (len(caminho_rev) >= 2 and caminho_rev[1] == ":") or caminho_rev.startswith(("/", "\\")):
                    return f"Caminho absoluto não permitido no git: '{a}'."
                if ".." in caminho_rev.replace("\\", "/").split("/"):
                    return f"Caminho não pode conter '..' (fora do projeto): '{a}'."
                if caminho_protegido(caminho_rev, modo="leitura"):
                    return f"Acesso a caminho protegido bloqueado no git: '{a}'."
                try:
                    alvo_rev = resolver_caminho_seguro(caminho_rev, base_dir=raiz, operacao="leitura")
                except ValueError:
                    return f"Caminho fora do diretório do projeto: '{a}'."
            elif not a.startswith("-"):
                if caminho_protegido(a, modo="leitura"):
                    return f"Acesso a caminho protegido bloqueado no git: '{a}'."
                try:
                    alvo = resolver_caminho_seguro(a, base_dir=raiz, operacao="leitura")
                except ValueError:
                    return f"Caminho fora do diretório do projeto: '{a}'."

    elif executavel in {"type", "findstr", "where", "dir"}:
        if executavel == "type" and len(args) < 2:
            return "Comando type requer ao menos um arquivo: 'type <arquivo>'."

        if executavel == "dir":
            flags_inv = [a for a in args[1:] if a.startswith(("/", "-")) and a.lower() not in ("/b", "-b")]
            if flags_inv:
                return f"Flag não suportada para 'dir': '{flags_inv[0]}'. O comando 'dir' suporta apenas a flag '/b'."

        if executavel == "where":
            for a in args[1:]:
                a_lower = a.lower()
                if a_lower in ("/r", "-r") or a_lower.startswith(("/r:", "-r:")):
                    return f"Flag perigosa não permitida no where: '{a}'."

        if executavel == "findstr":
            flags_findstr_bloqueadas = ("/g", "-g", "/d", "-d", "/s", "-s", "/f", "-f")
            for a in args[1:]:
                a_lower = a.lower()
                if (
                    a_lower in flags_findstr_bloqueadas
                    or any(a_lower.startswith(f"{f}:") for f in flags_findstr_bloqueadas)
                ):
                    return f"Flag perigosa não permitida no findstr: '{a}'."

            nao_flags = [a for a in args[1:] if not a.startswith(("/", "-"))]
            if len(nao_flags) < 2:
                return "Comando findstr requer padrão de busca e ao menos um arquivo alvo: 'findstr <padrão> <arquivo>'."

            for a in nao_flags[1:]:
                if "*" in a or "?" in a:
                    return f"Curingas (* e ?) não são permitidos em alvos do findstr: '{a}'."

            caminhos_para_validar = nao_flags[1:]
        else:
            caminhos_para_validar = [a for a in args[1:] if not a.startswith(("/", "-"))]

        for a in caminhos_para_validar:
            partes = a.replace("\\", "/").split("/")
            if ".." in partes:
                return f"Caminho não pode conter '..' (fora do projeto): '{a}'."
            if Path(a).is_absolute() or (len(a) >= 2 and a[1] == ":") or a.startswith(("/", "\\")):
                return f"Caminho absoluto não permitido: '{a}'."
            if caminho_protegido(a, modo="leitura"):
                return f"Acesso a caminho protegido bloqueado: '{a}'."
            try:
                alvo_cand = resolver_caminho_seguro(a, base_dir=raiz, operacao="leitura")
            except ValueError:
                return f"Caminho fora do diretório do projeto: '{a}'."

    elif executavel == "echo":
        # echo é permitido sem validação de caminhos (sem shell, metacaracteres não redirecionam)
        pass


def _desescapar_caminho_git(p: str) -> str:
    """
    Remove aspas e desescapa sequências de escape do Git em caminhos citados.
    Remove prefixos clássicos (a/, b/, i/, w/) se presentes.
    """
    p = p.strip()
    if p.startswith('"') and p.endswith('"') and len(p) >= 2:
        try:
            import codecs
            p = codecs.escape_decode(p[1:-1].encode("utf-8"))[0].decode("utf-8", errors="replace")
        except Exception:
            p = p[1:-1]

    # Trata prefixos como a/, b/, i/, w/
    if len(p) >= 2 and p[0] in ("a", "b", "i", "w") and p[1] == "/":
        p = p[2:]
    return p


def _filtrar_saida_git(stdout: str) -> str:
    """
    Filtra blocos de saída de diff do git que contenham arquivos protegidos (ex.: .env commitado).
    Aplica política FAIL-SAFE: caso os caminhos do cabeçalho do diff não possam ser
    identificados com certeza, ou caso algum caminho seja protegido, o conteúdo do bloco é redigido.
    Apenas blocos cujos caminhos foram verificados com sucesso e são inofensivos são mantidos intactos.
    """
    if not stdout or "diff --git" not in stdout:
        return stdout

    blocos = re.split(r"(?=^diff --git )", stdout, flags=re.MULTILINE)
    resultado = []
    for bloco in blocos:
        if not bloco.startswith("diff --git "):
            resultado.append(bloco)
            continue

        linhas = bloco.splitlines(keepends=True)
        primeira_linha = linhas[0].rstrip("\r\n")
        resto = primeira_linha[len("diff --git "):].strip()

        # Casa caminhos com ou sem aspas, com ou sem espaços
        m = re.match(r'^(?:"((?:[^"\\]|\\.)*)"|(\S+))\s+(?:"((?:[^"\\]|\\.)*)"|(\S+))$', resto)
        if not m:
            # FAIL-SAFE: formato de cabeçalho não reconhecido com certeza -> REDIGIR
            resultado.append(
                f"{primeira_linha}\n[conteúdo de diff omitido pela política de segurança (fail-safe)]\n"
            )
            continue

        raw_a = m.group(1) if m.group(1) is not None else m.group(2)
        raw_b = m.group(3) if m.group(3) is not None else m.group(4)
        caminho_a = _desescapar_caminho_git(raw_a)
        caminho_b = _desescapar_caminho_git(raw_b)

        # Checa se qualquer dos caminhos é protegido
        eh_protegido = caminho_protegido(caminho_a, modo="leitura") or caminho_protegido(caminho_b, modo="leitura")

        # Checa adicionalmente linhas --- e +++ (defesa em profundidade)
        if not eh_protegido:
            for l in linhas[1:6]:
                l_strip = l.strip()
                if l_strip.startswith("--- ") or l_strip.startswith("+++ "):
                    alvo_sub = l_strip[4:].strip()
                    if alvo_sub and alvo_sub != "/dev/null":
                        c_sub = _desescapar_caminho_git(alvo_sub)
                        if caminho_protegido(c_sub, modo="leitura"):
                            eh_protegido = True
                            break

        if eh_protegido:
            resultado.append(
                f"{primeira_linha}\n[conteúdo de arquivo protegido omitido pela política de segurança]\n"
            )
            continue

        resultado.append(bloco)

    return "".join(resultado)


def executar_comando(comando: str, base_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    Executa o comando por whitelist estrita e com shell=False (W7).
    Aplica blocklist secundária para defesa em profundidade e isolamento de variáveis de ambiente.
    """
    # 1. Tokenização própria
    try:
        args = _tokenizar(comando)
    except ValueError as e:
        return {
            "stdout": "",
            "stderr": f"Erro de sintaxe no comando: {e}",
            "codigo_saida": -1
        }

    if not args:
        return {
            "stdout": "",
            "stderr": "Comando vazio.",
            "codigo_saida": -1
        }

    # 2. Política de execução (camada primária: validação estrita de whitelist e argumentos)
    raiz = (base_dir or Path.cwd()).resolve()
    erro_whitelist = validar_comando_whitelist(args, base_dir=raiz)
    if erro_whitelist:
        return {
            "stdout": "",
            "stderr": erro_whitelist,
            "codigo_saida": -1
        }

    executavel = args[0].lower()

    # 3. Defesa em profundidade (camada secundária)
    # Comandos como echo não usam shell e não executam nada no sistema operacional
    if executavel != "echo":
        padrao = comando_bloqueado(comando)
        if padrao:
            return {
                "stdout": "",
                "stderr": f"Comando bloqueado pela política de segurança (padrão: {padrao})",
                "codigo_saida": -1
            }
    else:
        # Para echo, bloqueia se tentar redirecionamento para destino protegido
        for dest in _destinos_redirecionamento(comando):
            if _destino_redirecionamento_e_protegido(dest.strip("\"'")):
                return {
                    "stdout": "",
                    "stderr": "Comando bloqueado pela política de segurança (padrão: redirecionamento_destino_protegido)",
                    "codigo_saida": -1
                }

    # 4. Handlers nativos sem shell para builtins de comando (type, echo, dir)
    if executavel == "type":
        conteudos = []
        for arq in args[1:]:
            try:
                alvo = resolver_caminho_seguro(arq, base_dir=raiz, operacao="leitura")
            except ValueError as e:
                return {
                    "stdout": "",
                    "stderr": str(e),
                    "codigo_saida": 1
                }
            if not alvo.is_file():
                return {
                    "stdout": "",
                    "stderr": f"O sistema não pode encontrar o arquivo especificado: {arq}",
                    "codigo_saida": 1
                }
            try:
                tamanho = alvo.stat().st_size
                if tamanho > LIMITE_LEITURA_ARQUIVO_BYTES:
                    return {
                        "stdout": "",
                        "stderr": (
                            f"Arquivo excede limite de leitura de "
                            f"{LIMITE_LEITURA_ARQUIVO_BYTES // 1024} KB: {arq} ({tamanho} bytes)"
                        ),
                        "codigo_saida": 1
                    }
                conteudos.append(alvo.read_text(encoding="utf-8", errors="replace"))
            except Exception as e:
                return {
                    "stdout": "",
                    "stderr": f"Erro ao ler arquivo '{arq}': {e}",
                    "codigo_saida": 1
                }
        return {
            "stdout": "".join(conteudos),
            "stderr": "",
            "codigo_saida": 0
        }

    if executavel == "echo":
        texto = " ".join(args[1:]) + "\n"
        return {
            "stdout": texto,
            "stderr": "",
            "codigo_saida": 0
        }

    if executavel == "dir":
        alvo_dir = raiz
        caminhos_espec = [a for a in args[1:] if not a.startswith(("/", "-"))]
        if caminhos_espec:
            try:
                alvo_dir = resolver_caminho_seguro(caminhos_espec[0], base_dir=raiz, operacao="leitura")
            except ValueError:
                return {
                    "stdout": "",
                    "stderr": f"Acesso fora do diretório do projeto não permitido: {caminhos_espec[0]}",
                    "codigo_saida": 1
                }

        if not alvo_dir.exists():
            return {
                "stdout": "",
                "stderr": "O sistema não pode encontrar o arquivo ou diretório especificado.",
                "codigo_saida": 1
            }

        eh_bare = any(a.lower() in ("/b", "-b") for a in args[1:])

        # Se o alvo for um arquivo individual, lista as informações do próprio arquivo
        if alvo_dir.is_file():
            if eh_bare:
                saida = f"{alvo_dir.name}\n"
            else:
                saida = f" Pasta de {alvo_dir.parent}\n\n       {alvo_dir.name}\n"
            return {
                "stdout": saida,
                "stderr": "",
                "codigo_saida": 0
            }

        try:
            itens = sorted(os.listdir(alvo_dir))
        except Exception as e:
            return {"stdout": "", "stderr": f"Erro ao listar diretório: {e}", "codigo_saida": 1}

        if eh_bare:
            saida = "\n".join(itens) + ("\n" if itens else "")
        else:
            linhas = [f" Pasta de {alvo_dir}", ""]
            for item in itens:
                p = alvo_dir / item
                linhas.append(f"{'<DIR>' if p.is_dir() else '     '} {item}")
            saida = "\n".join(linhas) + "\n"

        return {
            "stdout": saida,
            "stderr": "",
            "codigo_saida": 0
        }

    # 5. Handlers para where e findstr em plataformas onde não existem nativamente (ex.: Linux CI)
    if executavel == "where" and not shutil.which("where"):
        caminhos_encontrados = []
        for a in args[1:]:
            p = shutil.which(a)
            if not p and a.lower() == "python":
                p = shutil.which("python3")
            if p:
                caminhos_encontrados.append(p)
        if caminhos_encontrados:
            return {"stdout": "\n".join(caminhos_encontrados) + "\n", "stderr": "", "codigo_saida": 0}
        return {"stdout": "", "stderr": "INFO: Could not find files for the given pattern(s).\n", "codigo_saida": 1}

    if executavel == "findstr" and not shutil.which("findstr"):
        nao_flags = [a for a in args[1:] if not a.startswith(("/", "-"))]
        if len(nao_flags) < 2:
            return {"stdout": "", "stderr": "Uso: findstr [opções] padrão arquivo", "codigo_saida": 2}
        padrao = nao_flags[0]
        linhas_match = []
        for nome_arq in nao_flags[1:]:
            try:
                p_arq = resolver_caminho_seguro(nome_arq, base_dir=raiz, operacao="leitura")
            except ValueError:
                continue
            if not p_arq.is_file():
                continue
            try:
                if p_arq.stat().st_size > LIMITE_BUSCA_ARQUIVO_BYTES:
                    continue
                with open(p_arq, "rb") as f_check:
                    chunk = f_check.read(1024)
                    if b"\x00" in chunk:
                        continue
                with open(p_arq, "r", encoding="utf-8", errors="replace") as f_text:
                    for linha in f_text:
                        if padrao in linha:
                            linhas_match.append(linha.rstrip("\r\n"))
                            if len(linhas_match) >= MAX_BUSCA_RESULTADOS:
                                break
                if len(linhas_match) >= MAX_BUSCA_RESULTADOS:
                    break
            except Exception:
                pass
        if linhas_match:
            return {"stdout": "\n".join(linhas_match) + "\n", "stderr": "", "codigo_saida": 0}
        return {"stdout": "", "stderr": "", "codigo_saida": 1}

    # 6. Execução via subprocess sem shell (shell=False)
    args_exec = list(args)
    if executavel == "python":
        args_exec[0] = sys.executable

    try:
        resultado = subprocess.run(
            args_exec,
            shell=False,
            cwd=str(raiz),
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            encoding="utf-8",
            errors="replace",
            env=obter_env_saneado()
        )
        stdout_final = resultado.stdout
        if executavel == "git":
            stdout_final = _filtrar_saida_git(stdout_final)

        return {
            "stdout": stdout_final,
            "stderr": resultado.stderr,
            "codigo_saida": resultado.returncode
        }
    except subprocess.TimeoutExpired:
        return {
            "stdout": "",
            "stderr": f"Erro: Timeout de execução atingido ({COMMAND_TIMEOUT_SECONDS}s).",
            "codigo_saida": -1
        }
    except Exception as e:
        return {
            "stdout": "",
            "stderr": f"Erro inesperado ao executar comando: {e}",
            "codigo_saida": -1
        }


def ler_arquivo(caminho: str, base_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    Lê o conteúdo de um arquivo de texto dentro do projeto com limite de 200 KB.
    Retorna conteúdo ou erro. Bloqueia caminhos protegidos.
    """
    try:
        alvo = _resolver_caminho_seguro(caminho, base_dir, operacao="leitura")
    except ValueError as e:
        return {"sucesso": False, "erro": str(e), "conteudo": ""}

    if not alvo.exists():
        return {"sucesso": False, "erro": f"Arquivo não encontrado: {caminho}", "conteudo": ""}

    if not alvo.is_file():
        return {"sucesso": False, "erro": f"O caminho não é um arquivo: {caminho}", "conteudo": ""}

    try:
        tamanho = alvo.stat().st_size
        with open(alvo, "r", encoding="utf-8", errors="replace") as f:
            if tamanho > LIMITE_LEITURA_ARQUIVO_BYTES:
                conteudo = f.read(LIMITE_LEITURA_ARQUIVO_BYTES)
                conteudo += f"\n[... truncado: arquivo com {tamanho} bytes excede limite de {LIMITE_LEITURA_ARQUIVO_BYTES} bytes]"
            else:
                conteudo = f.read()

        return {"sucesso": True, "conteudo": conteudo, "tamanho_bytes": tamanho}
    except Exception as e:
        return {"sucesso": False, "erro": f"Falha ao ler arquivo '{caminho}': {e}", "conteudo": ""}


def escrever_arquivo(caminho: str, conteudo: str, base_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    Cria ou sobrescreve um arquivo dentro do projeto com limite de 1 MB.
    Bloqueia caminhos protegidos.
    """
    try:
        alvo = _resolver_caminho_seguro(caminho, base_dir, operacao="escrita")
    except ValueError as e:
        return {"sucesso": False, "erro": str(e), "bytes_escritos": 0}

    dados = conteudo.encode("utf-8")
    if len(dados) > LIMITE_ESCRITA_ARQUIVO_BYTES:
        return {
            "sucesso": False,
            "erro": f"Conteúdo excede limite de escrita de {LIMITE_ESCRITA_ARQUIVO_BYTES} bytes ({len(dados)} bytes).",
            "bytes_escritos": 0
        }

    try:
        alvo.parent.mkdir(parents=True, exist_ok=True)
        with open(alvo, "w", encoding="utf-8") as f:
            f.write(conteudo)
        return {"sucesso": True, "caminho": str(alvo), "bytes_escritos": len(dados)}
    except Exception as e:
        return {"sucesso": False, "erro": f"Falha ao escrever arquivo '{caminho}': {e}", "bytes_escritos": 0}


def _padrao_tem_risco_redos(padrao: str) -> bool:
    """Detecta padrões regex suscetíveis a ReDoS (quantificador aninhado ou alternância repetida)."""
    if re.search(r"\([^)]*[\+\*\{][^)]*\)[\+\*\{]", padrao):
        return True
    if re.search(r"\([^)]*\|[^)]*\)[\+\*\{]", padrao):
        return True
    return False


def _padrao_tem_quantificador_aninhado(padrao: str) -> bool:
    """Detecta padrões regex com quantificadores aninhados suscetíveis a ReDoS (retrocompatibilidade)."""
    return _padrao_tem_risco_redos(padrao)


def buscar_no_projeto(
    padrao: str,
    extensao: Optional[str] = None,
    base_dir: Optional[Path] = None
) -> Dict[str, Any]:
    """
    Varre arquivos texto do projeto buscando padrão regex (máx 50 resultados).
    Ignora diretórios especiais (.git, .venv, etc.) e arquivos > 1 MB ou binários.
    Protegido contra ReDoS e caminhos restritos.
    """
    if _padrao_tem_risco_redos(padrao):
        return {
            "sucesso": False,
            "erro": "padrão potencialmente catastrófico (quantificador aninhado ou alternância repetida)",
            "resultados": []
        }

    raiz = (base_dir or Path.cwd()).resolve()

    try:
        regex = re.compile(padrao)
    except Exception as e:
        return {"sucesso": False, "erro": f"Padrão regex inválido: {e}", "resultados": []}

    ext_filtro = extensao.strip().lower() if extensao else None
    if ext_filtro and not ext_filtro.startswith("."):
        ext_filtro = f".{ext_filtro}"

    resultados: List[str] = []

    for root, dirs, files in os.walk(raiz):
        # Ignora pastas proibidas e protegidas in-place com validação de caminho seguro
        dirs_validos = []
        for d in dirs:
            if d in DIRS_IGNORADOS_BUSCA:
                continue
            p_dir = Path(root) / d
            try:
                resolver_caminho_seguro(p_dir, base_dir=raiz, operacao="leitura")
                dirs_validos.append(d)
            except ValueError:
                continue
        dirs[:] = dirs_validos

        for file in files:
            p = Path(root) / file
            try:
                alvo_seguro, caminho_rel = resolver_caminho_seguro(
                    p, base_dir=raiz, operacao="leitura", retornar_relativo=True
                )
            except ValueError:
                continue

            if ext_filtro and alvo_seguro.suffix.lower() != ext_filtro:
                continue

            try:
                if alvo_seguro.stat().st_size > LIMITE_BUSCA_ARQUIVO_BYTES:
                    continue
                # Lê amostra para checar se é binário
                with open(alvo_seguro, "rb") as f_check:
                    chunk = f_check.read(1024)
                    if b"\x00" in chunk:
                        continue

                with open(alvo_seguro, "r", encoding="utf-8", errors="replace") as f_text:
                    for num_linha, linha in enumerate(f_text, start=1):
                        linha_busca = linha[:500]
                        if regex.search(linha_busca):
                            linha_limpa = linha.strip()
                            resultados.append(f"{caminho_rel}:{num_linha}:{linha_limpa}")
                            if len(resultados) >= MAX_BUSCA_RESULTADOS:
                                return {
                                    "sucesso": True,
                                    "total": len(resultados),
                                    "limite_atingido": True,
                                    "resultados": resultados
                                }
            except Exception:
                continue

    return {
        "sucesso": True,
        "total": len(resultados),
        "limite_atingido": False,
        "resultados": resultados
    }


# ============================================================================
# Registry canônico de Tools do Agent Harness
# ============================================================================

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "executar_comando",
        "description": (
            "Executa comandos estritamente permitidos por whitelist sem shell no diretório do projeto. "
            "Permitidos: dir, type <arquivo>, python <arquivo>.py, git status|diff|log|show|ls-files, "
            "findstr <padrao> <arquivo>, where <executavel>, echo <texto>. "
            "Para ler, editar e buscar arquivos, prefira as ferramentas dedicadas ler_arquivo, "
            "escrever_arquivo e buscar_no_projeto. Retorna stdout, stderr e codigo_saida."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "comando": {
                    "type": "string",
                    "description": "O comando a ser executado (ex: 'dir /b', 'python script.py', 'git status')."
                }
            },
            "required": ["comando"]
        },
        # Nota: timeouts de execução são gerenciados internamente (ex.: executar_comando usa COMMAND_TIMEOUT_SECONDS)
        "handler": executar_comando,
    },
    {
        "name": "ler_arquivo",
        "description": (
            "Lê o conteúdo de um arquivo de texto do diretório do projeto. "
            "Não permite acessar caminhos fora do projeto. Limite de 200 KB."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "caminho": {
                    "type": "string",
                    "description": "Caminho relativo do arquivo a ser lido dentro do projeto."
                }
            },
            "required": ["caminho"]
        },
        "handler": ler_arquivo,
    },
    {
        "name": "escrever_arquivo",
        "description": (
            "Cria ou sobrescreve um arquivo de texto dentro do projeto com o conteúdo informado. "
            "Limite de escrita de 1 MB. Não permite caminhos fora do projeto."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "caminho": {
                    "type": "string",
                    "description": "Caminho relativo do arquivo a ser criado ou sobrescrito."
                },
                "conteudo": {
                    "type": "string",
                    "description": "Conteúdo textual completo a ser gravado no arquivo."
                }
            },
            "required": ["caminho", "conteudo"]
        },
        "handler": escrever_arquivo,
    },
    {
        "name": "buscar_no_projeto",
        "description": (
            "Busca por um padrão regex nos arquivos de código e texto do projeto. "
            "Ignora automaticamente diretórios como .venv, __pycache__ e .git. Máximo de 50 ocorrências."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "padrao": {
                    "type": "string",
                    "description": "Padrão de busca regex ou texto a ser procurado."
                },
                "extensao": {
                    "type": "string",
                    "description": "Extensão de arquivo opcional para filtrar (ex: 'py', 'txt', 'md')."
                }
            },
            "required": ["padrao"]
        },
        "handler": buscar_no_projeto,
    }
]

# Dicionário dinâmico derivado para compatibilidade com o loop e monkeypatches
TOOL_REGISTRY: Dict[str, Callable[..., Any]] = {
    tool["name"]: tool["handler"] for tool in TOOLS
}

