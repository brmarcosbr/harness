"""Ponto de entrada de linha de comando para o pacote harness."""

import argparse
import sys
from harness.gemini_client import carregar_api_key
from harness.loop import executar_loop


def main():
    parser = argparse.ArgumentParser(
        description="Agent Harness (Gemini API + Tool Use)."
    )
    parser.add_argument(
        "--tarefa",
        type=str,
        default="liste os arquivos desta pasta e crie um arquivo ola.txt com o conteúdo 'spike ok'",
        help="Instrução a ser executada pelo agente."
    )
    args = parser.parse_args()

    api_key = carregar_api_key()
    if not api_key:
        print(
            "ERRO: Chave de API da Gemini não encontrada!\n"
            "Defina a variável de ambiente GEMINI_API_KEY ou configure-a em um arquivo .env:\n"
            "  GEMINI_API_KEY=sua_chave_aqui\n",
            file=sys.stderr
        )
        sys.exit(1)

    executar_loop(args.tarefa, api_key)


if __name__ == "__main__":
    main()
