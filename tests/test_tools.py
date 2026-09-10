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
    _destinos_redirecionamento,
    caminho_protegido,
    comando_toca_protegido,
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


def test_caminhos_protegidos_leitura_e_escrita(tmp_path):
    # .env bloqueado para leitura e escrita
    res_ler_env = ler_arquivo(".env", base_dir=tmp_path)
    assert res_ler_env["sucesso"] is False
    assert "caminho protegido: .env" in res_ler_env["erro"]

    res_esc_env = escrever_arquivo(".env", "SEGREDO=123", base_dir=tmp_path)
    assert res_esc_env["sucesso"] is False
    assert "caminho protegido: .env" in res_esc_env["erro"]

    # .git bloqueado para leitura e escrita
    res_ler_git = ler_arquivo(".git/config", base_dir=tmp_path)
    assert res_ler_git["sucesso"] is False
    assert "caminho protegido: .git/config" in res_ler_git["erro"]

    res_esc_git = escrever_arquivo(".git/config", "alteracao", base_dir=tmp_path)
    assert res_esc_git["sucesso"] is False
    assert "caminho protegido: .git/config" in res_esc_git["erro"]

    # .github protegido para escrita, leitura permitida
    res_esc_github = escrever_arquivo(".github/workflows/ci.yml", "run: rm -rf", base_dir=tmp_path)
    assert res_esc_github["sucesso"] is False
    assert "caminho protegido: .github/workflows/ci.yml" in res_esc_github["erro"]

    # escrita em src/x.py permitida
    res_esc_valida = escrever_arquivo("src/x.py", "print('hello')", base_dir=tmp_path)
    assert res_esc_valida["sucesso"] is True
    assert res_esc_valida["bytes_escritos"] > 0


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


def test_caminho_protegido_funcao_pura():
    # Bloqueio total (leitura e escrita)
    assert caminho_protegido(".env", modo="leitura") is True
    assert caminho_protegido(".env", modo="escrita") is True
    assert caminho_protegido(".env.local", modo="leitura") is True
    assert caminho_protegido("subpasta/.env", modo="leitura") is True
    assert caminho_protegido(".git", modo="leitura") is True
    assert caminho_protegido(".git/config", modo="leitura") is True
    assert caminho_protegido(".git/config", modo="escrita") is True
    assert caminho_protegido("HEAD:.env", modo="leitura") is True

    # .github protegido apenas para escrita
    assert caminho_protegido(".github/workflows/ci.yml", modo="leitura") is False
    assert caminho_protegido(".github/workflows/ci.yml", modo="escrita") is True

    # Arquivos normais permitidos
    assert caminho_protegido("README.md", modo="leitura") is False
    assert caminho_protegido("README.md", modo="escrita") is False
    assert caminho_protegido(".gitignore", modo="leitura") is False
    assert caminho_protegido(".gitignore", modo="escrita") is False
    assert caminho_protegido("src/harness/tools.py", modo="escrita") is False


def test_buscar_no_projeto_pula_caminhos_protegidos(tmp_path):
    # .env e .git/config contêm a chave, mas devem ser ignorados pela busca
    (tmp_path / ".env").write_text("SEGREDO_CRITICO=12345", encoding="utf-8")
    git_dir = tmp_path / ".git"
    git_dir.mkdir(parents=True)
    (git_dir / "config").write_text("SEGREDO_CRITICO=12345", encoding="utf-8")

    # Arquivo normal no projeto contém a chave
    (tmp_path / "modulo.py").write_text("chave = 'SEGREDO_CRITICO'\n", encoding="utf-8")

    res = buscar_no_projeto("SEGREDO_CRITICO", base_dir=tmp_path)
    assert res["sucesso"] is True
    assert res["total"] == 1
    assert "modulo.py:1:chave = 'SEGREDO_CRITICO'" in res["resultados"][0]
    # Garante que nenhum resultado veio de .env ou .git
    for r in res["resultados"]:
        assert not r.startswith(".env:")
        assert not r.startswith(".git:")


def test_comando_toca_protegido_leitura_e_escrita():
    comandos_leitura_proibidos = [
        "type .env",
        "cat .env",
        "more .env",
        "findstr SECRET .env",
        "head -n 10 .env",
        "tail -n 10 .env",
        "Get-Content .env",
        "gc .env",
        "git show HEAD:.env",
        "git diff .env",
        "sort < .env",
        "dir & type .env",
        "echo safe && cat sub/.env",
    ]
    for cmd in comandos_leitura_proibidos:
        assert comando_bloqueado(cmd) is not None, f"Deveria ter bloqueado leitura de protegido: {cmd}"

    comandos_escrita_proibidos = [
        "copy safe.txt .env",
        "move safe.txt .env",
        "xcopy dir .git",
        "robocopy dir .git",
        "mklink link .env",
        "attrib +h .env",
        "icacls .env /grant Everyone:F",
        "copy novidade.yml .github/workflows/ci.yml",
    ]
    for cmd in comandos_escrita_proibidos:
        assert comando_bloqueado(cmd) is not None, f"Deveria ter bloqueado escrita em protegido: {cmd}"

    comandos_permitidos = [
        "type README.md",
        "copy a.txt b.txt",
        "git diff",
        "git show HEAD:README.md",
        "findstr def src/harness/tools.py",
    ]
    for cmd in comandos_permitidos:
        assert comando_bloqueado(cmd) is None, f"Deveria ter permitido: {cmd}"


def test_redirecionamento_encadeado_e_multiplos_destinos():
    # Extração de múltiplos destinos
    cmd_multi = 'echo a > "caminho 1.txt" && echo b > \'caminho 2.txt\' ; type foo > c.txt'
    assert _destinos_redirecionamento(cmd_multi) == ["caminho 1.txt", "caminho 2.txt", "c.txt"]

    # Redirecionamento encadeado atingindo arquivo protegido deve ser bloqueado
    cmd_perigoso = "dir > safe.txt & echo x > .env"
    assert _destinos_redirecionamento(cmd_perigoso) == ["safe.txt", ".env"]
    assert comando_bloqueado(cmd_perigoso) is not None

    cmd_perigoso2 = "type foo.txt > out1.txt && echo bar >> .git/config"
    assert comando_bloqueado(cmd_perigoso2) is not None

    # Redirecionamento encadeado com destinos seguros deve ser permitido
    cmd_seguro = "echo a > safe1.txt && echo b > safe2.txt ; echo c > safe3.txt"
    assert comando_bloqueado(cmd_seguro) is None


def test_comando_bloqueado_powershell_destrutivo():
    comandos_ps_proibidos = [
        "Remove-Item -Recurse pasta",
        "Remove-Item pasta -Force",
        "Remove-Item -r -fo pasta",
        "Remove-Item -recurse -force C:\\dados",
        "ri -r -fo pasta",
        "ri -recurse C:\\temp",
        "del -Recurse pasta",
        "rd -Recurse pasta",
        "Format-Volume -DriveLetter D",
        "Stop-Computer",
        "Clear-Disk 1",
    ]
    for cmd in comandos_ps_proibidos:
        assert comando_bloqueado(cmd) is not None, f"Deveria ter bloqueado comando PowerShell destrutivo: {cmd}"

    comandos_ps_permitidos = [
        "Get-ChildItem",
        "Get-ChildItem -Path .",
        "Get-ChildItem -Filter *.py",
        "Remove-Item arquivo_unico.txt",
    ]
    for cmd in comandos_ps_permitidos:
        assert comando_bloqueado(cmd) is None, f"Deveria ter permitido comando seguro: {cmd}"


def test_buscar_no_projeto_protecao_redos(tmp_path):
    # 1. Padrões com quantificadores aninhados devem ser rejeitados imediatamente
    res_redos1 = buscar_no_projeto("(a+)+$", base_dir=tmp_path)
    assert res_redos1["sucesso"] is False
    assert "quantificador aninhado" in res_redos1["erro"]

    res_redos2 = buscar_no_projeto("([a-z]+)+", base_dir=tmp_path)
    assert res_redos2["sucesso"] is False
    assert "quantificador aninhado" in res_redos2["erro"]

    # 2. Padrões regex legítimos funcionam normalmente
    (tmp_path / "app.py").write_text("def calcular_valor(x):\n    return x * 2\n", encoding="utf-8")
    res_ok = buscar_no_projeto(r"def\s+\w+", base_dir=tmp_path)
    assert res_ok["sucesso"] is True
    assert res_ok["total"] == 1
    assert "def calcular_valor(x):" in res_ok["resultados"][0]

    # 3. Truncamento de linha para 500 chars antes do regex.search
    linha_com_alvo_depois_de_500 = ("a" * 520) + "TARGET_EXTREMO"
    linha_com_alvo_dentro_de_500 = ("b" * 100) + "TARGET_INICIAL"
    (tmp_path / "longo.txt").write_text(f"{linha_com_alvo_depois_de_500}\n{linha_com_alvo_dentro_de_500}\n", encoding="utf-8")

    res_pos = buscar_no_projeto("TARGET_EXTREMO", base_dir=tmp_path)
    assert res_pos["sucesso"] is True
    assert res_pos["total"] == 0  # não encontra além de 500 chars

    res_dentro = buscar_no_projeto("TARGET_INICIAL", base_dir=tmp_path)
    assert res_dentro["sucesso"] is True
    assert res_dentro["total"] == 1


def test_comando_bloqueado_wrappers_e_evasao():
    # Comandos com wrappers envolvendo caminhos protegidos devem ser bloqueados
    comandos_evasivos_proibidos = [
        'cmd /c "type .env"',
        'cmd /k "type .env"',
        'cmd.exe /c "type .env"',
        'powershell -c "Get-Content .env"',
        'powershell -Command "Get-Content .env"',
        'pwsh -c "Get-Content .env"',
        'cmd /c "copy a.txt .env"',
        'cmd /c "type sub/.env"',
        'powershell -c "gc .env"',
    ]
    for cmd in comandos_evasivos_proibidos:
        assert comando_bloqueado(cmd) is not None, f"Deveria ter bloqueado comando evasivo: {cmd}"

    # Comandos inofensivos em wrappers devem continuar permitidos
    comandos_inofensivos = [
        'cmd /c "dir"',
        'cmd /c "echo hello"',
        'powershell -c "Get-ChildItem"',
        'powershell -Command "Get-ChildItem -Path ."',
        'pwsh -c "Get-ChildItem"',
    ]
    for cmd in comandos_inofensivos:
        assert comando_bloqueado(cmd) is None, f"Deveria ter permitido comando seguro: {cmd}"





