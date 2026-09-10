"""Módulo de ferramentas (tools) e registry canônico executáveis pelo Agent Harness."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
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
    r"(?:^|[&|;])\s*format(?:\.(?:exe|com))?(?:\s+|$)",
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
LIMITE_SUBPROCESSO_BYTES = 1024 * 1024      # 1 MB
TEMPO_MAXIMO_BUSCA_SEGUNDOS = 10.0          # 10s
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
            if bp in (".env", ".envrc"):
                if p.endswith((".example", ".sample", ".template")):
                    continue
                if (
                    p == bp
                    or p.startswith(f"{bp}.")
                    or p.startswith(f"{bp}_")
                ):
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
        segmentos = set(re.split(r"[_.]", k_upper))
        if (
            segmentos & termos_sensiveis
            or any(k_upper.startswith(p) for p in prefixos_sensiveis)
            or any(k_upper.endswith(s) for s in sufixos_sensiveis)
        ):
            env_copia.pop(k, None)
        elif k in CHAVES_CARREGADAS_ENV or k in chaves_proibidas_extra:
            env_copia.pop(k, None)

    return env_copia


# NOTA DE SEGURANÇA E ARQUITETURA:
# Metacaracteres de shell (&, |, >, <, ;, etc.) são tratados estritamente como literais por decisão
# de design: todos os comandos são executados com shell=False. Como o tokenizador não divide por
# metacaracteres, sequências como 'dir & rm -rf /' ou 'type a.txt > b.txt' tornam esses símbolos
# argumentos posicionais (candidatos a caminho), os quais falham categoricamente na validação
# estrita de caminhos/flags sem disparar subprocessos secundários nem redirecionar I/O.
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


def _validar_flag_status_git(a: str) -> bool:
    """Verifica se uma flag do 'git status' pertence à allowlist estrita de metadados."""
    a_lower = a.lower()
    # Flags longas exatas permitidas
    if a_lower in (
        "--short", "--long", "--branch", "--ignored",
        "--porcelain", "--no-ahead-behind", "--renames", "--no-renames"
    ):
        return True
    # Flags com parâmetros permitidos
    if a_lower.startswith(("--porcelain=", "--untracked-files=", "--ignored=")):
        return True
    # Flags curtas permitidas
    if a_lower in ("-s", "-b", "-u", "-uno", "-unormal", "-uall"):
        return True
    # Combinações de short flags inócuas (apenas 's' e 'b', ex: -sb, -bs)
    if a_lower.startswith("-") and not a_lower.startswith("--"):
        corpo = a_lower[1:]
        if corpo and all(c in ("s", "b") for c in corpo):
            return True
    return False


def _validar_flag_ls_files_git(a: str) -> bool:
    """Verifica se uma flag do 'git ls-files' pertence à allowlist estrita de metadados."""
    a_lower = a.lower()
    if a_lower in (
        "--cached", "--others", "--stage", "--deleted",
        "--modified", "--ignored", "--full-name",
        "--exclude-standard", "--error-unmatch", "--deduplicate"
    ):
        return True
    if a_lower in ("-c", "-o", "-s", "-t", "-d", "-m", "-i"):
        return True
    # Combinações de short flags inócuas ('c', 'o', 's', 't', 'd', 'm', 'i')
    if a_lower.startswith("-") and not a_lower.startswith("--"):
        corpo = a_lower[1:]
        if corpo and all(c in ("c", "o", "s", "t", "d", "m", "i") for c in corpo):
            return True
    return False


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
            f"Permitidos: dir, type, python <arquivo>.py, git status|ls-files|log --oneline, findstr, where, echo.\n"
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
            return "Subcomando git ausente. Permitidos apenas metadados: status, ls-files, log --oneline."
        subcmd = args[1].lower()

        if subcmd in ("diff", "show"):
            return (
                f"Subcomando git não permitido: '{args[1]}'. Permitidos apenas metadados: "
                "status, ls-files, log --oneline. Para ler arquivos use ler_arquivo."
            )

        subcomandos_permitidos = {"status", "ls-files", "log"}
        if subcmd not in subcomandos_permitidos:
            return (
                f"Subcomando git não permitido: '{args[1]}'. Permitidos apenas metadados: "
                "status, ls-files, log --oneline."
            )

        # Rejeição imediata de pathspec magic ':' em qualquer subcomando git
        for a in args[2:]:
            if a.startswith(":"):
                return f"Pathspec magic ':' não permitido no git: '{a}'."

        if subcmd == "log":
            tem_oneline = any(a.lower() == "--oneline" for a in args[2:])
            if not tem_oneline:
                return (
                    "Comando 'git log' requer a flag '--oneline': "
                    "'git log --oneline [-n <N>] [--stat]'."
                )

            # Permite apenas flags inócuas de metadados, limite de commits e caminhos seguros
            for a in args[2:]:
                a_lower = a.lower()
                if a_lower == "--oneline":
                    continue
                if a_lower in ("-n", "--max-count", "--stat"):
                    continue
                if a_lower.startswith("-n") and a_lower[2:].isdigit():
                    continue
                if a_lower.startswith("--max-count=") and a_lower.split("=", 1)[1].isdigit():
                    continue
                if a.isdigit() or (a.startswith("-") and a[1:].isdigit()):
                    continue

                if not a.startswith("-"):
                    if Path(a).is_absolute() or (len(a) >= 2 and a[1] == ":") or a.startswith(("/", "\\")):
                        return f"Caminho absoluto não permitido no git: '{a}'."
                    partes = a.replace("\\", "/").split("/")
                    if ".." in partes:
                        return f"Caminho não pode conter '..' (fora do projeto): '{a}'."
                    if caminho_protegido(a, modo="leitura"):
                        return f"Acesso a caminho protegido bloqueado no git: '{a}'."
                    try:
                        resolver_caminho_seguro(a, base_dir=raiz, operacao="leitura")
                        continue
                    except ValueError:
                        return f"Caminho fora do diretório do projeto: '{a}'."

                return (
                    f"Argumento não permitido para 'git log --oneline': '{a}'. "
                    "Permitido apenas '-n <N>', '-n<N>', '--max-count=<N>', '--stat' e caminhos de arquivos seguros."
                )

        elif subcmd == "status":
            for a in args[2:]:
                if a.startswith("-"):
                    if not _validar_flag_status_git(a):
                        return (
                            f"Flag não permitida para 'git status': '{a}'. "
                            "Permitidas apenas flags de metadados: --short, -s, --porcelain, "
                            "--branch, -b, --untracked-files, -u, --ignored, --long."
                        )
                else:
                    if Path(a).is_absolute() or (len(a) >= 2 and a[1] == ":") or a.startswith(("/", "\\")):
                        return f"Caminho absoluto não permitido no git: '{a}'."
                    partes = a.replace("\\", "/").split("/")
                    if ".." in partes:
                        return f"Caminho não pode conter '..' (fora do projeto): '{a}'."
                    if caminho_protegido(a, modo="leitura"):
                        return f"Acesso a caminho protegido bloqueado no git: '{a}'."
                    try:
                        resolver_caminho_seguro(a, base_dir=raiz, operacao="leitura")
                    except ValueError:
                        return f"Caminho fora do diretório do projeto: '{a}'."

        elif subcmd == "ls-files":
            for a in args[2:]:
                if a.startswith("-"):
                    if not _validar_flag_ls_files_git(a):
                        return (
                            f"Flag não permitida para 'git ls-files': '{a}'. "
                            "Permitidas apenas flags de metadados: --cached, -c, --others, -o, "
                            "--stage, -s, -t, --full-name, --deleted, --modified, --exclude-standard."
                        )
                else:
                    if Path(a).is_absolute() or (len(a) >= 2 and a[1] == ":") or a.startswith(("/", "\\")):
                        return f"Caminho absoluto não permitido no git: '{a}'."
                    partes = a.replace("\\", "/").split("/")
                    if ".." in partes:
                        return f"Caminho não pode conter '..' (fora do projeto): '{a}'."
                    if caminho_protegido(a, modo="leitura"):
                        return f"Acesso a caminho protegido bloqueado no git: '{a}'."
                    try:
                        resolver_caminho_seguro(a, base_dir=raiz, operacao="leitura")
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
            tem_flag_c = False
            for a in args[1:]:
                a_lower = a.lower()
                if a_lower.startswith(("/c:", "-c:")):
                    tem_flag_c = True
                    continue

                # Bloqueia flags perigosas: qualquer flag contendo 's', 'd', 'g' ou 'f'
                # cobre /s, -s, /si, /is, /fs:, /ds:, /g:, /d:, etc.
                if a_lower.startswith(("/", "-")):
                    flag_corpo = a_lower[1:]
                    if any(c in flag_corpo for c in ("s", "d", "g", "f")):
                        return f"Flag perigosa não permitida no findstr: '{a}'."

            nao_flags = [a for a in args[1:] if not a.startswith(("/", "-"))]
            if tem_flag_c:
                if len(nao_flags) < 1:
                    return "Comando findstr com /c requer ao menos um arquivo alvo: 'findstr /c:<padrão> <arquivo>'."
                caminhos_para_validar = nao_flags
            else:
                if len(nao_flags) < 2:
                    return "Comando findstr requer padrão de busca e ao menos um arquivo alvo: 'findstr <padrão> <arquivo>'."
                caminhos_para_validar = nao_flags[1:]

            for a in caminhos_para_validar:
                if "*" in a or "?" in a:
                    return f"Curingas (* e ?) não são permitidos em alvos do findstr: '{a}'."
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
    Decodifica escapes octais e caracteres escapados do Git (latin-1 para utf-8).
    """
    p = p.strip()
    if p.startswith('"') and p.endswith('"') and len(p) >= 2:
        p = p[1:-1]

    if "\\" in p:
        try:
            import codecs
            b = codecs.escape_decode(p.encode("latin-1", errors="replace"))[0]
            p = b.decode("utf-8", errors="replace")
        except Exception:
            pass

    # Trata prefixos como a/, b/, i/, w/
    if len(p) >= 2 and p[0] in ("a", "b", "i", "w") and p[1] == "/":
        p = p[2:]
    return p


def _expandir_renomeacoes(texto: str) -> List[str]:
    """
    Expande possíveis renomeações de caminho do Git ({a => b}, a => b, a -> b)
    retornando todos os caminhos candidatos (origem e destino).
    """
    candidatos: List[str] = []
    m = re.search(r"(\S*)\{([^=>]*)=>\s*([^}]*)\}(\S*)", texto)
    if m:
        pref, left, right, suff = m.group(1).strip(), m.group(2).strip(), m.group(3).strip(), m.group(4).strip()
        candidatos.extend([f"{pref}{left}{suff}", f"{pref}{right}{suff}"])
    if "=>" in texto:
        partes = [p.strip() for p in re.split(r"\s*=>\s*", texto)]
        if len(partes) >= 2:
            left_token = partes[0].split()[-1] if partes[0].split() else ""
            right_token = partes[1].split()[0] if partes[1].split() else ""
            candidatos.extend([left_token, right_token])
    if "->" in texto:
        partes = [p.strip() for p in re.split(r"\s*->\s*", texto)]
        if len(partes) >= 2:
            left_token = partes[0].split()[-1] if partes[0].split() else ""
            right_token = partes[1].split()[0] if partes[1].split() else ""
            candidatos.extend([left_token, right_token])
    return [c for c in candidatos if c]


def _linha_toca_protegido_git(linha: str) -> bool:
    """
    Avalia se uma linha ou registro de saída de comando git cita algum caminho protegido
    (como .env, .git, etc.), incluindo status, ls-files e log --oneline.
    """
    linha_limpa = linha.strip("\r\n\t \x00")
    if not linha_limpa:
        return False

    # 1. Checa expansões de renomeação na linha inteira
    for c in _expandir_renomeacoes(linha_limpa):
        if caminho_protegido(_desescapar_caminho_git(c), modo="leitura"):
            return True

    # 2. Se houver prefixos como 'modified: caminho' ou 'deleted: caminho'
    if ":" in linha_limpa:
        c_resto = linha_limpa.split(":", 1)[1].strip()
        if caminho_protegido(_desescapar_caminho_git(c_resto), modo="leitura"):
            return True
        for c in _expandir_renomeacoes(c_resto):
            if caminho_protegido(_desescapar_caminho_git(c), modo="leitura"):
                return True

    # 3. Varrer cada token individual separado por espaços, tabs ou NUL (\x00)
    for t in re.split(r"[\t\s\x00]+", linha_limpa):
        t_limpo = t.strip("\"'`,:\x00")
        if caminho_protegido(_desescapar_caminho_git(t_limpo), modo="leitura"):
            return True
        for c in _expandir_renomeacoes(t_limpo):
            if caminho_protegido(_desescapar_caminho_git(c), modo="leitura"):
                return True

    return False


def _filtrar_saida_git(stdout: str) -> Tuple[str, int]:
    """
    Filtra a saída textual dos subcomandos git permitidos (status, ls-files, log --oneline).
    Omite completamente qualquer linha ou registro NUL-delimitado que mencione arquivos protegidos.
    Retorna tupla (saida_filtrada, total_linhas_omitidas).
    """
    if not stdout:
        return stdout, 0

    omitidas = 0
    # Se a saída contiver registros separados por NUL (\x00), divide por NUL
    if "\x00" in stdout:
        termina_com_nul = stdout.endswith("\x00")
        registros = stdout.split("\x00")
        if termina_com_nul and registros and registros[-1] == "":
            registros.pop()
        registros_filtrados = []
        for r in registros:
            if _linha_toca_protegido_git(r):
                omitidas += 1
            else:
                registros_filtrados.append(r)
        saida = "\x00".join(registros_filtrados)
        if termina_com_nul and saida:
            saida += "\x00"
        return saida, omitidas

    linhas = stdout.splitlines(keepends=True)
    linhas_filtradas = []
    for l in linhas:
        if _linha_toca_protegido_git(l):
            omitidas += 1
        else:
            linhas_filtradas.append(l)
    return "".join(linhas_filtradas), omitidas


def _executar_subprocesso_com_limite(
    args_exec: List[str],
    cwd: str,
    env: Dict[str, str],
    timeout: int,
    limite_bytes: int = LIMITE_SUBPROCESSO_BYTES,
) -> Tuple[str, str, int]:
    """
    Executa comando em subprocesso (shell=False) lendo saídas com teto de memória.
    Lê stdout e stderr em chunks sem bufferizar dados além de limite_bytes.
    """
    proc = subprocess.Popen(
        args_exec,
        shell=False,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    stdout_chunks: List[bytes] = []
    stderr_chunks: List[bytes] = []
    flags = {"stdout_truncado": False, "stderr_truncado": False}

    def _reader(pipe, chunks: List[bytes], flag_chave: str):
        total = 0
        try:
            while True:
                chunk = pipe.read(64 * 1024)
                if not chunk:
                    break
                if not flags[flag_chave]:
                    if total + len(chunk) > limite_bytes:
                        sobra = limite_bytes - total
                        if sobra > 0:
                            chunks.append(chunk[:sobra])
                        flags[flag_chave] = True
                    else:
                        chunks.append(chunk)
                        total += len(chunk)
        except Exception:
            pass
        finally:
            try:
                pipe.close()
            except Exception:
                pass

    t_out = threading.Thread(target=_reader, args=(proc.stdout, stdout_chunks, "stdout_truncado"))
    t_err = threading.Thread(target=_reader, args=(proc.stderr, stderr_chunks, "stderr_truncado"))
    t_out.daemon = True
    t_err.daemon = True
    t_out.start()
    t_err.start()

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        t_out.join(timeout=1.0)
        t_err.join(timeout=1.0)
        raise

    t_out.join(timeout=2.0)
    t_err.join(timeout=2.0)

    out_str = b"".join(stdout_chunks).decode("utf-8", errors="replace")
    err_str = b"".join(stderr_chunks).decode("utf-8", errors="replace")

    if flags["stdout_truncado"]:
        out_str += f"\n[... saída de stdout truncada no limite de segurança de {limite_bytes // 1024} KB]"
    if flags["stderr_truncado"]:
        err_str += f"\n[... saída de stderr truncada no limite de segurança de {limite_bytes // 1024} KB]"

    return out_str, err_str, proc.returncode


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
    # A blocklist de padrões destrutivos (PADROES_BLOQUEADOS) é aplicada apenas aos VERBOS
    # do comando e aos comandos internos extraídos de wrappers (cmd /c, powershell -c).
    # Ela NUNCA varre o corpo de argumentos arbitrários de leitura/busca (como findstr ou type),
    # pois com shell=False nenhum executável permitido executa comandos a partir de argumentos.
    verbos_a_checar = [executavel]
    for cmd_w in _desmembrar_wrappers(comando):
        cmd_w_limpo = cmd_w.strip()
        if cmd_w_limpo:
            try:
                tokens_w = _tokenizar(cmd_w_limpo)
                if tokens_w:
                    v = tokens_w[0].lower()
                    if v not in verbos_a_checar:
                        verbos_a_checar.append(v)
            except Exception:
                partes = cmd_w_limpo.split()
                if partes:
                    v = partes[0].strip("\"'").lower()
                    if v not in verbos_a_checar:
                        verbos_a_checar.append(v)

    verbos_destrutivos_retaguarda = {
        "format", "diskpart", "shutdown", "rm", "del", "erase", "rd", "rmdir",
        "reg", "cipher", "taskkill", "format-volume", "stop-computer", "clear-disk", "remove-item",
    }
    for v in verbos_a_checar:
        padrao = comando_bloqueado(v)
        if not padrao and v in verbos_destrutivos_retaguarda:
            padrao = f"\\b{v}\\b"
        if padrao:
            return {
                "stdout": "",
                "stderr": f"Comando bloqueado pela política de segurança (padrão: {padrao})",
                "codigo_saida": -1
            }

    # Bloqueio de redirecionamentos para destino protegido
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
                texto_lido = alvo.read_text(encoding="utf-8", errors="replace")
                if len(args[1:]) > 1:
                    conteudos.append(f"\n{arq}\n\n{texto_lido}")
                else:
                    conteudos.append(texto_lido)
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
        padrao = None
        alvos = []
        for a in args[1:]:
            if a.lower().startswith(("/c:", "-c:")):
                padrao = a[3:].strip("\"'")
            elif not a.startswith(("/", "-")):
                if padrao is None:
                    padrao = a
                else:
                    alvos.append(a)

        if padrao is None or not alvos:
            return {"stdout": "", "stderr": "Uso: findstr [opções] padrão arquivo", "codigo_saida": 2}

        linhas_match = []
        for nome_arq in alvos:
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

    # 6. Execução via subprocess sem shell (shell=False) com teto de memória de 1 MB
    args_exec = list(args)
    if executavel == "python":
        args_exec[0] = sys.executable

    try:
        stdout_final, stderr_final, rc = _executar_subprocesso_com_limite(
            args_exec=args_exec,
            cwd=str(raiz),
            env=obter_env_saneado(),
            timeout=COMMAND_TIMEOUT_SECONDS,
            limite_bytes=LIMITE_SUBPROCESSO_BYTES
        )
        if executavel == "git":
            stdout_final, linhas_omitidas = _filtrar_saida_git(stdout_final)
            if linhas_omitidas > 0:
                sys.stderr.write(
                    f"[AUDITORIA] Redator git: {linhas_omitidas} linha(s) protegida(s) omitida(s) da saída.\n"
                )

        return {
            "stdout": stdout_final,
            "stderr": stderr_final,
            "codigo_saida": rc
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
    if re.search(r"\([^)]*[\+\*\?\{][^)]*\)[\+\*\?\{]", padrao):
        return True
    if re.search(r"\)[\+\*\?\{][^)]*\)[\+\*\?\{]", padrao):
        return True
    if re.search(r"\([^)]*\|[^)]*\)[\+\*\?\{]", padrao):
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
    t_inicio = time.time()

    for root, dirs, files in os.walk(raiz):
        if time.time() - t_inicio > TEMPO_MAXIMO_BUSCA_SEGUNDOS:
            return {
                "sucesso": True,
                "total": len(resultados),
                "limite_atingido": True,
                "aviso": f"Busca interrompida após atingir tempo limite de {TEMPO_MAXIMO_BUSCA_SEGUNDOS}s.",
                "resultados": resultados
            }
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
            if time.time() - t_inicio > TEMPO_MAXIMO_BUSCA_SEGUNDOS:
                return {
                    "sucesso": True,
                    "total": len(resultados),
                    "limite_atingido": True,
                    "aviso": f"Busca interrompida após atingir tempo limite de {TEMPO_MAXIMO_BUSCA_SEGUNDOS}s.",
                    "resultados": resultados
                }
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
            "Permitidos: dir, type <arquivo>, python <arquivo>.py, git status|ls-files|log --oneline, "
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

