"""Ponto de entrada de linha de comando para o pacote harness."""

import argparse
import os
import sys
from harness.config import MAX_TURNS
from harness.env import carregar_env
from harness.errors import HarnessError
from harness.loop import executar_loop
from harness.providers import criar_provider


def obter_api_key(provider_name: str) -> str:
    """Busca a chave de API apropriada para o provider informado."""
    carregar_env()
    p = provider_name.lower()

    if p == "gemini":
        return os.environ.get("GEMINI_API_KEY", "")
    elif p == "deepseek":
        return os.environ.get("DEEPSEEK_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    elif p == "openai":
        return os.environ.get("OPENAI_API_KEY", "")
    else:
        # Fallback genérico para providers customizados
        return os.environ.get(f"{p.upper()}_API_KEY", "")


def main():
    parser = argparse.ArgumentParser(
        description="Agent Harness — Loop multi-turno agnóstico de provider."
    )
    parser.add_argument(
        "--provider",
        type=str,
        default=os.environ.get("HARNESS_PROVIDER", "gemini"),
        help="Provedor de LLM: 'gemini', 'deepseek' ou 'openai' (padrão: HARNESS_PROVIDER ou 'gemini')."
    )
    parser.add_argument(
        "--modelo",
        type=str,
        default=os.environ.get("HARNESS_MODELO", None),
        help="Modelo específico a ser usado (opcional; usa o padrão do provider se omitido)."
    )
    parser.add_argument(
        "--tarefa",
        type=str,
        default="liste os arquivos desta pasta",
        help="Instrução a ser executada pelo agente."
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=MAX_TURNS,
        help=f"Número máximo de turnos de execução (padrão: {MAX_TURNS})."
    )
    args = parser.parse_args()

    provider_name = args.provider.lower()
    api_key = obter_api_key(provider_name)

    if not api_key:
        var_nome = (
            "GEMINI_API_KEY" if provider_name == "gemini"
            else "DEEPSEEK_API_KEY" if provider_name == "deepseek"
            else "OPENAI_API_KEY"
        )
        print(
            f"ERRO: Chave de API para o provider '{provider_name}' não encontrada!\n"
            f"Defina a variável de ambiente {var_nome} ou configure-a em um arquivo .env:\n"
            f"  {var_nome}=sua_chave_aqui\n",
            file=sys.stderr
        )
        sys.exit(1)

    try:
        provider = criar_provider(
            provider_name=provider_name,
            api_key=api_key,
            modelo=args.modelo,
            base_url=os.environ.get("HARNESS_BASE_URL", None)
        )
        executar_loop(tarefa=args.tarefa, provider=provider, max_turns=args.max_turns)
    except HarnessError as e:
        print(f"ERRO: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERRO INESPERADO: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
