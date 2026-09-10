"""Testes unitários para ferramentas e políticas de segurança de tools.py (sem rede)."""

from pathlib import Path
import pytest
from harness.tools import (
    comando_bloqueado,
    executar_comando,
    ler_arquivo,
    escrever_arquivo,
    buscar_no_projeto,
    truncar_saida,
    LIMITE_LEITURA_ARQUIVO_BYTES,
    LIMITE_ESCRITA_ARQUIVO_BYTES,
    TOOLS,
    TOOL_REGISTRY,
    _destino_redirecionamento,
)


def test_comando_bloqueado_padroes_destrutivos():
    # Comandos que DEVEM ser bloqueados (sem executar nenhum subprocess)
    comandos_proibidos = [
        "format C:",
        "FORMAT D: /FS:NTFS",
        "diskpart /s script.txt",
        "shutdown /s /t 0",
        "shutdown -r",
        "rd /s /q pasta",
        "rmdir /s /q pasta",
        "rm -rf /",
        "rm -r pasta",
        "reg delete HKLM\\Software",
        "del /s arquivo.txt",
        "erase /s /q *.*",
        "cipher /w:C:",
        "taskkill /f /im notepad.exe",
        # Wildcard destrutivo
        "del *.py",
        "erase *.*",
        "rd *",
        "rmdir *",
        # git clean -f
        "git clean -f",
        "git clean -fdx",
        "git clean -xdf",
        "git clean --force",
        # type nul > truncamento
        "type nul > arquivo.txt",
        "type nul > src/modulo.py",
        # Redirecionamento para arquivos protegidos
        "echo segredo > .env",
        "echo alteracao >> .git/config",
        "echo hack > subpasta/.env",
    ]
    for cmd in comandos_proibidos:
        assert comando_bloqueado(cmd) is not None, f"Deveria ter bloqueado: {cmd}"


def test_comando_bloqueado_permitidos():
    # Comandos seguros que DEVEM ser permitidos
    comandos_seguros = [
        "dir",
        "echo oi",
        "type arquivo.txt",
        "python w2_teste.py",
        "git status",
        "mkdir pasta_nova",
        "del arquivo.txt",  # del pontual sem wildcard nem /s é permitido
        "echo oi > novo.txt",  # redirecionamento sem caminho protegido
        "echo dados >> saida.log",
        "rd pasta",  # rd sem /s nem *
    ]
    for cmd in comandos_seguros:
        assert comando_bloqueado(cmd) is None, f"Deveria ter permitido: {cmd}"


def test_destino_redirecionamento():
    assert _destino_redirecionamento("echo teste > .env") == ".env"
    assert _destino_redirecionamento("echo chave >> .git/config") == ".git/config"
    assert _destino_redirecionamento("echo oi > \"caminho com espaco.txt\"") == "caminho com espaco.txt"
    assert _destino_redirecionamento("echo teste > 'arquivo.txt'") == "arquivo.txt"
    assert _destino_redirecionamento("dir 2>&1") is None
    assert _destino_redirecionamento("python script.py") is None


def test_executar_comando_bloqueio_seguranca():
    # Verifica o retorno imediato com código -1 sem executar
    resultado = executar_comando("shutdown /s /t 0")
    assert resultado["codigo_saida"] == -1
    assert "Comando bloqueado pela política de segurança" in resultado["stderr"]
    assert resultado["stdout"] == ""


def test_executar_comando_inofensivo():
    # Executa comando cross-platform inofensivo
    resultado = executar_comando("echo oi")
    assert resultado["codigo_saida"] == 0
    assert "oi" in resultado["stdout"]
    assert resultado["stderr"] == ""


def test_ler_arquivo_existente(tmp_path):
    arquivo = tmp_path / "teste.txt"
    arquivo.write_text("conteúdo de teste", encoding="utf-8")

    res = ler_arquivo("teste.txt", base_dir=tmp_path)
    assert res["sucesso"] is True
    assert res["conteudo"] == "conteúdo de teste"
    assert res["tamanho_bytes"] == len("conteúdo de teste".encode("utf-8"))


def test_ler_arquivo_nao_existente(tmp_path):
    res = ler_arquivo("inexistente.txt", base_dir=tmp_path)
    assert res["sucesso"] is False
    assert "não encontrado" in res["erro"].lower()


def test_ler_arquivo_path_traversal(tmp_path):
    # Tentativa de sair do base_dir com ..
    res = ler_arquivo("../../fora.txt", base_dir=tmp_path)
    assert res["sucesso"] is False
    assert "fora do diretório do projeto" in res["erro"]


def test_ler_arquivo_gigante_truncado(tmp_path):
    arquivo = tmp_path / "gigante.txt"
    conteudo_grande = "A" * (LIMITE_LEITURA_ARQUIVO_BYTES + 500)
    arquivo.write_text(conteudo_grande, encoding="utf-8")

    res = ler_arquivo("gigante.txt", base_dir=tmp_path)
    assert res["sucesso"] is True
    assert "[... truncado:" in res["conteudo"]
    assert res["conteudo"].startswith("A" * 100)


def test_escrever_arquivo_cria_e_rele(tmp_path):
    res_escrever = escrever_arquivo("subpasta/novo.txt", "texto salvo", base_dir=tmp_path)
    assert res_escrever["sucesso"] is True
    assert res_escrever["bytes_escritos"] == len("texto salvo".encode("utf-8"))

    res_ler = ler_arquivo("subpasta/novo.txt", base_dir=tmp_path)
    assert res_ler["sucesso"] is True
    assert res_ler["conteudo"] == "texto salvo"


def test_escrever_arquivo_path_traversal(tmp_path):
    res = escrever_arquivo("../fora.txt", "conteudo", base_dir=tmp_path)
    assert res["sucesso"] is False
    assert "fora do diretório do projeto" in res["erro"]


def test_escrever_arquivo_excede_limite(tmp_path):
    conteudo_muito_grande = "X" * (LIMITE_ESCRITA_ARQUIVO_BYTES + 10)
    res = escrever_arquivo("muito_grande.txt", conteudo_muito_grande, base_dir=tmp_path)
    assert res["sucesso"] is False
    assert "excede limite de escrita" in res["erro"]


def test_buscar_no_projeto_encontra_padrao(tmp_path):
    (tmp_path / "arquivo1.py").write_text("def funcao_harness():\n    pass\n", encoding="utf-8")
    (tmp_path / "arquivo2.txt").write_text("linha qualquer\noutra com harness aqui\n", encoding="utf-8")
    (tmp_path / "arquivo3.md").write_text("# Sem a palavra chave\n", encoding="utf-8")

    res = buscar_no_projeto("harness", base_dir=tmp_path)
    assert res["sucesso"] is True
    assert res["total"] == 2
    assert any("arquivo1.py:1:def funcao_harness():" in r for r in res["resultados"])
    assert any("arquivo2.txt:2:outra com harness aqui" in r for r in res["resultados"])


def test_buscar_no_projeto_com_filtro_extensao(tmp_path):
    (tmp_path / "arquivo1.py").write_text("chave secreta", encoding="utf-8")
    (tmp_path / "arquivo2.txt").write_text("chave secreta", encoding="utf-8")

    res = buscar_no_projeto("chave", extensao="py", base_dir=tmp_path)
    assert res["sucesso"] is True
    assert res["total"] == 1
    assert "arquivo1.py:1:chave secreta" in res["resultados"][0]


def test_buscar_no_projeto_ignora_diretorios_especiais(tmp_path):
    venv_dir = tmp_path / ".venv" / "lib"
    venv_dir.mkdir(parents=True)
    (venv_dir / "modulo.py").write_text("termo_busca", encoding="utf-8")

    (tmp_path / "codigo.py").write_text("termo_busca", encoding="utf-8")

    res = buscar_no_projeto("termo_busca", base_dir=tmp_path)
    assert res["sucesso"] is True
    assert res["total"] == 1
    assert "codigo.py:1:termo_busca" in res["resultados"][0]


def test_truncar_saida():
    curto = "texto curto"
    assert truncar_saida(curto, limite=50) == "texto curto"

    longo = "A" * 100
    truncado = truncar_saida(longo, limite=20)
    assert len(truncado) < 100
    assert truncado.startswith("A" * 20)
    assert "[... truncado: 100 caracteres]" in truncado


def test_tools_registry_consistencia():
    assert len(TOOLS) == 4
    nomes = [t["name"] for t in TOOLS]
    assert "executar_comando" in nomes
    assert "ler_arquivo" in nomes
    assert "escrever_arquivo" in nomes
    assert "buscar_no_projeto" in nomes

    for nome in nomes:
        assert nome in TOOL_REGISTRY
        assert callable(TOOL_REGISTRY[nome])

