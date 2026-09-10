"""Testes unitários para o módulo env.py — isolamento estritamente local."""

import os
from pathlib import Path
from harness.env import carregar_env


def test_carregar_env_local_com_sucesso(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    chave = "TESTE_HARNESS_VAR_LOCAL_123"
    monkeypatch.delenv(chave, raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(f"# Comentário\n\n{chave}=valor_teste_local\n", encoding="utf-8")

    carregar_env(base_dir=tmp_path)
    assert os.environ.get(chave) == "valor_teste_local"


def test_carregar_env_nao_carrega_da_home(tmp_path, monkeypatch):
    home_falsa = tmp_path / "home_falsa"
    home_falsa.mkdir()
    (home_falsa / ".env").write_text("TESTE_HOME_VAR_999=nao_deve_carregar\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home_falsa)

    work_dir = tmp_path / "work_dir"
    work_dir.mkdir()
    monkeypatch.chdir(work_dir)

    chave = "TESTE_HOME_VAR_999"
    monkeypatch.delenv(chave, raising=False)

    carregar_env(base_dir=work_dir)
    assert chave not in os.environ


def test_carregar_env_preserva_variavel_existente(tmp_path, monkeypatch):
    chave = "TESTE_HARNESS_VAR_EXISTENTE"
    monkeypatch.setenv(chave, "valor_original")

    (tmp_path / ".env").write_text(f"{chave}=novo_valor\n", encoding="utf-8")
    carregar_env(base_dir=tmp_path)

    assert os.environ.get(chave) == "valor_original"
