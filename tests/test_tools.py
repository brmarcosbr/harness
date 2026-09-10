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
    obter_env_saneado,
    _tokenizar,
    validar_comando_whitelist,
    resolver_caminho_seguro,
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


def test_obter_env_saneado_e_vazamento_subprocess(monkeypatch, tmp_path):
    from harness.env import CHAVES_CARREGADAS_ENV

    monkeypatch.setenv("GEMINI_API_KEY", "chave-secreta-gemini")
    monkeypatch.setenv("OPENAI_API_KEY", "chave-secreta-openai")
    monkeypatch.setenv("MINHA_CUSTOM_SECRET", "segredo-customizado")
    monkeypatch.setenv("APP_AUTH_TOKEN", "token-autenticacao")
    monkeypatch.setenv("AWS_ACCESS_KEY", "chave-aws")
    monkeypatch.setenv("DB_PASSWORD", "super-senha-db")
    monkeypatch.setenv("CONNECTION_STRING", "postgres://user:pass@host/db")
    monkeypatch.setenv("SSH_PRIVATE_KEY", "chave-privada-ssh")
    monkeypatch.setenv("DEFAULT_CREDENTIALS", "credenciais-default")
    monkeypatch.setenv("NORMAL_VAR", "conteudo-normal")
    monkeypatch.setenv("MINHA_API_KEY_2", "chave-secreta-2")
    monkeypatch.setenv("DB_PASSWORD_V2", "senha-v2")
    monkeypatch.setenv("PATH", "caminho_padrao")
    monkeypatch.setenv("HOME", "/home/usuario")
    monkeypatch.setenv("LANG", "pt_BR.UTF-8")

    CHAVES_CARREGADAS_ENV.add("VAR_DO_ENV_PROJETO")
    monkeypatch.setenv("VAR_DO_ENV_PROJETO", "segredo-do-arquivo-env")

    saneado = obter_env_saneado()
    assert "GEMINI_API_KEY" not in saneado
    assert "OPENAI_API_KEY" not in saneado
    assert "MINHA_CUSTOM_SECRET" not in saneado
    assert "APP_AUTH_TOKEN" not in saneado
    assert "AWS_ACCESS_KEY" not in saneado
    assert "DB_PASSWORD" not in saneado
    assert "CONNECTION_STRING" not in saneado
    assert "SSH_PRIVATE_KEY" not in saneado
    assert "DEFAULT_CREDENTIALS" not in saneado
    assert "MINHA_API_KEY_2" not in saneado
    assert "DB_PASSWORD_V2" not in saneado
    assert "VAR_DO_ENV_PROJETO" not in saneado
    assert saneado.get("NORMAL_VAR") == "conteudo-normal"
    assert saneado.get("PATH") == "caminho_padrao"
    assert saneado.get("HOME") == "/home/usuario"
    assert saneado.get("LANG") == "pt_BR.UTF-8"

    # Executa comando do sistema e verifica que não vaza segredos
    script_env = tmp_path / "print_env.py"
    script_env.write_text("import os\nfor k, v in os.environ.items():\n    print(f'{k}={v}')\n", encoding="utf-8")
    res = executar_comando(f"python {script_env.name}", base_dir=tmp_path)
    assert res["codigo_saida"] == 0
    assert "chave-secreta-gemini" not in res["stdout"]
    assert "chave-secreta-2" not in res["stdout"]
    assert "senha-v2" not in res["stdout"]
    assert "chave-secreta-openai" not in res["stdout"]
    assert "segredo-customizado" not in res["stdout"]
    assert "token-autenticacao" not in res["stdout"]
    assert "chave-aws" not in res["stdout"]
    assert "super-senha-db" not in res["stdout"]
    assert "postgres://user:pass@host/db" not in res["stdout"]
    assert "chave-privada-ssh" not in res["stdout"]
    assert "credenciais-default" not in res["stdout"]
    assert "segredo-do-arquivo-env" not in res["stdout"]
    assert "conteudo-normal" in res["stdout"]


def test_inversao_camadas_echo_inofensivo_vs_comandos_destrutivos(tmp_path):
    # 'echo format' e 'echo shutdown' não executam shell nem comandos destrutivos -> rc=0
    res_echo_format = executar_comando("echo format", base_dir=tmp_path)
    assert res_echo_format["codigo_saida"] == 0
    assert "format" in res_echo_format["stdout"]

    res_echo_shutdown = executar_comando("echo shutdown", base_dir=tmp_path)
    assert res_echo_shutdown["codigo_saida"] == 0
    assert "shutdown" in res_echo_shutdown["stdout"]

    # Comandos destrutivos reais continuam bloqueados pela whitelist/blocklist
    res_format = executar_comando("format C:", base_dir=tmp_path)
    assert res_format["codigo_saida"] == -1
    assert res_format["stderr"] != ""

    res_shutdown = executar_comando("shutdown /s", base_dir=tmp_path)
    assert res_shutdown["codigo_saida"] == -1
    assert res_shutdown["stderr"] != ""

    # Tentativa de redirecionamento para arquivo protegido via echo continua bloqueada
    res_echo_env = executar_comando("echo x > .env", base_dir=tmp_path)
    assert res_echo_env["codigo_saida"] == -1
    assert "bloqueado" in res_echo_env["stderr"].lower()


def test_comando_bloqueado_descritores_numericos_redirecionamento():
    # Descritores numéricos 1>, 2>, 1>>, 2>> apontando para .env devem ser bloqueados
    comandos_bloqueados = [
        "echo x 1> .env",
        "echo x 2> .env",
        "echo x 1>> .env",
        "echo x 2>> .env",
        'cmd /c "echo x 1> .env"',
        'powershell -c "echo x 1> .env"',
    ]
    for cmd in comandos_bloqueados:
        assert comando_bloqueado(cmd) is not None, f"Deveria ter bloqueado redirecionamento com descritor: {cmd}"

    # Redirecionamento de descritores padrão como 2>&1 deve ser permitido
    comandos_permitidos = [
        "python script.py 2>&1",
        "dir 2>&1 > saida.txt",
    ]
    for cmd in comandos_permitidos:
        assert comando_bloqueado(cmd) is None, f"Deveria ter permitido redirecionamento de descritor: {cmd}"


def test_buscar_no_projeto_protecao_redos_alternancia(tmp_path):
    # Padrões com alternância repetida devem ser rejeitados antes de compilar
    res_alt1 = buscar_no_projeto("(a|aa)+$", base_dir=tmp_path)
    assert res_alt1["sucesso"] is False
    assert res_alt1["erro"] == "padrão potencialmente catastrófico (quantificador aninhado ou alternância repetida)"

    res_alt2 = buscar_no_projeto("(foo|foobar)+", base_dir=tmp_path)
    assert res_alt2["sucesso"] is False
    assert res_alt2["erro"] == "padrão potencialmente catastrófico (quantificador aninhado ou alternância repetida)"


def test_tokenizar_comando():
    assert _tokenizar("dir /b") == ["dir", "/b"]
    assert _tokenizar('type "meu arquivo.txt"') == ["type", "meu arquivo.txt"]
    assert _tokenizar("findstr 'palavra com espaco' doc.txt") == ["findstr", "palavra com espaco", "doc.txt"]
    assert _tokenizar("echo x > .env") == ["echo", "x", ">", ".env"]
    assert _tokenizar("dir & type safe.txt") == ["dir", "&", "type", "safe.txt"]
    assert _tokenizar("echo $VAR %VAR%") == ["echo", "$VAR", "%VAR%"]
    assert _tokenizar(r'echo "hello \"world\""') == ["echo", 'hello "world"']
    assert _tokenizar(r"findstr 'palavra \'com\' aspas' doc.txt") == ["findstr", "palavra 'com' aspas", "doc.txt"]

    with pytest.raises(ValueError, match="desbalanceadas"):
        _tokenizar('type "arquivo.txt')

    with pytest.raises(ValueError, match="desbalanceadas"):
        _tokenizar("findstr 'padrao")


def test_executar_comando_whitelist_tabela_permitidos(tmp_path):
    import subprocess
    # Isola o teste do ambiente criando repositório temporário
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True)

    arquivo_teste = tmp_path / "README.md"
    arquivo_teste.write_text("Linha 1: Harness Agent\nLinha 2: Outra coisa\n", encoding="utf-8")

    script_py = tmp_path / "bench_test_math.py"
    script_py.write_text("print('resultado: 42')", encoding="utf-8")

    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "commit inicial de teste"], cwd=str(tmp_path), capture_output=True)

    # dir
    res_dir = executar_comando("dir", base_dir=tmp_path)
    assert res_dir["codigo_saida"] == 0
    assert "README.md" in res_dir["stdout"]

    # dir /b
    res_dir_b = executar_comando("dir /b", base_dir=tmp_path)
    assert res_dir_b["codigo_saida"] == 0
    assert "README.md" in res_dir_b["stdout"]

    # type README.md
    res_type = executar_comando("type README.md", base_dir=tmp_path)
    assert res_type["codigo_saida"] == 0
    assert "Harness Agent" in res_type["stdout"]

    # python bench_test_math.py
    res_py = executar_comando("python bench_test_math.py", base_dir=tmp_path)
    assert res_py["codigo_saida"] == 0
    assert "resultado: 42" in res_py["stdout"]

    # git status (no repo temporário isolado)
    res_git_status = executar_comando("git status", base_dir=tmp_path)
    assert res_git_status["codigo_saida"] == 0
    assert "branch" in res_git_status["stdout"].lower() or "working tree clean" in res_git_status["stdout"].lower()

    # git log (no repo temporário isolado)
    res_git_log = executar_comando("git log -n 1", base_dir=tmp_path)
    assert res_git_log["codigo_saida"] == 0
    assert "commit inicial de teste" in res_git_log["stdout"]

    # findstr "Harness" README.md
    res_findstr = executar_comando('findstr "Harness" README.md', base_dir=tmp_path)
    assert res_findstr["codigo_saida"] == 0
    assert "Harness Agent" in res_findstr["stdout"]

    # where python (suporta alias python/python3)
    res_where = executar_comando("where python", base_dir=tmp_path)
    assert res_where["codigo_saida"] == 0
    assert "python" in res_where["stdout"].lower()


def test_executar_comando_whitelist_tabela_proibidos(tmp_path):
    comandos_proibidos = [
        "rm -rf .",
        "del /s *.py",
        'cmd /c "type .env"',
        'powershell -c "Get-Content .env"',
        "type .env",
        'python -c "import os;os.system(\'calc\')"',
        r"python C:\Windows\System32\qualquer.py",
        "git clean -fdx",
        "git reset --hard",
        "git push",
        "echo x > .env",
        "comando_inexistente",
    ]
    for cmd in comandos_proibidos:
        res = executar_comando(cmd, base_dir=tmp_path)
        assert res["codigo_saida"] == -1, f"Deveria ter retornado codigo -1 para: {cmd}"
        assert res["stderr"] != "", f"Deveria ter stderr explicativo para: {cmd}"


def test_invariante_sem_shell_true_em_tools():
    arquivo_tools = Path(__file__).parent.parent / "src" / "harness" / "tools.py"
    conteudo = arquivo_tools.read_text(encoding="utf-8")
    assert "shell=True" not in conteudo


def test_git_validacao_argumentos_e_caminhos(tmp_path):
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True)
    (tmp_path / "README.md").write_text("teste de git", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True)

    # Argumentos proibidos
    res1 = executar_comando("git diff --no-index /etc/passwd x", base_dir=tmp_path)
    assert res1["codigo_saida"] == -1
    assert "Flag perigosa" in res1["stderr"] or "não permitid" in res1["stderr"]

    res2 = executar_comando("git log --output=/tmp/x -1", base_dir=tmp_path)
    assert res2["codigo_saida"] == -1
    assert "Redirecionamento" in res2["stderr"] or "bloqueado" in res2["stderr"]

    res3 = executar_comando("git show --output=/tmp/x HEAD", base_dir=tmp_path)
    assert res3["codigo_saida"] == -1
    assert "Redirecionamento" in res3["stderr"] or "bloqueado" in res3["stderr"]

    # Argumentos permitidos
    res4 = executar_comando("git log --oneline -3", base_dir=tmp_path)
    assert res4["codigo_saida"] == 0
    assert "init" in res4["stdout"]

    res5 = executar_comando("git diff --stat", base_dir=tmp_path)
    assert res5["codigo_saida"] == 0


def test_symlink_e_junction_traversal_bloqueado(tmp_path):
    import os
    from harness.contexto import gerar_contexto_repo
    raiz = tmp_path / "projeto"
    raiz.mkdir()
    fora = tmp_path / "externo"
    fora.mkdir()
    arquivo_secreto = fora / "secreto.txt"
    arquivo_secreto.write_text("conteudo secreto ultra confidencial", encoding="utf-8")

    link_criado = False
    # Tenta criar junction no Windows
    try:
        import _winapi
        _winapi.CreateJunction(str(fora), str(raiz / "link_dir"))
        link_criado = True
    except Exception:
        pass

    # Tenta criar symlink no Linux/POSIX
    if not link_criado:
        try:
            os.symlink(str(fora), str(raiz / "link_dir"))
            link_criado = True
        except OSError:
            pass

    if link_criado:
        # 1. dir sobre symlink/junction
        res_dir = executar_comando("dir link_dir", base_dir=raiz)
        assert res_dir["codigo_saida"] != 0
        assert "secreto.txt" not in res_dir["stdout"]

        # 2. type sobre arquivo em symlink/junction
        res_type = executar_comando("type link_dir/secreto.txt", base_dir=raiz)
        assert res_type["codigo_saida"] != 0
        assert "conteudo secreto" not in res_type["stdout"]

        # 3. ler_arquivo sobre arquivo em symlink/junction
        res_ler = ler_arquivo("link_dir/secreto.txt", base_dir=raiz)
        assert res_ler["sucesso"] is False
        assert res_ler["conteudo"] == ""

        # 4. escrever_arquivo sobre symlink/junction
        res_escrever = escrever_arquivo("link_dir/novo.txt", "ataque", base_dir=raiz)
        assert res_escrever["sucesso"] is False

        # 5. buscar_no_projeto sobre projeto com symlink/junction externa
        res_busca = buscar_no_projeto("conteudo", base_dir=raiz)
        assert res_busca["sucesso"] is True
        assert len(res_busca["resultados"]) == 0

        # 6. gerar_contexto_repo ignorando symlink/junction externa
        contexto = gerar_contexto_repo(raiz)
        assert "conteudo secreto" not in contexto
        assert "secreto.txt" not in contexto

    # Validação direta da função resolver_caminho_seguro
    with pytest.raises(ValueError, match="fora do diretório do projeto"):
        resolver_caminho_seguro("../externo/secreto.txt", base_dir=raiz)

    with pytest.raises(ValueError, match="caminho protegido"):
        resolver_caminho_seguro(".env", base_dir=raiz)


def test_python_validacao_argumentos_e_caminhos(tmp_path):
    script_py = tmp_path / "script.py"
    script_py.write_text("import sys\nprint('args:', sys.argv[1:])\n", encoding="utf-8")

    # Argumento com caminho fora da raiz (..) deve ser bloqueado
    res1 = executar_comando(r"python script.py ..\secret", base_dir=tmp_path)
    assert res1["codigo_saida"] == -1
    assert "fora do projeto" in res1["stderr"] or "não pode conter" in res1["stderr"]

    # Argumento com caminho absoluto deve ser bloqueado
    res2 = executar_comando("python script.py /etc/passwd", base_dir=tmp_path)
    assert res2["codigo_saida"] == -1
    assert "Caminho absoluto não permitido" in res2["stderr"]

    # Argumento com arquivo protegido deve ser bloqueado
    res3 = executar_comando("python script.py .env", base_dir=tmp_path)
    assert res3["codigo_saida"] == -1
    assert "protegido" in res3["stderr"]

    # Argumento via flag --config=.env deve ser bloqueado
    res4 = executar_comando("python script.py --config=.env", base_dir=tmp_path)
    assert res4["codigo_saida"] == -1
    assert "protegido" in res4["stderr"]

    # Argumento via flag com caminho absoluto deve ser bloqueado
    res5 = executar_comando("python script.py --input=/etc/passwd", base_dir=tmp_path)
    assert res5["codigo_saida"] == -1
    assert "Caminho absoluto não permitido" in res5["stderr"]

    # Argumentos inofensivos e flags simples devem ser permitidos
    res_ok = executar_comando("python script.py arg1 123 --verbose", base_dir=tmp_path)
    assert res_ok["codigo_saida"] == 0
    assert "arg1" in res_ok["stdout"]


def test_findstr_e_where_validacao_flags_perigosas(tmp_path):
    # findstr: flags perigosas /g:, -g:, /d:, -d:
    res_fg = executar_comando("findstr /g:arquivo.txt padrao", base_dir=tmp_path)
    assert res_fg["codigo_saida"] == -1
    assert "Flag perigosa não permitida no findstr" in res_fg["stderr"]

    res_fd = executar_comando("findstr /d:dir padrao", base_dir=tmp_path)
    assert res_fd["codigo_saida"] == -1
    assert "Flag perigosa não permitida no findstr" in res_fd["stderr"]

    # where: flag recursiva arbitrária /r, -r
    res_wr = executar_comando(r"where /r C:\Windows calc", base_dir=tmp_path)
    assert res_wr["codigo_saida"] == -1
    assert "Flag perigosa não permitida no where" in res_wr["stderr"]

    res_wr_dash = executar_comando("where -r /usr/bin python", base_dir=tmp_path)
    assert res_wr_dash["codigo_saida"] == -1
    assert "Flag perigosa não permitida no where" in res_wr_dash["stderr"]


def test_git_orderfile_bloqueado(tmp_path):
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True)
    (tmp_path / "a.txt").write_text("conteudo a", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "commit 1"], cwd=str(tmp_path), capture_output=True)

    # git diff -O / --orderfile
    res_diff_o = executar_comando("git diff -O order.txt", base_dir=tmp_path)
    assert res_diff_o["codigo_saida"] == -1
    assert "Flag perigosa não permitida no git" in res_diff_o["stderr"]

    res_diff_orderfile = executar_comando("git diff --orderfile=order.txt", base_dir=tmp_path)
    assert res_diff_orderfile["codigo_saida"] == -1
    assert "Flag perigosa não permitida no git" in res_diff_orderfile["stderr"]

    # git log -O / --orderfile
    res_log_o = executar_comando("git log -O order.txt", base_dir=tmp_path)
    assert res_log_o["codigo_saida"] == -1
    assert "Flag perigosa não permitida no git" in res_log_o["stderr"]

    res_log_orderfile = executar_comando("git log --orderfile=order.txt", base_dir=tmp_path)
    assert res_log_orderfile["codigo_saida"] == -1
    assert "Flag perigosa não permitida no git" in res_log_orderfile["stderr"]


def test_comando_bloqueado_sem_falsos_positivos():
    assert comando_bloqueado("echo .env") is None
    assert comando_bloqueado("type foo.git") is None
    assert comando_bloqueado("echo projeto.git") is None


def test_type_nativo_limite_tamanho_200kb(tmp_path):
    grande = tmp_path / "grande.txt"
    grande.write_bytes(b"A" * (205 * 1024))
    res = executar_comando("type grande.txt", base_dir=tmp_path)
    assert res["codigo_saida"] == 1
    assert "excede limite de leitura de 200 KB" in res["stderr"]


def test_dir_nativo_rejeita_flags_nao_suportadas(tmp_path):
    res_s = executar_comando("dir /s", base_dir=tmp_path)
    assert res_s["codigo_saida"] in (-1, 1)
    assert "não suportada" in res_s["stderr"]

    res_r = executar_comando("dir /r", base_dir=tmp_path)
    assert res_r["codigo_saida"] in (-1, 1)
    assert "não suportada" in res_r["stderr"]


def test_dir_nativo_sobre_arquivo_individual(tmp_path):
    arquivo = tmp_path / "exemplo.txt"
    arquivo.write_text("conteudo teste", encoding="utf-8")

    # dir sobre arquivo existente sem flag
    res = executar_comando("dir exemplo.txt", base_dir=tmp_path)
    assert res["codigo_saida"] == 0
    assert "exemplo.txt" in res["stdout"]

    # dir /b sobre arquivo existente
    res_bare = executar_comando("dir /b exemplo.txt", base_dir=tmp_path)
    assert res_bare["codigo_saida"] == 0
    assert res_bare["stdout"].strip() == "exemplo.txt"


def test_git_exfiltracao_conteudo_bloqueado(tmp_path):
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True)

    (tmp_path / "README.md").write_text("documentacao inicial", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True)

    # Commita um arquivo protegido (.env) com chave secreta
    chave_secreta = "SEGREDO_SUPER_CONFIDENCIAL_12345"
    (tmp_path / ".env").write_text(f"API_KEY={chave_secreta}\n", encoding="utf-8")
    subprocess.run(["git", "add", ".env"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "add secret"], cwd=str(tmp_path), capture_output=True)

    # 1. git log -p -1 deve ser bloqueado por flag de patch e nao vazar segredo
    res_log_p = executar_comando("git log -p -1", base_dir=tmp_path)
    assert res_log_p["codigo_saida"] == -1
    assert chave_secreta not in res_log_p["stdout"]

    # 2. git show HEAD sem resumo ou arquivo especifico deve ser bloqueado e nao vazar segredo
    res_show_head = executar_comando("git show HEAD", base_dir=tmp_path)
    assert res_show_head["codigo_saida"] == -1
    assert chave_secreta not in res_show_head["stdout"]

    # 3. git show --stat HEAD deve ser permitido e nao exibir conteudo do segredo
    res_show_stat = executar_comando("git show --stat HEAD", base_dir=tmp_path)
    assert res_show_stat["codigo_saida"] == 0
    assert chave_secreta not in res_show_stat["stdout"]

    # 4. git log --oneline -3 deve ser permitido e nao vazar segredo
    res_log_oneline = executar_comando("git log --oneline -3", base_dir=tmp_path)
    assert res_log_oneline["codigo_saida"] == 0
    assert chave_secreta not in res_log_oneline["stdout"]

    # 5. git status deve ser permitido
    res_status = executar_comando("git status", base_dir=tmp_path)
    assert res_status["codigo_saida"] == 0


def test_filtrar_saida_git_redige_diff_protegido():
    from harness.tools import _filtrar_saida_git

    diff_vazamento = (
        "diff --git a/README.md b/README.md\n"
        "index 111..222 100644\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1 +1 @@\n"
        "+novo readme\n"
        "diff --git a/.env b/.env\n"
        "new file mode 100644\n"
        "index 000..333\n"
        "--- /dev/null\n"
        "+++ b/.env\n"
        "@@ -0,0 +1 @@\n"
        "+SECRET_KEY=super_secreta_999\n"
    )

    saida_filtrada = _filtrar_saida_git(diff_vazamento)
    assert "+novo readme" in saida_filtrada
    assert "SECRET_KEY=super_secreta_999" not in saida_filtrada
    assert "[conteúdo de arquivo protegido omitido pela política de segurança]" in saida_filtrada


def test_anti_duplicacao_relative_to():
    """Garante que relative_to aparece exatamente uma vez em tools.py e zero em contexto.py."""
    src_dir = Path(__file__).parent.parent / "src" / "harness"
    tools_code = (src_dir / "tools.py").read_text(encoding="utf-8")
    contexto_code = (src_dir / "contexto.py").read_text(encoding="utf-8")

    matches_tools = [linha for linha in tools_code.splitlines() if "relative_to(" in linha]
    matches_contexto = [linha for linha in contexto_code.splitlines() if "relative_to(" in linha]

    assert len(matches_tools) == 1, f"tools.py deve ter exatamente 1 chamada a relative_to, encontrado: {matches_tools}"
    assert "relativo = alvo.relative_to(raiz)" in matches_tools[0]
    assert len(matches_contexto) == 0, f"contexto.py não deve conter relative_to, encontrado: {matches_contexto}"


def test_obter_env_saneado_termos_ampliados(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@localhost/db")
    monkeypatch.setenv("REDIS_URI", "redis://localhost:6379")
    monkeypatch.setenv("DB_CONN", "Server=localhost;Database=test;")
    monkeypatch.setenv("BASIC_AUTH", "Basic dXNlcjpwYXNz")
    monkeypatch.setenv("MYSQL_PWD", "mysqlsecret")
    monkeypatch.setenv("PASSPHRASE_CLIENT", "my-passphrase")
    monkeypatch.setenv("PASSKEY_SECRET", "my-passkey")
    monkeypatch.setenv("USER", "testuser")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOME", "/home/testuser")
    monkeypatch.setenv("LANG", "en_US.UTF-8")

    env_limpo = obter_env_saneado()

    assert "DATABASE_URL" not in env_limpo
    assert "REDIS_URI" not in env_limpo
    assert "DB_CONN" not in env_limpo
    assert "BASIC_AUTH" not in env_limpo
    assert "MYSQL_PWD" not in env_limpo
    assert "PASSPHRASE_CLIENT" not in env_limpo
    assert "PASSKEY_SECRET" not in env_limpo

    assert env_limpo.get("USER") == "testuser"
    assert env_limpo.get("PATH") == "/usr/bin:/bin"
    assert env_limpo.get("HOME") == "/home/testuser"
    assert env_limpo.get("LANG") == "en_US.UTF-8"


def test_falsos_positivos_env_templates_e_findstr(tmp_path):
    (tmp_path / ".env.example").write_text("FOO=BAR_EXAMPLE\n", encoding="utf-8")
    (tmp_path / ".env.sample").write_text("FOO=BAR_SAMPLE\n", encoding="utf-8")
    (tmp_path / ".env.template").write_text("FOO=BAR_TEMPLATE\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=REAL_VALUE\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("Arquivo .env de configuracao\n", encoding="utf-8")

    # .env.example, .env.sample e .env.template permitidos
    assert caminho_protegido(".env.example") is False
    assert caminho_protegido(".env.sample") is False
    assert caminho_protegido(".env.template") is False
    assert caminho_protegido(".env") is True
    assert caminho_protegido(".env.local") is True

    res_ex = executar_comando("type .env.example", base_dir=tmp_path)
    assert res_ex["codigo_saida"] == 0
    assert "FOO=BAR_EXAMPLE" in res_ex["stdout"]

    res_real = executar_comando("type .env", base_dir=tmp_path)
    assert res_real["codigo_saida"] in (-1, 1)

    # findstr com '.env' como padrão de busca no README.md deve ser PERMITIDO
    res_findstr = executar_comando("findstr .env README.md", base_dir=tmp_path)
    assert res_findstr["codigo_saida"] == 0
    assert ".env" in res_findstr["stdout"]

    # findstr com /g:.env ou visando .env como arquivo deve ser BLOQUEADO
    res_findstr_g = executar_comando("findstr /g:.env README.md", base_dir=tmp_path)
    assert res_findstr_g["codigo_saida"] == -1
    assert "Flag perigosa" in res_findstr_g["stderr"] or "bloqueado" in res_findstr_g["stderr"]

    res_findstr_alvo = executar_comando("findstr padrao .env", base_dir=tmp_path)
    assert res_findstr_alvo["codigo_saida"] == -1
    assert "protegido" in res_findstr_alvo["stderr"]


def test_findstr_posix_fallback_limites(tmp_path, monkeypatch):
    import shutil
    # Força uso do fallback do findstr simulando ausência do binário nativo
    monkeypatch.setattr(shutil, "which", lambda cmd: None if cmd == "findstr" else "/bin/" + cmd)

    # Arquivo gigante (> 1 MB) deve ser ignorado
    gigante = tmp_path / "gigante.txt"
    gigante.write_bytes(b"alvo\n" * (300 * 1024))  # ~1.5 MB
    res_gigante = executar_comando("findstr alvo gigante.txt", base_dir=tmp_path)
    assert res_gigante["codigo_saida"] == 1
    assert res_gigante["stdout"] == ""

    # Arquivo binário com null byte deve ser ignorado
    binario = tmp_path / "binario.dat"
    binario.write_bytes(b"cabecalho\x00alvo em binario")
    res_bin = executar_comando("findstr alvo binario.dat", base_dir=tmp_path)
    assert res_bin["codigo_saida"] == 1
    assert res_bin["stdout"] == ""

    # Arquivo com muitas ocorrências deve limitar a 50 resultados
    muitas = tmp_path / "muitas.txt"
    muitas.write_text("linha alvo\n" * 100, encoding="utf-8")
    res_muitas = executar_comando("findstr alvo muitas.txt", base_dir=tmp_path)
    assert res_muitas["codigo_saida"] == 0
    linhas = [l for l in res_muitas["stdout"].splitlines() if l.strip()]
    assert len(linhas) == 50


def test_filtro_fail_safe_git_diff_variacoes():
    from harness.tools import _filtrar_saida_git

    # 1. Diff com pasta com espaço e arquivo protegido entre aspas
    diff_espaco = (
        'diff --git "a/pasta dir/.env" "b/pasta dir/.env"\n'
        'index 111..222 100644\n'
        '--- "a/pasta dir/.env"\n'
        '+++ "b/pasta dir/.env"\n'
        '@@ -1 +1 @@\n'
        '+SEGREDO_ESPACO=123\n'
    )
    saida_espaco = _filtrar_saida_git(diff_espaco)
    assert "SEGREDO_ESPACO=123" not in saida_espaco
    assert "[conteúdo de arquivo protegido omitido pela política de segurança]" in saida_espaco

    # 2. Diff sem prefixo (--no-prefix) com arquivo protegido
    diff_no_prefix = (
        'diff --git .env .env\n'
        'index 111..222 100644\n'
        '--- .env\n'
        '+++ .env\n'
        '@@ -1 +1 @@\n'
        '+SEGREDO_NO_PREFIX=456\n'
    )
    saida_no_prefix = _filtrar_saida_git(diff_no_prefix)
    assert "SEGREDO_NO_PREFIX=456" not in saida_no_prefix
    assert "[conteúdo de arquivo protegido omitido pela política de segurança]" in saida_no_prefix

    # 3. Cabeçalho de diff malformado ou desconhecido -> fail-safe redige
    diff_malformado = (
        'diff --git estranho_sem_segundo_argumento\n'
        '@@ -1 +1 @@\n'
        '+DADOS_QUE_PODEM_SER_SENSIVEIS\n'
    )
    saida_malformado = _filtrar_saida_git(diff_malformado)
    assert "DADOS_QUE_PODEM_SER_SENSIVEIS" not in saida_malformado
    assert "fail-safe" in saida_malformado

    # 4. Arquivo seguro com espaço passa intacto
    diff_seguro = (
        'diff --git "a/minha pasta/app.py" "b/minha pasta/app.py"\n'
        'index 111..222 100644\n'
        '--- "a/minha pasta/app.py"\n'
        '+++ "b/minha pasta/app.py"\n'
        '@@ -1 +1 @@\n'
        '+print("ola mundo")\n'
    )
    saida_seguro = _filtrar_saida_git(diff_seguro)
    assert '+print("ola mundo")' in saida_seguro


def test_git_diff_sem_resumo_e_flags_prefixo_bloqueadas(tmp_path):
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True)

    (tmp_path / "app.py").write_text("print('v1')", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "v1"], cwd=str(tmp_path), capture_output=True)

    (tmp_path / "app.py").write_text("print('v2')", encoding="utf-8")

    # git diff sem flag de resumo deve ser bloqueado
    res_diff_puro = executar_comando("git diff", base_dir=tmp_path)
    assert res_diff_puro["codigo_saida"] == -1
    assert "sem flag de resumo bloqueado" in res_diff_puro["stderr"]

    # git diff com resumo permitido
    res_diff_stat = executar_comando("git diff --stat", base_dir=tmp_path)
    assert res_diff_stat["codigo_saida"] == 0

    # Flags de prefixo bloqueadas
    res_no_prefix = executar_comando("git diff --stat --no-prefix", base_dir=tmp_path)
    assert res_no_prefix["codigo_saida"] == -1
    assert "Flag perigosa não permitida no git: '--no-prefix'" in res_no_prefix["stderr"]

    res_src_prefix = executar_comando("git diff --stat --src-prefix=x/", base_dir=tmp_path)
    assert res_src_prefix["codigo_saida"] == -1
    assert "Flag perigosa não permitida no git: '--src-prefix=x/'" in res_src_prefix["stderr"]

    res_dst_prefix = executar_comando("git diff --stat --dst-prefix=y/", base_dir=tmp_path)
    assert res_dst_prefix["codigo_saida"] == -1
    assert "Flag perigosa não permitida no git: '--dst-prefix=y/'" in res_dst_prefix["stderr"]

    # Flags -c e --cc bloqueadas
    res_cc = executar_comando("git diff --stat --cc", base_dir=tmp_path)
    assert res_cc["codigo_saida"] == -1
    assert "Flag de exibição de conteúdo/patch não permitida no git" in res_cc["stderr"]


def test_findstr_validacao_estrita_argumentos_e_curingas(tmp_path):
    (tmp_path / "teste.txt").write_text("linha de teste\n", encoding="utf-8")

    # findstr requer ao menos 2 argumentos nao-flags
    res_falta_alvo = executar_comando("findstr padrao", base_dir=tmp_path)
    assert res_falta_alvo["codigo_saida"] == -1
    assert "requer padrão de busca e ao menos um arquivo alvo" in res_falta_alvo["stderr"]

    # findstr com curinga * bloqueado
    res_wildcard_star = executar_comando("findstr padrao *", base_dir=tmp_path)
    assert res_wildcard_star["codigo_saida"] == -1
    assert "Curingas (* e ?) não são permitidos" in res_wildcard_star["stderr"]

    # findstr com curinga ? bloqueado
    res_wildcard_quest = executar_comando("findstr padrao teste?.txt", base_dir=tmp_path)
    assert res_wildcard_quest["codigo_saida"] == -1
    assert "Curingas (* e ?) não são permitidos" in res_wildcard_quest["stderr"]

    # findstr com flags /s ou -s bloqueado
    res_flag_s = executar_comando("findstr /s padrao teste.txt", base_dir=tmp_path)
    assert res_flag_s["codigo_saida"] == -1
    assert "Flag perigosa não permitida no findstr: '/s'" in res_flag_s["stderr"]

    res_flag_s_dash = executar_comando("findstr -s padrao teste.txt", base_dir=tmp_path)
    assert res_flag_s_dash["codigo_saida"] == -1
    assert "Flag perigosa não permitida no findstr: '-s'" in res_flag_s_dash["stderr"]

    # findstr com flags /f ou -f bloqueado
    res_flag_f = executar_comando("findstr /f:lista.txt padrao teste.txt", base_dir=tmp_path)
    assert res_flag_f["codigo_saida"] == -1
    assert "Flag perigosa não permitida no findstr: '/f:lista.txt'" in res_flag_f["stderr"]

    # findstr correto permitido
    res_ok = executar_comando("findstr teste teste.txt", base_dir=tmp_path)
    assert res_ok["codigo_saida"] == 0
    assert "linha de teste" in res_ok["stdout"]


def test_comando_bloqueado_format_verb_position(tmp_path):
    # format como verbo de comando destrutivo deve ser bloqueado
    assert comando_bloqueado("format C:") is not None
    assert comando_bloqueado("format C: /FS:NTFS") is not None
    assert comando_bloqueado("format.exe D:") is not None
    assert comando_bloqueado("echo oi & format C:") is not None

    # format como argumento/flag inofensiva NÃO deve ser bloqueado na blocklist
    assert comando_bloqueado("git log --format=oneline") is None
    assert comando_bloqueado("dir format") is None
    assert comando_bloqueado("findstr format n.py") is None

    # Execução via executar_comando com dir format e findstr format
    (tmp_path / "format").mkdir()
    (tmp_path / "format" / "arq.txt").write_text("conteudo", encoding="utf-8")
    res_dir = executar_comando("dir format", base_dir=tmp_path)
    assert res_dir["codigo_saida"] == 0

    (tmp_path / "n.py").write_text("def format_string(): pass\n", encoding="utf-8")
    res_findstr = executar_comando("findstr format n.py", base_dir=tmp_path)
    assert res_findstr["codigo_saida"] == 0
    assert "format_string" in res_findstr["stdout"]


def test_readme_contagem_testes_sincronizada():
    import ast
    import re
    from pathlib import Path
    raiz = Path(__file__).parent.parent
    readme_path = raiz / "README.md"
    if not readme_path.exists():
        import pytest
        pytest.skip("README.md não encontrado no ambiente/pacote de execução")

    tests_dir = raiz / "tests"

    def _contar_testes_arquivo(f: Path) -> int:
        count = 0
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                multiplicador = 1
                for dec in getattr(node, "decorator_list", []):
                    if (
                        isinstance(dec, ast.Call)
                        and isinstance(dec.func, ast.Attribute)
                        and dec.func.attr == "parametrize"
                    ):
                        if len(dec.args) >= 2 and isinstance(dec.args[1], (ast.List, ast.Tuple)):
                            multiplicador *= len(dec.args[1].elts)
                count += multiplicador
        return count

    total_testes = sum(_contar_testes_arquivo(f) for f in tests_dir.glob("test_*.py"))

    readme_texto = readme_path.read_text(encoding="utf-8")

    # 1. Checa contagem no badge
    m_badge = re.search(r"img\.shields\.io/badge/tests-(\d+)%2F\1%20passing", readme_texto)
    assert m_badge is not None, "Badge de testes com padrão 'tests-N%2FN%20passing' não encontrado no README.md"
    badge_count = int(m_badge.group(1))
    assert badge_count == total_testes, f"Badge no README cita {badge_count}, mas suíte tem {total_testes} testes"

    # 2. Checa contagem no texto da seção de testes
    m_texto = re.search(r"Os (\d+) testes unitários são executados 100% offline", readme_texto)
    assert m_texto is not None, "Frase 'Os N testes unitários...' não encontrada no README.md"
    texto_count = int(m_texto.group(1))
    assert texto_count == total_testes, f"Texto no README cita {texto_count}, mas suíte tem {total_testes} testes"

    # 3. Checa contagem no bloco de saída esperada do pytest
    m_saida = re.search(r"(\d+) passed in", readme_texto)
    assert m_saida is not None, "Saída 'N passed in' não encontrada no README.md"
    saida_count = int(m_saida.group(1))
    assert saida_count == total_testes, f"Saída no README cita {saida_count}, mas suíte tem {total_testes} testes"


def test_envrc_protegido_e_templates(tmp_path):
    (tmp_path / ".envrc").write_text("export SECRET=123\n", encoding="utf-8")
    (tmp_path / ".envrc.example").write_text("export SECRET=example\n", encoding="utf-8")

    # .envrc protegido em caminho_protegido
    assert caminho_protegido(".envrc") is True
    assert caminho_protegido(".envrc.local") is True
    assert caminho_protegido(".envrc.example") is False
    assert caminho_protegido(".envrc.sample") is False
    assert caminho_protegido(".envrc.template") is False

    # type .envrc bloqueado
    res_envrc = executar_comando("type .envrc", base_dir=tmp_path)
    assert res_envrc["codigo_saida"] in (-1, 1)

    # type .envrc.example permitido
    res_example = executar_comando("type .envrc.example", base_dir=tmp_path)
    assert res_example["codigo_saida"] == 0
    assert "SECRET=example" in res_example["stdout"]


def test_git_show_hifen_isolado_bloqueado(tmp_path):
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True)

    (tmp_path / "app.py").write_text("print('hello')", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True)

    # git show HEAD -- isolado NÃO deve ser tratado como arquivo específico
    res_show_dash = executar_comando("git show HEAD --", base_dir=tmp_path)
    assert res_show_dash["codigo_saida"] == -1
    assert "requer flags de resumo" in res_show_dash["stderr"]

    # git show HEAD --stat -- permitido
    res_show_dash_stat = executar_comando("git show HEAD --stat --", base_dir=tmp_path)
    assert res_show_dash_stat["codigo_saida"] == 0


def test_type_multi_arquivo_formato_cmd(tmp_path):
    (tmp_path / "f1.txt").write_text("conteudo 1", encoding="utf-8")
    (tmp_path / "f2.txt").write_text("conteudo 2", encoding="utf-8")

    # Arquivo único: sem cabeçalho extra
    res_single = executar_comando("type f1.txt", base_dir=tmp_path)
    assert res_single["codigo_saida"] == 0
    assert res_single["stdout"] == "conteudo 1"

    # Múltiplos arquivos: formato cmd.exe (\n<nome>\n\n<conteudo>)
    res_multi = executar_comando("type f1.txt f2.txt", base_dir=tmp_path)
    assert res_multi["codigo_saida"] == 0
    esperado = "\nf1.txt\n\nconteudo 1\nf2.txt\n\nconteudo 2"
    assert res_multi["stdout"] == esperado


def test_git_bloqueio_objetos_nus_blob_e_tree(tmp_path):
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True)

    (tmp_path / "app.py").write_text("print('safe')", encoding="utf-8")
    (tmp_path / ".env").write_text("CHAVE=SUPERSEGREDO_CRITICO\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py", ".env"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "commit inicial"], cwd=str(tmp_path), capture_output=True)

    # Obtém SHA do blob do .env (completo e abreviado)
    res_blob = subprocess.run(["git", "rev-parse", "HEAD:.env"], cwd=str(tmp_path), capture_output=True, text=True)
    blob_sha = res_blob.stdout.strip()
    assert len(blob_sha) == 40
    blob_abbrev = blob_sha[:7]

    # Obtém SHA da tree
    res_tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=str(tmp_path), capture_output=True, text=True)
    tree_sha = res_tree.stdout.strip()

    # Obtém SHA do commit
    res_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(tmp_path), capture_output=True, text=True)
    commit_sha = res_commit.stdout.strip()

    # Bloqueio de objeto blob nu com e sem flags de resumo, completo e abreviado
    res1 = executar_comando(f"git show {blob_sha}", base_dir=tmp_path)
    assert res1["codigo_saida"] == -1
    assert "Objeto git nu (blob/tree) não permitido" in res1["stderr"]
    assert "SUPERSEGREDO_CRITICO" not in res1["stdout"]

    res2 = executar_comando(f"git show --stat {blob_sha}", base_dir=tmp_path)
    assert res2["codigo_saida"] == -1
    assert "Objeto git nu (blob/tree) não permitido" in res2["stderr"]
    assert "SUPERSEGREDO_CRITICO" not in res2["stdout"]

    res3 = executar_comando(f"git show --stat {blob_abbrev}", base_dir=tmp_path)
    assert res3["codigo_saida"] == -1
    assert "Objeto git nu (blob/tree) não permitido" in res3["stderr"]
    assert "SUPERSEGREDO_CRITICO" not in res3["stdout"]

    res4 = executar_comando(f"git show --name-only {blob_abbrev}", base_dir=tmp_path)
    assert res4["codigo_saida"] == -1
    assert "Objeto git nu (blob/tree) não permitido" in res4["stderr"]

    res5 = executar_comando(f"git show --name-status {blob_abbrev}", base_dir=tmp_path)
    assert res5["codigo_saida"] == -1
    assert "Objeto git nu (blob/tree) não permitido" in res5["stderr"]

    res6 = executar_comando(f"git show -s {blob_abbrev}", base_dir=tmp_path)
    assert res6["codigo_saida"] == -1
    assert "Objeto git nu (blob/tree) não permitido" in res6["stderr"]

    res7 = executar_comando(f"git show --no-patch {blob_abbrev}", base_dir=tmp_path)
    assert res7["codigo_saida"] == -1
    assert "Objeto git nu (blob/tree) não permitido" in res7["stderr"]

    # Bloqueio de objeto tree
    res_t1 = executar_comando(f"git show {tree_sha}", base_dir=tmp_path)
    assert res_t1["codigo_saida"] == -1
    assert "Objeto git nu (blob/tree) não permitido" in res_t1["stderr"]

    res_t2 = executar_comando(f"git show --stat {tree_sha}", base_dir=tmp_path)
    assert res_t2["codigo_saida"] == -1
    assert "Objeto git nu (blob/tree) não permitido" in res_t2["stderr"]

    # Bloqueio em git log
    res_l1 = executar_comando(f"git log {blob_sha}", base_dir=tmp_path)
    assert res_l1["codigo_saida"] == -1
    assert "Objeto git nu (blob/tree) não permitido" in res_l1["stderr"]

    # Objeto commit com resumo é permitido
    res_c1 = executar_comando(f"git show --stat {commit_sha}", base_dir=tmp_path)
    assert res_c1["codigo_saida"] == 0
    res_c2 = executar_comando("git show --stat HEAD", base_dir=tmp_path)
    assert res_c2["codigo_saida"] == 0


def test_git_flags_patch_compostas_e_raw_bloqueadas(tmp_path):
    # Flags compostas e --raw devem ser bloqueadas no git diff e git log
    res1 = executar_comando("git log --patch-with-raw -1", base_dir=tmp_path)
    assert res1["codigo_saida"] == -1
    assert "Flag de exibição de conteúdo/patch não permitida" in res1["stderr"]

    res2 = executar_comando("git log --stat --patch-with-raw -1", base_dir=tmp_path)
    assert res2["codigo_saida"] == -1
    assert "Flag de exibição de conteúdo/patch não permitida" in res2["stderr"]

    res3 = executar_comando("git diff --stat --patch-with-stat", base_dir=tmp_path)
    assert res3["codigo_saida"] == -1
    assert "Flag de exibição de conteúdo/patch não permitida" in res3["stderr"]

    res4 = executar_comando("git diff --stat --raw", base_dir=tmp_path)
    assert res4["codigo_saida"] == -1
    assert "Flag de exibição de conteúdo/patch não permitida" in res4["stderr"]

    res5 = executar_comando("git log -n 1 --raw", base_dir=tmp_path)
    assert res5["codigo_saida"] == -1
    assert "Flag de exibição de conteúdo/patch não permitida" in res5["stderr"]


def test_git_redacao_metadados_resumo_e_diff_cc(tmp_path):
    import subprocess
    from harness.tools import _filtrar_saida_git

    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True)

    (tmp_path / "app.py").write_text("print('safe')", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=XYZ\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py", ".env"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True)

    # 1. git show --stat HEAD redige .env mas preserva app.py
    res_stat = executar_comando("git show --stat HEAD", base_dir=tmp_path)
    assert res_stat["codigo_saida"] == 0
    assert "SECRET" not in res_stat["stdout"]
    assert "app.py" in res_stat["stdout"]
    assert ".env" not in res_stat["stdout"]
    assert "[arquivo protegido omitido pela política de segurança]" in res_stat["stdout"]

    # 2. git show --name-only HEAD redige .env
    res_name = executar_comando("git show --name-only HEAD", base_dir=tmp_path)
    assert res_name["codigo_saida"] == 0
    assert "app.py" in res_name["stdout"]
    assert ".env" not in res_name["stdout"]
    assert "[arquivo protegido omitido pela política de segurança]" in res_name["stdout"]

    # 3. git show --name-status HEAD redige .env
    res_status = executar_comando("git show --name-status HEAD", base_dir=tmp_path)
    assert res_status["codigo_saida"] == 0
    assert "app.py" in res_status["stdout"]
    assert ".env" not in res_status["stdout"]
    assert "[arquivo protegido omitido pela política de segurança]" in res_status["stdout"]

    # 4. diff --cc protegido e seguro no _filtrar_saida_git
    diff_cc_protegido = (
        "diff --cc .env\n"
        "index 111,222..333\n"
        "--- a/.env\n"
        "+++ b/.env\n"
        "@@@ -1,1 -1,1 +1,1 @@@\n"
        "+SECRET_CONFLITO\n"
    )
    saida_cc_p = _filtrar_saida_git(diff_cc_protegido)
    assert "SECRET_CONFLITO" not in saida_cc_p
    assert "[conteúdo de arquivo protegido omitido pela política de segurança]" in saida_cc_p

    diff_cc_seguro = (
        "diff --cc app.py\n"
        "index 111,222..333\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@@ -1,1 -1,1 +1,1 @@@\n"
        "+print('resolvido')\n"
    )
    saida_cc_s = _filtrar_saida_git(diff_cc_seguro)
    assert "+print('resolvido')" in saida_cc_s


def test_findstr_flags_compostas_e_flag_c(tmp_path):
    (tmp_path / "texto.txt").write_text("linha teste de busca\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=123\n", encoding="utf-8")

    # Flags compostas bloqueadas: /si, /is, /fs:, /d:
    res1 = executar_comando("findstr /si busca texto.txt", base_dir=tmp_path)
    assert res1["codigo_saida"] == -1
    assert "Flag perigosa não permitida" in res1["stderr"]

    res2 = executar_comando("findstr /is busca texto.txt", base_dir=tmp_path)
    assert res2["codigo_saida"] == -1
    assert "Flag perigosa não permitida" in res2["stderr"]

    res3 = executar_comando("findstr /fs:lista.txt busca texto.txt", base_dir=tmp_path)
    assert res3["codigo_saida"] == -1
    assert "Flag perigosa não permitida" in res3["stderr"]

    res4 = executar_comando("findstr /d:pasta busca texto.txt", base_dir=tmp_path)
    assert res4["codigo_saida"] == -1
    assert "Flag perigosa não permitida" in res4["stderr"]

    # Flag /c: com texto permitido
    res5 = executar_comando("findstr /c:teste texto.txt", base_dir=tmp_path)
    assert res5["codigo_saida"] == 0
    assert "linha teste de busca" in res5["stdout"]

    # Flag /c: sem arquivo alvo -> erro de argumento
    res6 = executar_comando("findstr /c:teste", base_dir=tmp_path)
    assert res6["codigo_saida"] == -1
    assert "requer ao menos um arquivo alvo" in res6["stderr"]

    # Flag /c: visando arquivo protegido -> bloqueado
    res7 = executar_comando("findstr /c:teste .env", base_dir=tmp_path)
    assert res7["codigo_saida"] == -1
    assert "protegido" in res7["stderr"]


def test_desescapar_caminho_git_octal_e_aspas():
    from harness.tools import _desescapar_caminho_git, caminho_protegido

    # 1. Octal UTF-8 com aspas em pasta contendo .env
    c1 = _desescapar_caminho_git(r'"a/pasta_\303\241/.env"')
    assert c1 == "pasta_á/.env"
    assert caminho_protegido(c1) is True

    # 2. Octal UTF-8 sem aspas (já limpo por regex)
    c2 = _desescapar_caminho_git(r'a/pasta_\303\241/.env')
    assert c2 == "pasta_á/.env"
    assert caminho_protegido(c2) is True

    # 3. Aspas internas escapadas
    c3 = _desescapar_caminho_git(r'"a/sub/\"minha_pasta\"/.env"')
    assert c3 == 'sub/"minha_pasta"/.env'
    assert caminho_protegido(c3) is True


def test_format_com_bloqueado():
    # format.com deve ser bloqueado na blocklist
    assert comando_bloqueado("format.com C:") is not None
    assert comando_bloqueado("format.com D: /FS:NTFS") is not None
    assert comando_bloqueado("format.com") is not None
    assert comando_bloqueado("echo oi & format.com C:") is not None


def test_obter_env_saneado_com_ponto_e_diff_janela_chunk(monkeypatch):
    from harness.tools import _filtrar_saida_git

    # 1. Variáveis com '.' delimitador
    monkeypatch.setenv("MY.PASSWORD", "segredo_com_ponto")
    monkeypatch.setenv("APP.SECRET.KEY", "chave_com_ponto")
    monkeypatch.setenv("DB.AUTH", "auth_com_ponto")
    monkeypatch.setenv("APP.SAFE.CONFIG", "valor_seguro")

    env_saneado = obter_env_saneado()
    assert "MY.PASSWORD" not in env_saneado
    assert "APP.SECRET.KEY" not in env_saneado
    assert "DB.AUTH" not in env_saneado
    assert env_saneado.get("APP.SAFE.CONFIG") == "valor_seguro"

    # 2. Janela de cabeçalho do diff com muitas linhas antes do chunk @@
    diff_longo_cabecalho = (
        "diff --git a/pasta/antiga/app.py b/pasta/nova/app.py\n"
        "similarity index 98%\n"
        "rename from pasta/antiga/app.py\n"
        "rename to pasta/nova/app.py\n"
        "dissimilarity index 2%\n"
        "index abcdef1..1234567 100644\n"
        "--- a/pasta/antiga/.env\n"
        "+++ b/pasta/nova/.env\n"
        "@@ -1,3 +1,3 @@\n"
        "+CONTEUDO_VAZADO=TRUE\n"
    )
    saida_filtrada = _filtrar_saida_git(diff_longo_cabecalho)
    assert "CONTEUDO_VAZADO=TRUE" not in saida_filtrada
    assert "[conteúdo de arquivo protegido omitido pela política de segurança]" in saida_filtrada






