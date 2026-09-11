[English](README.md) · [Português (BR)](README.pt-BR.md)

# Agent Harness — Code Agent with Measured Context Caching

> Provider-agnostic multi-turn loop, safe tool use, and **74.6% to 76.2% cost savings via context caching** (real benchmark with Gemini).

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![Tests](https://img.shields.io/badge/tests-167%2F167%20passing-brightgreen)
![License MIT](https://img.shields.io/badge/license-MIT-green)
![Zero Libs](https://img.shields.io/badge/external--deps-zero-informational)
![CI](https://github.com/brmarcosbr/harness/actions/workflows/ci.yml/badge.svg)


**Agent Harness** is a pure-Python implementation (no heavy frameworks or external runtime dependencies) of a harness for autonomous software engineering agents. It runs multi-turn loops with tool resolution (*tool calling*), supports multiple providers (Google Gemini and OpenAI-compatible / DeepSeek), and manages the session context to enable and measure the efficiency of **Context Caching**.

---

## Contents

- [Highlights](#highlights)
- [Architecture](#architecture)
  - [The Turn Loop](#the-turn-loop)
  - [Context Management and Prefix Invariance](#context-management-and-prefix-invariance)
- [Security](#security)
- [Command Execution Policy](#command-execution-policy)
- [Benchmark Results](#benchmark-results)
- [Installation and Setup](#installation-and-setup)
- [Usage Examples](#usage-examples)
  - [Real Execution Example](#real-execution-example)
- [Running the Test Suite](#running-the-test-suite)
- [Roadmap](#roadmap)
- [License](#license)

---

## Highlights

- **Zero External Dependencies in Production:** Strictly uses the Python standard library (`urllib`, `json`, `dataclasses`, `subprocess`, `argparse`). `pytest` is the only development dependency.
- **Provider-Neutral:** Unified message and tool protocol, dynamically adapted to Gemini's native schema (including Gemini 3+) or to the OpenAI / DeepSeek standard.
- **Measured Context Caching:** Stable deterministic prefixes (*prefix invariance*) let the Gemini API reuse cached tokens from the 2nd turn onward, reducing costs by more than 74% (up to 76.2% counterfactual).
- **Active Context Pruning:** *head-body-tail* algorithm that keeps the cacheable prefix intact at the top (*head*), discards intermediate turns when the token budget overflows (*body*), and preserves the most recent turns (*tail*).
- **Safe Tools with Blocklist and Path Protection:** Terminal commands, file reading, writing, and search come with strict restrictions against destructive commands and *path traversal*.

---

## Architecture

### The Turn Loop

On each turn, the harness sends the structured history to the model. If the model replies requesting tool calls (*tool calls*), the harness dispatches the executions to the tool registry, appends the outputs as tool response messages, and triggers the next turn. When the model returns plain text, the cycle ends.

```
                      +-----------------------------+
                      |   User (Instruction/CLI)    |
                      +--------------+--------------+
                                     |
                                     v
                       +---------------------------+
                       | Context Assembly          |
                       | [Head + Body + Tail]      |
                       +-------------+-------------+
                                     |
                                     v
                       +---------------------------+
                       | Provider Adapter          |
                       | (Gemini / DeepSeek/OpenAI)|
                       +-------------+-------------+
                                     |
                                     v
                             +---------------+
                             |   LLM Call    |
                             +-------+-------+
                                     |
                   +-----------------+-----------------+
                   |                                   |
             [Tool Calls]                         [Final Text]
                   |                                   |
                   v                                   v
        +----------------------+             +-------------------+
        | Tool Registry        |             | Execution End     |
        | - executar_comando   |             | Cost Summary      |
        | - ler_arquivo        |             +-------------------+
        | - escrever_arquivo   |
        | - buscar_no_projeto  |
        +----------+-----------+
                   |
                   v
        +----------------------+
        | Append Results       |
        | Prune if Over Limit  |
        +----------+-----------+
                   |
                   +----> Next Turn (Loop)
```

### Context Management and Prefix Invariance

Modern LLM providers such as Google Gemini support automatic *implicit context caching* for static prefixes exceeding 4,096 tokens. For the cache to be used, the conversation's initial prefix must be identical on every turn (*Prefix Invariance*).

The context module splits the window into 3 regions:

```
+--------------------------------------------------------------------+
| 1. HEAD (Deterministic & Stable)                                   |
|    - System Prompt                                                 |
|    - Repository Context (when provided)                            |
|    * NEVER contains timestamps, volatile hashes, or random order   |
|    * Primary cache-hit target across turns 2..N                    |
+--------------------------------------------------------------------+
| 2. BODY (History Pruning)                                          |
|    - Intermediate tool-call and reasoning turns                    |
|    - Dropped in pairs (call + response) if tokens overflow         |
+--------------------------------------------------------------------+
| 3. TAIL (Local Preservation)                                       |
|    - Most recent turns (guaranteeing immediate coherence)          |
|    - Agent's final answer                                          |
+--------------------------------------------------------------------+
```

When the `--no-cache` flag is provided, a dynamic header containing a timestamp (`time.time()`, float seconds) is deliberately injected at the beginning of the user message, breaking prefix invariance and forcing a *cache miss* on every turn for comparison purposes.

---

## Security

The harness exposes 4 native tools to the model:

1. `executar_comando`: Execution of whitelist-controlled commands without a shell (`shell=False`) in the project directory, with a 1 MB cap on subprocess output.
2. `ler_arquivo`: Reading of text files with a maximum limit of 200 KB per file.
3. `escrever_arquivo`: Creation and overwriting of files with a 1 MB limit.
4. `buscar_no_projeto`: Regex pattern search over the contents of the project's code/text files (ignores the `.venv`, `__pycache__`, `.git`, `.pytest_cache`, `build`, and `dist` folders; allows optional filtering by extension; ignores binaries and files larger than 1 MB; maximum of 50 results; 10-second timeout and extended ReDoS protection). It does **not** search by filename.

- **Critical Command Blocklist:** Regex-based blocking of the dangerous patterns mapped in `PADROES_BLOQUEADOS`: `format`, `diskpart`, `shutdown`, `rd /s` (or `/q`), `rmdir /s` (or `/q`), `rm -rf`, `reg delete`, `del /s` (or `/f` or `/q`), `erase /s` (or `/f` or `/q`), `cipher /w`, and `taskkill /f /im`.
- **Configurable Protected Paths (`CAMINHOS_PROTEGIDOS`):** Centralized in `config.py` and checked by the authoritative function `resolver_caminho_seguro`:
  - `bloqueio_total` (reading and writing blocked): `.env` (blocking `.env`, `.env.*`, and `.env_*`, with a safe exception for templates such as `.env.example`, `.env.sample`, and `.env.template`) and `.git/` (all repository objects, configs, and references);
  - `somente_escrita` (reading allowed if needed, writing categorically blocked): `.github/` (protects GitHub Actions workflows and automation files against overwriting or corruption by the model).
- **Protection against Path Traversal and Symlink Escape:** Strict validation via `resolver_caminho_seguro` ensuring that no path accesses folders above the project root (`..` forbidden) or escapes the project tree through symlinks or NTFS junctions.
- **Hard Timeouts and Memory Caps:** Each command execution has a default limit of 30 seconds and chunked reading capped at no more than 1 MB of subprocess output, preventing hangs or memory exhaustion caused by noisy scripts. File search is limited to 10 seconds.
- **Kwargs Validation in the Loop against the Schema:** When the loop dispatches tools, any argument not expressly declared in the canonical schema (`TOOLS`) is summarily rejected before execution, preventing the injection of spurious parameters (`base_dir`, etc.).
- **Cross-Platform Protection via Whitelist:** Shell-less process execution (`shell=False`) restricted to a whitelist of allowed binaries prevents destructive commands from running on both Windows (`cmd.exe`) and POSIX/Linux environments (`rm -rf`, `mkfs`, etc.). Native handlers such as `type`, `dir`, `where`, and `findstr` have transparent emulation in Python for full portability.

### Known Limits of the Blocklist (Mitigation vs. Sandboxing)

The combination of command blocklist, redirection inspection, ReDoS protection, and protected-path validation is a pragmatic mitigation layer (*defense-in-depth*) designed for assisted local development and testing, **NOT** a formal security sandbox:

1. **What the blocklist does NOT protect against:**
   - **Arbitrary binaries invoked by the model:** Tools such as `curl`, `wget`, or `bitsadmin` downloading external scripts or executables.
   - **Execution of arbitrary code in interpreters:** Commands such as `python -c "..."` or `powershell` running arbitrary, obfuscated, or Base64-encoded dynamic payloads.
   - **Complex scripts and substitutions:** Batch scripts or complex chains with delayed expansion of environment variables (`cmd /v:on /c "%VAR%"`).
2. **Untrusted Execution Requires a Formal Sandbox:**
   - Environments that execute arbitrary or untrusted code require formal kernel-level isolation via a container (Docker sandbox / gVisor) or an ephemeral microVM (Firecracker), as foreseen in the architectural roadmap (W7+).

> [!WARNING]
> **Security Notice (Honest Disclaimer):**
> String blocklist-based heuristics reduce accidents, but they **do not replace** a real isolation environment against adversarial agents. For production environments open to arbitrary code, isolation in containers (Docker sandbox / gVisor) or ephemeral virtual machines is the mandatory next step.

---

## Command Execution Policy

As of Milestone W7 (and the W7.7 consolidation), the harness adopts a **strict whitelist execution policy without a shell (`shell=False`)**, eliminating shell grammar interpretation as an attack and evasion vector.

### Two-Layer Architecture

1. **Primary Layer (Shell-less Whitelist):**
   - **Execution without an Interpreter (`shell=False`):** The harness invokes external executables directly via subprocess without a shell. There is no pass through `cmd.exe` or `sh`. Metacharacters such as `|`, `&`, `;`, `>`, `<`, `$VAR`, and `%VAR%` are not interpreted by the operating system, being treated strictly as literal arguments or rejected.
   - **Own Tokenizer (`_tokenizar`):** Splits the command line by spaces while respecting single and double quotes (the content between quotes becomes a single token without the surrounding quotes), rejecting commands with unbalanced quotes before any execution.
   - **Authorized Executables (`COMANDOS_PERMITIDOS`):**
     - `dir`: Inspection of project directories (executed natively in Python to avoid depending on the shell).
     - `type`: Quick reading of files (executed natively with path validation and blocking of protected files).
     - `python`: Strict execution of Python scripts inside the project (`python <file>.py`). Inline interpreter flags (`-c`, `-m`, `-i`), references with `..`, and absolute paths are categorically blocked.
     - `git`: **Pure Metadata via Strict Allowlist** (`git status`, `git ls-files`, and `git log --oneline`).
        - Subcommands that display content or diffs (`diff`, `show`, `log -p`, etc.) were completely removed from the whitelist.
        - **Flag Allowlist:** For `status`, only metadata flags are accepted (`--short`, `-s`, `--porcelain`, `--branch`, `-b`, `--untracked-files`, `-u`, `--ignored`, `--long`), blocking `-v`, `-vv`, `--verbose`, `-z`, `--null`. For `ls-files`, only metadata is accepted (`--cached`, `-c`, `--others`, `-o`, `--stage`, `-s`, `-t`, `--full-name`, `--exclude-standard`, etc.). For `log`, `--oneline` is required and `--stat`, `-n <N>`, `-n<N>`, `--max-count=<N>`, and safe paths are accepted.
        - **Pathspec Magic:** Any argument with a `:` prefix (e.g., `:(top).env`) is categorically rejected to prevent evasion of path filters.
        - **Fail-Safe Redactor with NUL Support:** Outputs are inspected line by line and by NUL records (`\x00`). Lines or records that cite protected files (including rename patterns `{old => new}`) are summarily omitted.
         - **Over-Redaction Trade-off and Audit Metric:** Commit messages that cite protected files (e.g., `add .env`) cause the entire corresponding line of `git log --oneline` to be omitted from the response to the model. The redactor counts the total number of omitted lines/records and emits an audit log to `stderr` for the developer, without exposing metrics to the model, in order to avoid inference about the existence of protected files.
      - `findstr`: Fast text search (with transparent fallback on non-Windows platforms, acting as a direct substring search in files without support for advanced flags of Windows' native findstr).
      - `where`: Location of safe executables in PATH (with a cross-platform fallback via `shutil.which`, automatically handling the mapping of `python` to `python3` when necessary).
      - `echo`: Printing text to the terminal (without allowing redirection via the shell).
    - Any binary outside the whitelist (e.g., `rm`, `del`, `curl`, `powershell`, `cmd`, `bash`, `sh`, `nc`) is blocked immediately with exit code `-1` and an explanatory message.

2. **Secondary Layer (Verb Blocklist and Redirection Protection — Defense in Depth):**
   - **Exclusive Application on the Verb (Elimination of False Positives in Reading):** The destructive pattern blocklist (`PADROES_BLOQUEADOS`) is evaluated exclusively against the **verb** of the command (and the internal commands of `cmd /c` or `powershell -c` wrappers), never over the body of the arguments. Evidence: legitimate searches such as `findstr "rm -rf" DOC.md` or `findstr shutdown DOC.md` were unduly refused (`rc=-1`), while `findstr "format C:"` passed. With shell-less execution (`shell=False`), authorized reading binaries do not execute commands embedded in text, which makes it safe and necessary to restrict the blocklist to the executed verb.
   - As an additional backstop safeguard, destructive verbs (`format`, `diskpart`, `shutdown`, `rm`, `del`, etc.) and redirections to protected files (`.env`, `.git/`, `.github/`) are categorically intercepted.

### Why a Shell-less Whitelist and Not a Blocklist?

The migration from the pure blocklist approach to the shell-less whitelist was driven by **empirical evidence obtained over 3 rounds of multi-model code review (v1, v2, v3)**, in which **6 evasion vectors** against blocklist-based execution with `shell=True` were analyzed (5 reproduced in the audits and 1 identified in the architectural analysis):

1. **Command Chaining via `&` or `&&`:** Allowed commands masking subsequent dangerous instructions (e.g., `dir & type .env`).
2. **Output Redirections to Critical Files:** Use of stream operators (`echo x > .env` or `type a > b && echo x >> .git/config`) to corrupt credentials or git history.
3. **Interpreter Wrappers:** Invocation through secondary interpreters (e.g., `cmd /c "type .env"` or `powershell -c "Get-Content .env"`), bypassing simple lexical checks.
4. **Redirection with Numeric Descriptors:** Use of stream descriptors (`echo x 1> .env` or `echo x 2>> .env`) that escaped standard redirection regexes.
5. **Escaping via Quotes and Spaces:** Variations with nested quotes and obfuscated relative paths that the shell interpreter decoded at runtime.
6. **Inline Code Execution via Flags (identified in the architectural analysis):** Use of `python -c "import os; os.system('...')"` to run arbitrary code without triggering shell keywords.

These tests demonstrated that **no regular-expression-based blocklist is capable of exhaustively covering the recursive grammar of a shell (`cmd.exe` or `sh`)**. Disabling the shell (`shell=False`) and limiting execution to a rigorous whitelist with semantic validation of arguments eliminates this entire class of attacks by architectural definition.

### Residual Limits

- **Execution of Local Python Scripts:** The agent is allowed to run `python <file>.py` to execute its own tests. A script generated by the model with malicious behavior can still be executed if it is written into the project.
- **Need for a Sandbox for Unrestricted Autonomy:** For environments open to arbitrary and unsupervised tasks, container-level isolation (Docker sandbox / gVisor) or an ephemeral microVM (Firecracker) remains the definitive containment standard (W8+).

---

## Benchmark Results

The automated benchmark (`python -m harness --bench`) runs 3 real software engineering tasks against the repository, comparing **Cache Enabled** mode (*invariant prefix*) versus **Cache Disabled** mode (*prefix intentionally broken on every turn*).

The suite currently runs **three conditions with N repetitions each** (default 5, minimum 3):

- **ON** — cache ON + history pruning ON (baseline).
- **OFF** — cache OFF + history pruning ON (isolates the cache effect).
- **SEM_PODA** — cache OFF + history pruning OFF. Declared explicitly, not inferred: the third condition isolates the effect of the *pruning policy*, and its comparison base is the OFF condition.

Each execution is recorded individually and never aggregated before being written. Per task × condition the suite reports the median and range of cost, latency, turns and tokens, plus the success rate (n of N). Because the validator changed after the pilot was observed, the record stores both criteria — the current one and the strict historical one (test file containing the substring `assert`) — and the summary reports both success rates side by side, so the effect of the change is visible instead of chosen after the fact. Raw results are written to `bench_<date>_<provider>.json` under a header stating date, driver and version, effective model, `reasoning_effort`, `max_tokens`, tariff window (peak/off-peak), tariff used, account coverage, number of repetitions, replacement policy and repository commit.

Failures are classified instead of lumped together. A **task failure** (there were turns, the validation did not pass) counts in the denominator as a normal failure. An **abort** (0 turns, or a connection/API error) means nothing was measured — it is instrument failure, not a model attempt: it is recorded with `tipo_falha`, excluded from the success-rate denominator, and replaced, up to 2 replacements per cell. If that ceiling is exceeded, the collection stops and reports why in the header.

The table below is the published measurement (Gemini, 2 conditions, a single run each) and is kept as the historical record:

| Task | Mode | Turns | Prompt Tokens | Cached Tokens | Cost (USD) | Savings | Success |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **T1: Module Map** | ON | 5 | 184,217 | 130,804 | $0.052799 | 62.6% | YES |
| *(create bench_mapa.md with the modules of src/harness)* | OFF | 4 | 146,818 | 0 | $0.112510 | 0.0% | YES |
| **T2: Generation + Own Test** | ON | 5 | 176,620 | 163,510 | $0.022876 | 82.8% | YES |
| *(create bench_math.py + bench_test_math.py and run them)* | OFF | 5 | 176,743 | 0 | $0.133562 | 0.0% | YES |
| **T3: Spec + Own Test** | ON | 6 | 211,947 | 196,185 | $0.027912 | 82.6% | YES |
| *(create bench_contador.py + bench_test_contador.py and run them)* | OFF | 6 | 212,945 | 0 | $0.161520 | 0.0% | YES |
| **TOTAL CACHE ON** | **ON** | **16** | **572,784** | **490,499** | **$0.103586** | **76.2%*** | **3/3** |
| **TOTAL CACHE OFF** | **OFF** | **15** | **536,506** | **0** | **$0.407592** | **0.0%** | **3/3** |

### Methodology and Transparency

- **Tasks Evaluated:**
  - **T1 (map):** Create `bench_mapa.md` listing each module of `src/harness` with one sentence about its responsibility, based on the repository context and without changing existing files.
  - **T2 (generation-with-test):** Create `bench_math.py` with a `soma(a, b)` function and `bench_test_math.py` with non-zero output validation in case of error, and run `python bench_test_math.py`.
  - **T3 (file-spec):** Create `bench_contador.py` with a `contar_palavras(t)` function and `bench_test_contador.py` with specific test cases, and run `python bench_test_contador.py`.
- The model autonomously decides the number of turns for each task (for example, in T1 the model used 5 turns with cache and 4 turns without cache, exploring files independently).
- **Alternating Execution Order (Bias Mitigation):** To prevent the fixed order (ON always before OFF) from introducing cache warm-up bias or latency advantages on the provider's server, the benchmark alternates the execution order on each task: T1 runs ON -> OFF, T2 runs OFF -> ON, and T3 runs ON -> OFF.
- **The Two Bases for Calculating Savings (Full Transparency):**
  1. **74.6% — Direct Comparison between Distinct Runs:** The real cost of the complete suite with Cache OFF was **$0.407592** (15 turns), while with Cache ON it was **$0.103586** (16 turns). The direct ratio `($0.407592 - $0.103586) / $0.407592` results in **74.6% real savings**, even with the agent running 1 extra turn in the cached round.
  2. **76.2% — Turn-by-Turn Counterfactual Savings (*):** The value reported in the benchmark table (`$0.331087 / 76.2%`) is the counterfactual sum calculated by the harness over the exact Cache ON run: what those 16 specific turns would have cost if no token had been served from cache ($0.434673 counterfactual) versus what they actually cost with the cache discount ($0.103586 real).
- The cache only starts acting from the 2nd turn of each task, when the conversation's initial prefix has already been ingested and recognized by the provider.

---

## Installation and Setup

### Prerequisites

- Python 3.10 or higher (tested up to Python 3.14).
- Google Gemini API key (`GEMINI_API_KEY`) and/or DeepSeek (`DEEPSEEK_API_KEY`).

### Installation

```bash
# Clone the repository
git clone https://github.com/brmarcosbr/harness.git
cd harness

# Create and activate the virtual environment
python -m venv .venv

# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1
# Linux / macOS
source .venv/bin/activate

# Install the local package in editable mode with dev dependencies
pip install -e .[dev]
```

### Environment Configuration

Create the `.env` file at the project root:

```bash
cp .env.example .env
```

Edit the `.env` with your credentials:

```ini
GEMINI_API_KEY=sua_chave_gemini_aqui
DEEPSEEK_API_KEY=sua_chave_deepseek_aqui
HARNESS_PROVIDER=gemini

# Optional: reasoning effort and output ceiling sent to the OpenAI-compatible endpoint.
# Defaults: high and 65536. Accepted effort values: low, high, max (medium and xhigh
# are aliases that resolve to high). Invalid values are ignored with a warning on stderr.
# HARNESS_REASONING_EFFORT=high
# HARNESS_MAX_TOKENS=65536
```

---

## Usage Examples

### 1. Basic Task in the Terminal

```bash
python -m harness --tarefa "liste os arquivos desta pasta"
```

### 2. Task with Full Repository Context (Context Caching Active)

```bash
python -m harness --provider gemini --contexto-repo --tarefa "Analise a arquitetura de providers e sugira um novo adaptador"
```

### 3. Comparison with Cache Disabled

```bash
python -m harness --provider gemini --contexto-repo --no-cache --tarefa "Run the same analysis"
```

### 4. Run with the DeepSeek Provider

```bash
python -m harness --provider deepseek --tarefa "Write a function that computes the Fibonacci sequence"
```

### 5. Running the Automated Benchmark

```bash
python -m harness --bench
```

---

### Real Execution Example

Below is the transcript of the real output of a simple run with the `gemini-3.8-flash` model:

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

*(Note: In this short 3-turn task, the prefix contained only the system prompt with 640 tokens, below the 4,096-token threshold for activating Gemini's implicit cache. The totalTokenCount reported by the Gemini API may include thinking/internal reasoning tokens in models that support native thinking. When providing `--contexto-repo`, the head reaches ~26k tokens and the savings reach 76.2%, as demonstrated in the benchmark).*

---

## Running the Test Suite

The 167 unit tests run 100% offline (they use mocks and fake providers, with no network dependency or API quota consumption):

```bash
pytest tests/ -q
```

Expected output:

```text
.......................................................................................................................................................................  [100%]
167 passed in 4.67s
```

The tests cover:
- Price calculation and accuracy (Gemini and DeepSeek with cache windows, verification date, and `PRECO_*` environment overrides).
- Reasoning effort and output ceiling declared in the request body (`HARNESS_REASONING_EFFORT` / `HARNESS_MAX_TOKENS` overrides) instead of depending on server defaults.
- Benchmark collection rigor: three conditions (cache ON, cache OFF and no pruning), N repetitions with median and range per task × condition, peak/off-peak tariff window as a pure function, and agent instruction files (`AGENTS.md`, `CLAUDE.md`, `.claude/`) kept out of the repository context head.
- Per-turn prefix telemetry (head hash and stability flag) and latency decomposed into model and tool time.
- Collection robustness: printing that cannot kill a paid run (UTF-8 reconfiguration plus a stream that degrades instead of raising), abort versus task failure classification with the success rate computed only over non-aborted executions, a replacement policy capped per cell, and results written explicitly as UTF-8 (`ensure_ascii=False`), so the artifact does not depend on the machine's local encoding.
- Tool security resolution and validation (shell-less whitelist, strict verb blocklist, timeouts, path traversal).
- Auditable guarantee that metacharacters are literals (`dir & rm -rf /`, `type a.txt > b.txt` without overwriting).
- Strict validation of git, python, findstr, and where arguments, and protection against symlink/junction traversal on all surfaces.
- Audit metric for git over-redaction with notification on `stderr` (without leaking to the model).
- Visibility with a warning on `stderr` when the positional tool fallback in the loop is triggered.
- Sanitization of environment credentials against leaking into subprocesses (by token segment/position).
- Inversion of execution layers with primary whitelist validation without false positives on safe commands (`echo format`, `findstr "rm -rf"`).
- Serialization and conversion of schemas in the Gemini and OpenAI formats.
- Normalization and parsing of multi-turn responses with tool calling.
- Context pruning and prefix invariance guarantee.
- Main loop execution and benchmark suite.

---

## Roadmap

- [ ] **Docker Sandboxing:** Run tools in ephemeral, isolated containers.
- [ ] **Streaming Support:** Real-time text response via SSE/WebSockets.
- [ ] **LLM Summarization:** Summarize pruned history blocks instead of merely discarding them.
- [ ] **Multi-Agent Extension:** Orchestration of sub-agents with distinct specialties (research, coding, and review).

---

## License

Distributed under the MIT license. See [LICENSE](LICENSE) for more information.

Author: **Bruno Marcos Bonifacio**
