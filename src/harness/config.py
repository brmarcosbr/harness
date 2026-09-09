"""Configurações e constantes do Agent Harness."""

from typing import Any, Dict

# Preços oficiais por 1 milhão de tokens (USD)
# Gemini: https://ai.google.dev/pricing (Paid tier Standard, conferido em set/2026)
# DeepSeek: https://api-docs.deepseek.com/quick_start/pricing (DeepSeek API, conferido em set/2026)
PROVIDER_PRECOS: Dict[str, Dict[str, float]] = {
    "gemini": {
        "input": 0.30,
        "output": 2.50,
        "cache": 0.03,
    },
    "deepseek": {
        # DeepSeek API (conferido em set/2026 via api-docs.deepseek.com/quick_start/pricing):
        # Cache Miss (input): $0.27 / 1M tokens
        # Output: $1.10 / 1M tokens
        # Cache Hit (context caching): $0.014 / 1M tokens
        "input": 0.27,
        "output": 1.10,
        "cache": 0.014,
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
    "gemini": "gemini-2.5-flash",
    "deepseek": "deepseek-chat",
    "openai": "gpt-4o-mini",
}

# Fallbacks padrão
DEFAULT_FALLBACKS = {
    "gemini": ["gemini-2.0-flash"],
    "deepseek": [],
    "openai": [],
}

# Endpoints base padrão
DEFAULT_BASE_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models",
    "deepseek": "https://api.deepseek.com/v1",
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

# Compatibilidade retroativa com W1a (gemini_client)
API_BASE_URL = DEFAULT_BASE_URLS["gemini"]
MODELOS_PADRAO = [DEFAULT_MODELS["gemini"]] + DEFAULT_FALLBACKS["gemini"]
PRECOS_PADRAO = PROVIDER_PRECOS["gemini"]
TOOL_DECLARATION = {
    "name": TOOL_DEFINITION_NEUTRA["name"],
    "description": TOOL_DEFINITION_NEUTRA["description"],
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "comando": {
                "type": "STRING",
                "description": TOOL_DEFINITION_NEUTRA["parameters"]["properties"]["comando"]["description"]
            }
        },
        "required": TOOL_DEFINITION_NEUTRA["parameters"]["required"]
    }
}
