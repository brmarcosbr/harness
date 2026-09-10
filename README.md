# Agent Harness — Agente de Código com Context Caching Medido

> Loop multi-turno agnóstico de provider, tool use segura e **74,6% a 76,2% de economia de custo via context caching** (benchmark real com Gemini).

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![Tests](https://img.shields.io/badge/tests-140%2F140%20passing-brightgreen)
![License MIT](https://img.shields.io/badge/license-MIT-green)
![Zero Libs](https://img.shields.io/badge/external--deps-zero-informational)
![CI](https://github.com/brmarcosbr/harness/actions/workflows/ci.yml/badge.svg)


O **Agent Harness** é uma implementação em Python puro (sem frameworks pesados ou dependências externas em runtime) de um harness para agentes de engenharia de software autônomos. Ele executa loops multi-turno com resolução de ferramentas (*tool calling*), suporta múltiplos provedores (Google Gemini e OpenAI-compatible / DeepSeek) e gerencia o contexto da sessão para viabilizar e mensurar a eficiência de **Context Caching**.

---

## Sumário

- [Destaques](#destaques)
- [Arquitetura](#arquitetura)
  - [O Loop de Turnos](#o-loop-de-turnos)
  - [Gestão de Contexto e Prefix Invariance](#gestão-de-contexto-e-prefix-invariance)
- [Segurança](#segurança)
- [Política de Execução de Comandos](#política-de-execução-de-comandos)
- [Resultados do Benchmark](#resultados-do-benchmark)
- [Instalação e Configuração](#instalação-e-configuração)
- [Exemplos de Uso](#exemplos-de-uso)
  - [Exemplo de Execução Real](#exemplo-de-execução-real)
- [Executando a Suíte de Testes](#executando-a-suíte-de-testes)
- [Roadmap](#roadmap)
- [Licença](#licença)

---

## Destaques

- **Zero Dependências Externas em Produção:** Usa estritamente a biblioteca padrão do Python (`urllib`, `json`, `dataclasses`, `subprocess`, `argparse`). O `pytest` é a única dependência de desenvolvimento.
- **Multi-Provider Neutro:** Protocolo unificado de mensagens e ferramentas, adaptado dinamicamente para o schema nativo do Gemini (incluindo Gemini 3+) ou para o padrão OpenAI / DeepSeek.
- **Context Caching Mensurado:** Prefixos determinísticos estáveis (*prefix invariance*) permitem que a API Gemini reutilize tokens cacheados a partir do 2º turno, reduzindo custos em mais de 74% (até 76,2% contrafactual).
- **Poda Ativa de Contexto:** Algoritmo *head-body-tail* que mantém o prefixo cacheável intacto no topo (*head*), descarta turnos intermediários quando o orçamento de tokens estoura (*body*) e preserva os turnos mais recentes (*tail*).
- **Tools Seguras com Blocklist e Proteção de Caminho:** Comandos de terminal, leitura, escrita e busca de arquivos contam com restrições rígidas contra comandos destrutivos e *path traversal*.

---

## Arquitetura

### O Loop de Turnos

A cada turno, o harness envia o histórico estruturado ao modelo. Se o modelo responder requisitando a chamada de ferramentas (*tool calls*), o harness despacha as execuções para o registry de tools, anexa as saídas como mensagens de resposta de ferramenta e aciona o próximo turno. Quando o modelo devolve texto puro, o ciclo é encerrado.

```
                      +-----------------------------+
                      |   Usuário (Instrução/CLI)   |
                      +--------------+--------------+
                                     |
                                     v
                       +---------------------------+
                       | Montagem do Contexto      |
                       | [Head + Body + Tail]      |
                       +-------------+-------------+
                                     |
                                     v
                       +---------------------------+
                       | Adaptador de Provedor     |
                       | (Gemini / DeepSeek/OpenAI)|
                       +-------------+-------------+
                                     |
                                     v
                             +---------------+
                             |  Chamada LLM  |
                             +-------+-------+
                                     |
                   +-----------------+-----------------+
                   |                                   |
             [Tool Calls]                         [Texto Final]
                   |                                   |
                   v                                   v
        +----------------------+             +-------------------+
        | Registry de Tools    |             | Fim da Execução   |
        | - executar_comando   |             | Resumo de Custos  |
        | - ler_arquivo        |             +-------------------+
        | - escrever_arquivo   |
        | - buscar_no_projeto  |
        +----------+-----------+
                   |
                   v
        +----------------------+
        | Anexar Resultados    |
        | Poda se Estourar Lim.|
        +----------+-----------+
                   |
                   +----> Próximo Turno (Loop)
```

### Gestão de Contexto e Prefix Invariance

Provedores modernos de LLM como a Google Gemini suportam *implicit context caching* automático para prefixos estáticos que superem 4.096 tokens. Para que o cache seja aproveitado, o prefixo inicial da conversa deve ser idêntico em cada turno (*Prefix Invariance*).

O módulo de contexto divide a janela em 3 regiões:

```
+-------------------------------------------------------------------+
| 1. HEAD (Determinístico & Estável)                                |
|    - System Prompt                                                |
|    - Contexto do Repositório (quando fornecido)                   |
|    * NUNCA contém timestamps, hashes voláteis ou ordem aleatória  |
|    * Alvo primário de cache hit entre os turnos 2..N              |
+-------------------------------------------------------------------+
| 2. BODY (Poda de Histórico)                                       |
|    - Turnos intermediários de ferramentas e raciocínio            |
|    - Descartados aos pares (call + resposta) se estourar tokens   |
+-------------------------------------------------------------------+
| 3. TAIL (Preservação Local)                                       |
|    - Últimos turnos mais recentes (garante coerência imediata)    |
|    - Resposta final do agente                                     |
+-------------------------------------------------------------------+
```

Quando a flag `--no-cache` é fornecida, um cabeçalho dinâmico contendo timestamp (`time.time()`, segundos float) é propositadamente injetado no início da mensagem do usuário, quebrando a invariância do prefixo e forçando *cache miss* a cada turno para fins de comparação.

---

## Segurança

O harness disponibiliza 4 ferramentas nativas para o modelo:

1. `executar_comando`: Execução de comandos controlados por whitelist sem shell (`shell=False`) no diretório do projeto, com teto de saída de subprocessos em 1 MB.
2. `ler_arquivo`: Leitura de arquivos de texto com limite máximo de 200 KB por arquivo.
3. `escrever_arquivo`: Criação e sobrescrita de arquivos com limite de 1 MB.
4. `buscar_no_projeto`: Busca por padrão regex no conteúdo dos arquivos de código/texto do projeto (ignora pastas `.venv`, `__pycache__`, `.git`, `.pytest_cache`, `build` e `dist`; permite filtro por extensão opcional; ignora binários e arquivos maiores que 1 MB; limite máximo de 50 resultados; timeout de 10 segundos e proteção ReDoS estendida). **Não** busca por nome de arquivo.

- **Blocklist de Comandos Críticos:** Bloqueio via regex dos padrões perigosos mapeados em `PADROES_BLOQUEADOS`: `format`, `diskpart`, `shutdown`, `rd /s` (ou `/q`), `rmdir /s` (ou `/q`), `rm -rf`, `reg delete`, `del /s` (ou `/f` ou `/q`), `erase /s` (ou `/f` ou `/q`), `cipher /w` e `taskkill /f /im`.
- **Caminhos Protegidos Configuráveis (`CAMINHOS_PROTEGIDOS`):** Centralizados em `config.py` e verificados pela função autoritativa `resolver_caminho_seguro`:
  - `bloqueio_total` (leitura e escrita bloqueadas): `.env` (bloqueando `.env`, `.env.*` e `.env_*`, com exceção segura para templates como `.env.example`, `.env.sample` e `.env.template`) e `.git/` (todos os objetos, configs e referências do repositório);
  - `somente_escrita` (leitura permitida se necessário, escrita categoricamente bloqueada): `.github/` (protege workflows do GitHub Actions e arquivos de automação contra sobrescrita ou corrupção pelo modelo).
- **Proteção contra Path Traversal e Symlink Escape:** Validação estrita via `resolver_caminho_seguro` garantindo que nenhum caminho acesse pastas superiores à raiz do projeto (`..` proibido) ou escape da árvore do projeto através de symlinks ou NTFS junctions.
- **Timeouts e Tetos de Memória Rígidos:** Cada execução de comando possui limite padrão de 30 segundos e leitura em chunks limitada a no máximo 1 MB de saída em subprocessos, prevenindo bloqueios ou estouro de memória por scripts ruidosos. A busca em arquivos é limitada a 10 segundos.
- **Validação de Kwargs no Loop contra o Schema:** No despacho das ferramentas pelo loop, qualquer argumento não declarado expressamente no schema canônico (`TOOLS`) é sumariamente rejeitado antes da execução, impedindo injeção de parâmetros espúrios (`base_dir`, etc.).
- **Proteção Multiplataforma via Whitelist:** A execução de processos sem shell (`shell=False`) e restrita à whitelist de binários permitidos impede a execução de comandos destrutivos tanto no Windows (`cmd.exe`) quanto em ambientes POSIX/Linux (`rm -rf`, `mkfs`, etc.). Handlers nativos como `type`, `dir`, `where` e `findstr` contam com emulação transparente em Python para portabilidade integral.

### Limites Conhecidos da Blocklist (Mitigação vs. Sandboxing)

A combinação de blocklist de comandos, inspeção de redirecionamentos, proteção ReDoS e validação de caminhos protegidos é uma camada de mitigação pragmática (*defense-in-depth*) desenhada para desenvolvimento e testes locais assistidos, **NÃO** um sandbox formal de segurança:

1. **O que a blocklist NÃO protege:**
   - **Binários arbitrários invocados pelo modelo:** Ferramentas como `curl`, `wget` ou `bitsadmin` baixando scripts ou executáveis externos.
   - **Execução de código arbitrário em interpretadores:** Comandos como `python -c "..."` ou `powershell` executando payloads arbitrários dinâmicos, ofuscados ou codificados em Base64.
   - **Scripts complexos e substituições:** Scripts batch ou encadeamentos complexos com expansão atrasada de variáveis de ambiente (`cmd /v:on /c "%VAR%"`).
2. **Execução não-confiável requer Sandbox Formal:**
   - Ambientes que executam código arbitrário ou não-confiável exigem isolamento formal em nível de kernel via container (Docker sandbox / gVisor) ou microVM efêmera (Firecracker), conforme previsto no roadmap arquitetural (W7+).

> [!WARNING]
> **Aviso de Segurança (Disclaimer Honesto):**
> Heurísticas baseadas em blocklist de strings reduzem acidentes, mas **não substituem** um ambiente de isolamento real contra agentes adversariais. Para ambientes de produção abertos a códigos arbitrários, o isolamento em containers (Docker sandbox / gVisor) ou máquinas virtuais efêmeras é o próximo passo obrigatório.

---

## Política de Execução de Comandos

A partir do Marco W7 (e consolidação W7.7), o harness adota uma **política de execução estrita por whitelist sem shell (`shell=False`)**, eliminando a interpretação gramatical do shell como vetor de ataque e evasão.

### Arquitetura em Duas Camadas

1. **Camada Primária (Whitelist sem Shell):**
   - **Execução sem Interpretador (`shell=False`):** O harness invoca executáveis externos diretamente via subprocesso sem shell. Não há passagem por `cmd.exe` ou `sh`. Metacaracteres como `|`, `&`, `;`, `>`, `<`, `$VAR` e `%VAR%` não são interpretados pelo sistema operacional, sendo tratados estritamente como argumentos literais ou rejeitados.
   - **Tokenizador Próprio (`_tokenizar`):** Divide a linha de comando por espaços respeitando aspas simples e duplas (o conteúdo entre aspas torna-se um único token sem as aspas envolventes), rejeitando comandos com aspas desbalanceadas antes de qualquer execução.
   - **Executáveis Autorizados (`COMANDOS_PERMITIDOS`):**
     - `dir`: Inspeção de diretórios do projeto (executada nativamente em Python para evitar dependência do shell).
     - `type`: Leitura rápida de arquivos (executada nativamente com validação de caminhos e bloqueio a arquivos protegidos).
     - `python`: Execução estrita de scripts Python dentro do projeto (`python <arquivo>.py`). Flags de interpretação inline (`-c`, `-m`, `-i`), referências com `..` e caminhos absolutos são categoricamente bloqueados.
     - `git`: **Metadados Puros por Allowlist Estrita** (`git status`, `git ls-files` e `git log --oneline`).
        - Subcomandos que exibem conteúdo ou diffs (`diff`, `show`, `log -p`, etc.) foram completamente eliminados do whitelist.
        - **Allowlist de Flags:** Para `status`, aceitam-se apenas flags de metadados (`--short`, `-s`, `--porcelain`, `--branch`, `-b`, `--untracked-files`, `-u`, `--ignored`, `--long`), bloqueando `-v`, `-vv`, `--verbose`, `-z`, `--null`. Para `ls-files`, aceitam-se apenas metadados (`--cached`, `-c`, `--others`, `-o`, `--stage`, `-s`, `-t`, `--full-name`, `--exclude-standard`, etc.). Para `log`, exige-se `--oneline` e aceitam-se `--stat`, `-n <N>`, `-n<N>`, `--max-count=<N>` e caminhos seguros.
        - **Pathspec Magic:** Qualquer argumento com prefixo `:` (ex.: `:(top).env`) é categoricamente rejeitado para impedir evasão de filtros de caminho.
        - **Redator Fail-Safe com Suporte a NUL:** Saídas são inspecionadas por linha e por registros NUL (`\x00`). Linhas ou registros que citem arquivos protegidos (incluindo padrões de renomeação `{old => new}`) são sumariamente omitidos.
         - **Trade-off de Super-Redação e Métrica de Auditoria:** Mensagens de commit que citem arquivos protegidos (ex.: `add .env`) fazem a linha inteira correspondente de `git log --oneline` ser omitida da resposta ao modelo. O redator contabiliza o total de linhas/registros omitidos e emite um log de auditoria em `stderr` para o desenvolvedor, sem expor métricas ao modelo para evitar inferência sobre a existência de arquivos protegidos.
      - `findstr`: Busca textual rápida (com fallback transparente em plataformas não-Windows, atuando como busca por substring direta em arquivos sem suporte a flags avançadas do findstr nativo do Windows).
      - `where`: Localização de executáveis seguros no PATH (com fallback cross-platform via `shutil.which`, tratando automaticamente o mapeamento de `python` para `python3` caso necessário).
      - `echo`: Impressão de texto no terminal (sem permitir redirecionamento via shell).
    - Qualquer binário fora da whitelist (ex: `rm`, `del`, `curl`, `powershell`, `cmd`, `bash`, `sh`, `nc`) é bloqueado imediatamente com código de saída `-1` e mensagem explicativa.

2. **Camada Secundária (Blocklist de Verbos e Proteção de Redirecionamentos — Defesa em Profundidade):**
   - **Aplicação Exclusiva no Verbo (Eliminação de Falsos Positivos em Leitura):** A blocklist de padrões destrutivos (`PADROES_BLOQUEADOS`) é avaliada exclusivamente contra o **verbo** do comando (e comandos internos de wrappers `cmd /c` ou `powershell -c`), nunca sobre o corpo dos argumentos. Evidência: buscas legítimas como `findstr "rm -rf" DOC.md` ou `findstr shutdown DOC.md` eram indevidamente recusadas (`rc=-1`), enquanto `findstr "format C:"` passava. Com a execução sem shell (`shell=False`), binários de leitura autorizados não executam comandos embutidos em texto, tornando seguro e necessário restringir a blocklist ao verbo executado.
   - Como salvaguarda adicional de retaguarda, verbos destrutivos (`format`, `diskpart`, `shutdown`, `rm`, `del`, etc.) e redirecionamentos para arquivos protegidos (`.env`, `.git/`, `.github/`) são interceptados categoricamente.

### Por que Whitelist sem Shell e Não Blocklist?

A migração da abordagem de blocklist pura para a whitelist sem shell foi impulsionada por **evidências empíricas obtidas ao longo de 3 rodadas de code review multi-modelo (v1, v2, v3)**, nas quais foram analisados **6 vetores de evasão** contra a execução baseada em blocklist com `shell=True` (5 reproduzidos nas auditorias e 1 identificado na análise arquitetural):

1. **Encadeamento de Comandos via `&` ou `&&`:** Comandos permitidos mascarando instruções subsequentes perigosas (ex: `dir & type .env`).
2. **Redirecionamentos de Saída para Arquivos Críticos:** Uso de operadores de fluxo (`echo x > .env` ou `type a > b && echo x >> .git/config`) para corromper credenciais ou histórico git.
3. **Wrappers de Interpretador:** Invocação através de interpretadores secundários (ex: `cmd /c "type .env"` ou `powershell -c "Get-Content .env"`), contornando checagens léxicas simples.
4. **Redirecionamento com Descritores Numéricos:** Uso de descritores de fluxo (`echo x 1> .env` ou `echo x 2>> .env`) que escapavam de regexes padrão de redirecionamento.
5. **Escape por Aspas e Espaços:** Variações com aspas aninhadas e caminhos relativos ofuscados que o interpretador do shell decodificava em runtime.
6. **Execução de Código Inline via Flags (identificado na análise arquitetural):** Uso de `python -c "import os; os.system('...')"` para rodar código arbitrário sem disparar as palavras-chave do shell.

Esses testes demonstraram que **nenhuma blocklist baseada em expressões regulares é capaz de cobrir exaustivamente a gramática recursiva de um shell (`cmd.exe` ou `sh`)**. Desativar o shell (`shell=False`) e limitar a execução a uma whitelist rigorosa com validação semântica de argumentos elimina toda essa classe de ataques por definição arquitetural.

### Limites Residuais

- **Execução de Scripts Python Locais:** O agente tem permissão para rodar `python <arquivo>.py` para executar seus próprios testes. Um script gerado pelo modelo com comportamento malicioso pode ainda ser executado caso seja gravado no projeto.
- **Necessidade de Sandbox para Autonomia Irrestrita:** Para ambientes abertos a tarefas arbitrárias e não supervisionadas, o isolamento em nível de container (Docker sandbox / gVisor) ou microVM efêmera (Firecracker) permanece como o padrão definitivo de contenção (W8+).

---

## Resultados do Benchmark

O benchmark automatizado (`python -m harness --bench`) executa 3 tarefas reais de engenharia de software contra o repositório, comparando o modo com **Cache Habilitado** (*prefixo invariante*) versus **Cache Desabilitado** (*prefixo quebrado intencionalmente a cada turno*).

Resultados medidos no modelo `gemini-3.8-flash` com o contexto do repositório (~26.000 tokens):

| Tarefa | Modo | Turnos | Prompt Tokens | Cached Tokens | Custo (USD) | Economia | Sucesso |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **T1: Mapa de Módulos** | ON | 5 | 184.217 | 130.804 | $0.052799 | 62,6% | SIM |
| *(criar bench_mapa.md com módulos de src/harness)* | OFF | 4 | 146.818 | 0 | $0.112510 | 0,0% | SIM |
| **T2: Geração + Teste Próprio** | ON | 5 | 176.620 | 163.510 | $0.022876 | 82,8% | SIM |
| *(criar bench_math.py + bench_test_math.py e rodar)* | OFF | 5 | 176.743 | 0 | $0.133562 | 0,0% | SIM |
| **T3: Spec + Teste Próprio** | ON | 6 | 211.947 | 196.185 | $0.027912 | 82,6% | SIM |
| *(criar bench_contador.py + bench_test_contador.py e rodar)* | OFF | 6 | 212.945 | 0 | $0.161520 | 0,0% | SIM |
| **TOTAL CACHE ON** | **ON** | **16** | **572.784** | **490.499** | **$0.103586** | **76,2%*** | **3/3** |
| **TOTAL CACHE OFF** | **OFF** | **15** | **536.506** | **0** | **$0.407592** | **0,0%** | **3/3** |

### Metodologia e Transparência

- **Tarefas Avaliadas:**
  - **T1 (mapa):** Criar `bench_mapa.md` listando cada módulo de `src/harness` com uma frase sobre sua responsabilidade, baseando-se no contexto do repositório e sem alterar arquivos existentes.
  - **T2 (geracao-com-teste):** Criar `bench_math.py` com função `soma(a, b)` e `bench_test_math.py` com validação de saída não-zero em caso de erro, e executar `python bench_test_math.py`.
  - **T3 (spec-de-arquivo):** Criar `bench_contador.py` com função `contar_palavras(t)` e `bench_test_contador.py` com casos de teste específicos, e executar `python bench_test_contador.py`.
- O modelo decide autonomamente a quantidade de turnos para cada tarefa (por exemplo, na T1 o modelo utilizou 5 turnos com cache e 4 turnos sem cache, explorando arquivos de forma independente).
- **Alternância de Ordem de Execução (Mitigação de Viés):** Para evitar que a ordem fixa (ON sempre antes de OFF) introduza viés de aquecimento de cache ou vantagens de latência no servidor do provedor, o benchmark alterna a ordem de execução a cada tarefa: T1 roda ON -> OFF, T2 roda OFF -> ON, e T3 roda ON -> OFF.
- **As Duas Bases de Cálculo de Economia (Transparência Total):**
  1. **74,6% — Comparação Direta entre Execuções Distintas:** O custo real da suíte completa com Cache OFF foi de **$0.407592** (15 turnos), enquanto com Cache ON foi de **$0.103586** (16 turnos). A razão direta `($0.407592 - $0.103586) / $0.407592` resulta em **74,6% de economia real**, mesmo com o agente executando 1 turno a mais na rodada com cache.
  2. **76,2% — Economia Contrafactual Turno a Turno (*):** O valor reportado na tabela de benchmark (`$0.331087 / 76,2%`) é a soma contrafactual calculada pelo harness sobre a exata execução com Cache ON: o que aqueles 16 turnos específicos teriam custado caso nenhum token tivesse sido servido pelo cache ($0.434673 contrafactual) versus o que de fato custaram com o desconto de cache ($0.103586 real).
- O cache só passa a atuar a partir do 2º turno de cada tarefa, quando o prefixo inicial da conversa já foi ingerido e reconhecido pelo provedor.

---

## Instalação e Configuração

### Pré-requisitos

- Python 3.10 ou superior (testado até Python 3.14).
- Chave de API do Google Gemini (`GEMINI_API_KEY`) e/ou DeepSeek (`DEEPSEEK_API_KEY`).

### Instalação

```bash
# Clone o repositório
git clone https://github.com/brmarcosbr/harness.git
cd harness

# Crie e ative o ambiente virtual
python -m venv .venv

# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1
# Linux / macOS
source .venv/bin/activate

# Instale o pacote local em modo editável com dependências de desenvolvimento
pip install -e .[dev]
```

### Configuração de Ambiente

Crie o arquivo `.env` na raiz do projeto:

```bash
cp .env.example .env
```

Edite o `.env` com suas credenciais:

```ini
GEMINI_API_KEY=sua_chave_gemini_aqui
DEEPSEEK_API_KEY=sua_chave_deepseek_aqui
HARNESS_PROVIDER=gemini
```

---

## Exemplos de Uso

### 1. Tarefa Básica no Terminal

```bash
python -m harness --tarefa "liste os arquivos desta pasta"
```

### 2. Tarefa com Contexto Completo do Repositório (Context Caching Ativo)

```bash
python -m harness --provider gemini --contexto-repo --tarefa "Analise a arquitetura de providers e sugira um novo adaptador"
```

### 3. Comparação com Cache Desabilitado

```bash
python -m harness --provider gemini --contexto-repo --no-cache --tarefa "Faça a mesma análise"
```

### 4. Execução com Provedor DeepSeek

```bash
python -m harness --provider deepseek --tarefa "Escreva uma função que calcula a sequência de Fibonacci"
```

### 5. Execução do Benchmark Automatizado

```bash
python -m harness --bench
```

---

### Exemplo de Execução Real

Abaixo, a transcrição da saída real de uma execução simples com o modelo `gemini-3.8-flash`:

```text
============================================================
AGENT HARNESS
Provider: GEMINI | Modelo: gemini-3.8-flash
============================================================
Tarefa: liste os arquivos desta pasta
Cache de contexto: ON (head sha256: c7ef1665ff5cbcc2)
Diretório atual: D:\Projetos\Antigravity\Harness
------------------------------------------------------------

>>> TURNO 1 / 8
Prompt tokens: 640
Completion tokens: 19
Total tokens: 862
Tamanho do texto gerado: 0 caracteres

[Tool Call] Função: 'executar_comando'
[Tool Exec] Executando 'executar_comando' com args: {'comando': 'dir /b'}
[Tool Output] Código: 0 | Stdout: 112 chars | Stderr: 0 chars

>>> TURNO 2 / 8
Prompt tokens: 930
Completion tokens: 20
Total tokens: 1044
Tamanho do texto gerado: 0 caracteres

[Tool Call] Função: 'ler_arquivo'
[Tool Exec] Executando 'ler_arquivo' com args: {'caminho': 'README.md'}
[Tool Output] Status: OK | Chaves: ['sucesso', 'conteudo', 'tamanho_bytes']

>>> TURNO 3 / 8
Prompt tokens: 1093
Completion tokens: 87
Total tokens: 1193
Tamanho do texto gerado: 214 caracteres

[Resposta Final do Modelo]:
Arquivos e diretórios presentes na pasta atual:

- `.env`
- `.env.example`
- `.github/`
- `.gitignore`
- `.pytest_cache/`
- `.venv/`
- `LICENSE`
- `pyproject.toml`
- `README.md`
- `src/`
- `tests/`
- `__pycache__/`

============================================================
RESUMO DA EXECUÇÃO
============================================================
Turnos utilizados: 3 de 8
Total Prompt Tokens: 2663
Total Cached Tokens: 0
Total Completion Tokens: 126
Total Geral de Tokens: 2789
Tokens estimados do historico final: 666
Custo real (com cache): $0.002470 USD
Custo se sem cache: $0.002470 USD | Economia: $0.000000 USD (0.0%)
Modelo final: gemini-3.8-flash
============================================================
```

*(Nota: Nesta tarefa curta de 3 turnos, o prefixo continha apenas o system prompt com 640 tokens, abaixo do limiar de 4.096 tokens para ativação do cache implícito do Gemini. O totalTokenCount reportado pela API da Gemini pode incluir tokens de thinking/raciocínio interno em modelos que suportam pensamento nativo. Ao fornecer `--contexto-repo`, o head atinge ~26k tokens e a economia atinge 76,2%, como demonstrado no benchmark).*

---

## Executando a Suíte de Testes

Os 140 testes unitários são executados 100% offline (utilizam mocks e providers fakes, sem dependência de rede ou consumo de cotas de API):

```bash
pytest tests/ -q
```

Saída esperada:

```text
............................................................................................................................................  [100%]
140 passed in 2.75s
```

Os testes cobrem:
- Cálculo e precisão de preços (Gemini e DeepSeek com janelas de cache, data de conferência e overrides por ambiente `PRECO_*`).
- Resolução e validação de segurança de ferramentas (whitelist sem shell, blocklist estrita em verbos, timeouts, path traversal).
- Garantia auditável de metacaracteres como literais (`dir & rm -rf /`, `type a.txt > b.txt` sem sobrescrita).
- Validação estrita de argumentos git, python, findstr, where e proteção contra symlink/junction traversal em todas as superfícies.
- Métrica de auditoria de super-redação do git com notificação em `stderr` (sem vazamento para o modelo).
- Visibilidade com aviso em `stderr` quando acionado o fallback posicional de tools no loop.
- Saneamento de credenciais do ambiente contra vazamento em subprocessos (por segmento/posição de token).
- Inversão de camadas de execução com validação primária de whitelist sem falsos positivos em comandos seguros (`echo format`, `findstr "rm -rf"`).
- Serialização e conversão de schemas nos formatos Gemini e OpenAI.
- Normalização e parsing de respostas multi-turnos com tool calling.
- Poda de contexto e garantia de prefix invariance.
- Execução do loop principal e suíte de benchmark.

---

## Roadmap

- [ ] **Sandboxing com Docker:** Executar ferramentas em containers efêmeros e isolados.
- [ ] **Suporte a Streaming:** Resposta de texto em tempo real via SSE/WebSockets.
- [ ] **Sumarização com LLM:** Resumir blocos de histórico podados em vez de apenas descartá-los.
- [ ] **Extensão Multi-Agente:** Orquestração de sub-agentes com especialidades distintas (pesquisa, codificação e revisão).

---

## Licença

Distribuído sob a licença MIT. Consulte [LICENSE](LICENSE) para mais informações.

Autor: **Bruno Marcos Bonifacio**
