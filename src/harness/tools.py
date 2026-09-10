"""Módulo de ferramentas (tools) e registry canônico executáveis pelo Agent Harness."""

import os
from pathlib import Path
import re
import subprocess
from typing import Any, Callable, Dict, List, Optional, Union
from harness.config import COMMAND_TIMEOUT_SECONDS, DIRS_IGNORADOS, CAMINHOS_PROTEGIDOS

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

    bloqueio_total = [p.lower() for p in CAMINHOS_PROTEGIDOS.get("bloqueio_total", [])]
    somente_escrita = [p.lower() for p in CAMINHOS_PROTEGIDOS.get("somente_escrita", [])]

    # Checa bloqueio total (leitura e escrita)
    for bp in bloqueio_total:
        for p in partes:
            if bp == ".env":
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
    inclusive em comandos encadeados (&, &&, |, ;).
    Trata aspas simples e duplas no caminho e ignora redirecionamentos de descritor como '2>&1'.
    """
    destinos: List[str] = []
    padrao = r"(?:^|[^>0-9])(?:>>|>)\s*(?:\"([^\"]+)\"|'([^']+)'|([^\s>&|;]+))"
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


def comando_toca_protegido(comando: str) -> Optional[str]:
    """
    Função pura que analisa se o comando tenta ler ou escrever em caminhos protegidos.
    Cobre:
    - LEITURA: type, cat, more, findstr, head, tail, Get-Content, gc, git show HEAD:.env, git diff .env
    - ESCRITA: copy, move, xcopy, robocopy, mklink, attrib, icacls
    - Redirecionamento de entrada: < arquivo_protegido
    Retorna a identificação da infração ou None caso o comando seja seguro.
    """
    # Checagem de redirecionamento de entrada (< arquivo)
    for m in re.finditer(r"(?:^|[^<])<\s*(?:\"([^\"]+)\"|'([^']+)'|([^\s>&|;]+))", comando):
        src = m.group(1) or m.group(2) or m.group(3)
        if src and caminho_protegido(src.strip(), modo="leitura"):
            return f"redirecionamento_origem_protegida: {src.strip()}"

    subcomandos = re.split(r"&&|\|\||[&|;]", comando)
    for sub in subcomandos:
        sub = sub.strip()
        if not sub:
            continue
        raw_tokens = re.findall(r'"([^"]+)"|\'([^\']+)\'|(\S+)', sub)
        tokens = [t[0] or t[1] or t[2] for t in raw_tokens]
        if not tokens:
            continue

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

        # Git show / git diff
        if cmd_nome == "git" and args:
            sub_git = args[0].lower()
            if sub_git in ("show", "diff"):
                for a in args[1:]:
                    if a.startswith("-") and not (":" in a and not a.startswith("--")):
                        continue
                    if caminho_protegido(a, modo="leitura"):
                        return f"comando_toca_protegido: git {sub_git} {a}"

        # Comandos de leitura
        elif cmd_nome in LEITURA_COMANDOS:
            for a in args:
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
    ou tenta ler/escrever em arquivos ou destinos protegidos.
    Retorna a string do padrão que casou ou None caso seja permitido.
    """
    cmd_lower = comando.strip().lower()
    for padrao in PADROES_BLOQUEADOS:
        if re.search(padrao, cmd_lower):
            return padrao

    destinos = _destinos_redirecionamento(comando)
    for dest in destinos:
        if _destino_redirecionamento_e_protegido(dest):
            return r"redirecionamento_destino_protegido"

    toca_prot = comando_toca_protegido(comando)
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


def _resolver_caminho_seguro(
    caminho: str,
    base_dir: Optional[Path] = None,
    operacao: str = "leitura"
) -> Path:
    """
    Valida e resolve o caminho relativo ao base_dir (default cwd).
    Levanta ValueError se tentar path traversal para fora do base_dir
    ou se tentar acessar caminhos protegidos do projeto via caminho_protegido.
    """
    raiz = (base_dir or Path.cwd()).resolve()
    alvo = (raiz / caminho).resolve()
    try:
        relativo = alvo.relative_to(raiz)
    except ValueError:
        raise ValueError(f"Caminho fora do diretório do projeto: {caminho}")

    if caminho_protegido(relativo, modo=operacao) or caminho_protegido(caminho, modo=operacao):
        raise ValueError(f"caminho protegido: {caminho}")

    return alvo


def executar_comando(comando: str) -> Dict[str, Any]:
    """
    Executa o comando em subprocess no cmd.exe com timeout de 30s.
    Aplica política de segurança contra comandos destrutivos.
    """
    padrao = comando_bloqueado(comando)
    if padrao:
        return {
            "stdout": "",
            "stderr": f"Comando bloqueado pela política de segurança (padrão: {padrao})",
            "codigo_saida": -1
        }

    try:
        resultado = subprocess.run(
            comando,
            shell=True,
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            encoding="utf-8",
            errors="replace"
        )
        return {
            "stdout": resultado.stdout,
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


def _padrao_tem_quantificador_aninhado(padrao: str) -> bool:
    """Detecta padrões regex com quantificadores aninhados suscetíveis a ReDoS."""
    return bool(re.search(r"\([^)]*[\+\*\{][^)]*\)[\+\*\{]", padrao))


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
    if _padrao_tem_quantificador_aninhado(padrao):
        return {
            "sucesso": False,
            "erro": "padrão potencialmente catastrófico (quantificador aninhado)",
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
        # Ignora pastas proibidas e protegidas in-place
        dirs[:] = [
            d for d in dirs
            if d not in DIRS_IGNORADOS_BUSCA and not caminho_protegido(d, modo="leitura")
        ]

        for file in files:
            p = Path(root) / file
            caminho_rel = p.relative_to(raiz)
            if caminho_protegido(caminho_rel, modo="leitura"):
                continue

            if ext_filtro and p.suffix.lower() != ext_filtro:
                continue

            try:
                if p.stat().st_size > LIMITE_BUSCA_ARQUIVO_BYTES:
                    continue
                # Lê amostra para checar se é binário
                with open(p, "rb") as f_check:
                    chunk = f_check.read(1024)
                    if b"\x00" in chunk:
                        continue

                with open(p, "r", encoding="utf-8", errors="replace") as f_text:
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
            "Executa um comando no terminal Windows (shell cmd.exe) no diretório atual de trabalho. "
            "NÃO use comandos PowerShell (como Get-ChildItem). Comandos destrutivos são bloqueados por segurança. "
            "Retorna stdout, stderr e o código de saída."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "comando": {
                    "type": "string",
                    "description": "O comando cmd.exe a ser executado no Windows."
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

