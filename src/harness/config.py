"""Configurações e constantes do Agent Harness."""

from typing import Any, Dict

# Preços oficiais por 1 milhão de tokens (USD)
# Gemini: https://ai.google.dev/pricing (Paid tier Standard, conferido em set/2026)
# DeepSeek: https://api-docs.deepseek.com/quick_start/pricing (conferido em set/2026, modelo deepseek-v4-flash)
# Tabela oficial DeepSeek (deepseek-v4-flash):
#   - Off-peak (seg-sex 16:30-08:30 UTC, sáb-dom all day):
#       input (cache miss): $0.22 / 1M | output: $0.66 / 1M | cache (hit): $0.007 / 1M
#   - Peak (seg-sex 01:00-04:00 e 06:00-10:00 UTC):
#       preço = 2x o valor off-peak (input: $0.44 / output: $1.32 / cache: $0.014 / 1M)
# NOTA: O custo calculado pelo harness é uma estimativa baseada nos valores OFF-PEAK.
PROVIDER_PRECOS: Dict[str, Dict[str, float]] = {
    "gemini": {
        # Preços gemini-3.8-flash: promoção até 31/12/2026; dobra a partir de 01/01/2027
        # (input 1.50, output 7.50, cache 0.15) — página ai.google.dev/pricing
        "input": 0.75,
        "output": 3.75,
        "cache": 0.075,
    },
    "deepseek": {
        "input": 0.22,
        "output": 0.66,
        "cache": 0.007,
    },
    "openai": {
        # Referência padrão OpenAI gpt-4o-mini
        "input": 0.15,
        "output": 0.60,
        "cache": 0.075,
    }
}

# Modelos padrão para cada provider
DEFAULT_MODELS = {
    "gemini": "gemini-3.8-flash",
    "deepseek": "deepseek-v4-flash",
    "openai": "gpt-4o-mini",
}

# Fallbacks padrão
DEFAULT_FALLBACKS = {
    "gemini": ["gemini-3.6-flash"],
    "deepseek": [],
    "openai": [],
}

# Endpoints base padrão (o endpoint /chat/completions é anexado no providers.py)
DEFAULT_BASE_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models",
    "deepseek": "https://api.deepseek.com",
    "openai": "https://api.openai.com/v1",
}

MAX_TURNS = 3
COMMAND_TIMEOUT_SECONDS = 30

SYSTEM_PROMPT = (
    "Você é um assistente operacional em um ambiente Windows. "
    "Você tem acesso à ferramenta 'executar_comando' para executar comandos no sistema. "
    "Ao usar 'executar_comando', forneça comandos compatíveis com o terminal Windows (PowerShell ou CMD). "
    "Cumpra os pedidos do usuário de forma concisa e direta."
)

# Definição neutra única da ferramenta executar_comando
TOOL_DEFINITION_NEUTRA: Dict[str, Any] = {
    "name": "executar_comando",
    "description": (
        "Executa um comando de linha de comando no terminal do Windows (PowerShell/CMD) "
        "no diretório atual de trabalho. Retorna stdout, stderr e o código de saída."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "comando": {
                "type": "string",
                "description": "O comando de terminal a ser executado no Windows."
            }
        },
        "required": ["comando"]
    }
}

