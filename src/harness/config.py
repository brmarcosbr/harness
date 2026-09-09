"""Configurações e constantes do Agent Harness."""

# Preços oficiais do gemini-2.5-flash por 1 milhão de tokens (USD)
# Referência: https://ai.google.dev/pricing (Paid tier Standard, conferido em set/2026)
PRECO_INPUT_POR_1M = 0.30     # USD por 1M prompt tokens
PRECO_OUTPUT_POR_1M = 2.50    # USD por 1M completion tokens (inclui thinking tokens)
PRECO_CACHE_POR_1M = 0.03     # USD por 1M cached tokens (context caching, texto)

PRECOS_PADRAO = {
    "input": PRECO_INPUT_POR_1M,
    "output": PRECO_OUTPUT_POR_1M,
    "cache": PRECO_CACHE_POR_1M,
}

# Modelos padrão na ordem de tentativa (modelo primário + fallback sem duplicatas)
MODELOS_PADRAO = ["gemini-2.5-flash", "gemini-2.0-flash"]

MAX_TURNS = 3
COMMAND_TIMEOUT_SECONDS = 30
API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

SYSTEM_PROMPT = (
    "Você é um assistente operacional em um ambiente Windows. "
    "Você tem acesso à ferramenta 'executar_comando' para executar comandos no sistema. "
    "Ao usar 'executar_comando', forneça comandos compatíveis com o terminal Windows (PowerShell ou CMD). "
    "Cumpra os pedidos do usuário de forma concisa e direta."
)

TOOL_DECLARATION = {
    "function_declarations": [
        {
            "name": "executar_comando",
            "description": (
                "Executa um comando de linha de comando no terminal do Windows (PowerShell/CMD) "
                "no diretório atual de trabalho. Retorna stdout, stderr e o código de saída."
            ),
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "comando": {
                        "type": "STRING",
                        "description": "O comando de terminal a ser executado no Windows."
                    }
                },
                "required": ["comando"]
            }
        }
    ]
}
