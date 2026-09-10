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


def test_carregar_env_retorna_conjunto_chaves_e_aceita_vazias(tmp_path, monkeypatch):
    from harness.env import CHAVES_CARREGADAS_ENV

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VAR_COM_VALOR", raising=False)
    monkeypatch.delenv("VAR_VAZIA", raising=False)

    env_conteudo = (
        "# Linha de comentário\n"
        "VAR_COM_VALOR=conteudo_preenchido\n"
        "VAR_VAZIA=\n"
        "   \n"
    )
    (tmp_path / ".env").write_text(env_conteudo, encoding="utf-8")

    carregadas = carregar_env(base_dir=tmp_path)
    assert isinstance(carregadas, set)
    assert "VAR_COM_VALOR" in carregadas
    assert "VAR_VAZIA" in carregadas
    assert "VAR_COM_VALOR" in CHAVES_CARREGADAS_ENV
    assert "VAR_VAZIA" in CHAVES_CARREGADAS_ENV

    assert os.environ.get("VAR_COM_VALOR") == "conteudo_preenchido"
    assert os.environ.get("VAR_VAZIA") == ""


def test_cli_parse_args_com_env_defaults_e_obter_api_key(tmp_path, monkeypatch):
    from harness.__main__ import parse_args, obter_api_key

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HARNESS_PROVIDER", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    (tmp_path / ".env").write_text("HARNESS_PROVIDER=deepseek\n", encoding="utf-8")

    args = parse_args([])
    assert args.provider == "deepseek"

    # obter_api_key sem DEEPSEEK_API_KEY não deve fazer fallback para OPENAI_API_KEY
    monkeypatch.setenv("OPENAI_API_KEY", "chave-openai")
    assert obter_api_key("deepseek") == ""

    # obter_api_key com provider desconhecido não deve retornar fallback genérico
    assert obter_api_key("outro_provider") == ""

