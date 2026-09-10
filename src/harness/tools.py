"""Módulo de ferramentas (tools) e registry canônico executáveis pelo Agent Harness."""

import os
from pathlib import Path
import re
import subprocess
from typing import Any, Callable, Dict, List, Optional
from harness.config import COMMAND_TIMEOUT_SECONDS, DIRS_IGNORADOS

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
]

LIMITE_LEITURA_ARQUIVO_BYTES = 200 * 1024  # 200 KB
LIMITE_ESCRITA_ARQUIVO_BYTES = 1024 * 1024  # 1 MB
LIMITE_BUSCA_ARQUIVO_BYTES = 1024 * 1024    # 1 MB
MAX_BUSCA_RESULTADOS = 50
LIMITE_TRUNCAMENTO_SAIDA = 4000

DIRS_IGNORADOS_BUSCA = DIRS_IGNORADOS


def _destino_redirecionamento(comando: str) -> Optional[str]:
    """
    Função pura que extrai o destino de um redirecionamento '>' ou '>>' no comando, se houver.
    Trata aspas simples e duplas no caminho e ignora redirecionamentos de descritor como '2>&1'.
    """
    match = re.search(r"(?:^|[^>])(?:>>|>)\s*(?:\"([^\"]+)\"|'([^']+)'|([^\s>&|]+))", comando)
    if match:
        destino = match.group(1) or match.group(2) or match.group(3)
        return destino.strip() if destino else None
    return None


def _destino_redirecionamento_e_protegido(destino: str) -> bool:
    """Verifica se o destino de redirecionamento atinge arquivo/pasta protegida (.env ou .git)."""
    dest_norm = destino.replace("\\", "/").lower().strip()
    partes = [p for p in dest_norm.split("/") if p and p != "."]
    for parte in partes:
        if parte == ".env" or parte == ".git" or parte.startswith(".env") or parte.startswith(".git"):
            return True
    return False


def comando_bloqueado(comando: str) -> Optional[str]:
    """
    Função pura que avalia se o comando contém padrões destrutivos de sistema no cmd.exe.
    Retorna a string do padrão que casou ou None caso seja permitido.
    """
    cmd_lower = comando.strip().lower()
    for padrao in PADROES_BLOQUEADOS:
        if re.search(padrao, cmd_lower):
            return padrao

    destino = _destino_redirecionamento(comando)
    if destino and _destino_redirecionamento_e_protegido(destino):
        return r"redirecionamento_destino_protegido"

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


def _resolver_caminho_seguro(caminho: str, base_dir: Optional[Path] = None) -> Path:
    """
    Valida e resolve o caminho relativo ao base_dir (default cwd).
    Levanta ValueError se tentar path traversal para fora do base_dir.
    """
    raiz = (base_dir or Path.cwd()).resolve()
    alvo = (raiz / caminho).resolve()
    try:
        alvo.relative_to(raiz)
    except ValueError:
        raise ValueError(f"Caminho fora do diretório do projeto: {caminho}")
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
    Retorna conteúdo ou erro.
    """
    try:
        alvo = _resolver_caminho_seguro(caminho, base_dir)
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
    """
    try:
        alvo = _resolver_caminho_seguro(caminho, base_dir)
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


def buscar_no_projeto(
    padrao: str,
    extensao: Optional[str] = None,
    base_dir: Optional[Path] = None
) -> Dict[str, Any]:
    """
    Varre arquivos texto do projeto buscando padrão regex (máx 50 resultados).
    Ignora diretórios especiais (.git, .venv, etc.) e arquivos > 1 MB ou binários.
    """
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
        # Ignora pastas proibidas in-place
        dirs[:] = [d for d in dirs if d not in DIRS_IGNORADOS_BUSCA]

        for file in files:
            p = Path(root) / file
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

                caminho_rel = p.relative_to(raiz)
                with open(p, "r", encoding="utf-8", errors="replace") as f_text:
                    for num_linha, linha in enumerate(f_text, start=1):
                        if regex.search(linha):
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
        "handler": executar_comando,
        "timeout": COMMAND_TIMEOUT_SECONDS
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
        "timeout": 10
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
        "timeout": 10
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
        "timeout": 15
    }
]

# Dicionário dinâmico derivado para compatibilidade com o loop e monkeypatches
TOOL_REGISTRY: Dict[str, Callable[..., Any]] = {
    tool["name"]: tool["handler"] for tool in TOOLS
}

