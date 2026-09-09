"""Módulo de ferramentas (tools) executáveis pelo Agent Harness."""

import os
import subprocess
from typing import Any, Dict
from harness.config import COMMAND_TIMEOUT_SECONDS


def executar_comando(comando: str) -> Dict[str, Any]:
    """
    Executa o comando em subprocess no Windows com timeout de 30s.
    Captura stdout, stderr e código de saída.
    """
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
