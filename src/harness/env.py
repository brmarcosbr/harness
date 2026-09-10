"""Módulo para carregamento e resolução de variáveis de ambiente e arquivos .env."""

import os
from pathlib import Path


from typing import Optional, Union


def carregar_env(base_dir: Optional[Union[str, Path]] = None) -> None:
    """Carrega variáveis definidas no arquivo .env local para os.environ (se não definidas)."""
    env_path = (Path(base_dir) if base_dir else Path.cwd()) / ".env"
    if env_path.is_file():
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    chave, val = line.split("=", 1)
                    chave = chave.strip()
                    val = val.strip().strip('"').strip("'")
                    if chave and chave not in os.environ and val:
                        os.environ[chave] = val
        except Exception:
            pass

