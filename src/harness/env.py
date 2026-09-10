"""Módulo para carregamento e resolução de variáveis de ambiente e arquivos .env."""

import os
from pathlib import Path


from typing import Optional, Set, Union

CHAVES_CARREGADAS_ENV: Set[str] = set()


def carregar_env(base_dir: Optional[Union[str, Path]] = None) -> Set[str]:
    """
    Carrega variáveis definidas no arquivo .env local para os.environ (se não definidas).
    Retorna o conjunto de nomes de variáveis carregadas e aceita valores vazios (ex: CHAVE=).
    """
    carregadas: Set[str] = set()
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
                    if chave and chave not in os.environ:
                        os.environ[chave] = val
                        carregadas.add(chave)
                        CHAVES_CARREGADAS_ENV.add(chave)
                    elif chave:
                        CHAVES_CARREGADAS_ENV.add(chave)
        except Exception:
            pass
    return carregadas

