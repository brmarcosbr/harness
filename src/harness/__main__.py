"""Ponto de entrada de linha de comando para o pacote harness."""

import argparse
import os
import sys
from pathlib import Path
from harness.config import MAX_TURNS
from harness.bench import REPETICOES_MINIMAS, REPETICOES_PADRAO
from harness.contexto import gerar_contexto_repo
from harness.env import carregar_env
from harness.errors import HarnessError
from harness.loop import executar_loop, garantir_saida_utf8
from harness.providers import criar_provider


def obter_api_key(provider_name: str) -> str:
    """Busca a chave de API apropriada para o provider informado."""
    carregar_env()
    p = provider_name.lower()

    if p == "gemini":
        return os.environ.get("GEMINI_API_KEY", "")
    elif p == "deepseek":
        return os.environ.get("DEEPSEEK_API_KEY", "")
    elif p == "openai":
        return os.environ.get("OPENAI_API_KEY", "")
    return ""


def criar_parser() -> argparse.ArgumentParser:
    """Cria e configura o parser de linha de comando com defaults resolvidos."""
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
        default=None,
        help="Instrução a ser executada pelo agente (padrão: 'liste os arquivos desta pasta')."
    )
    parser.add_argument(
        "--contexto",
        type=str,
        default=None,
        help="Caminho para arquivo de contexto do projeto (.md ou .txt, max 200 KB)."
    )
    parser.add_argument(
        "--contexto-repo",
        action="store_true",
        help="Gera e utiliza o contexto do repositório atual como contexto do projeto."
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Desabilita estabilidade do prefixo de contexto para forçar cache miss a cada turno."
    )
    parser.add_argument(
        "--bench",
        action="store_true",
        help="Executa a suíte de benchmark nas três condições (cache ON, cache OFF e sem poda)."
    )
    parser.add_argument(
        "--repeticoes",
        type=int,
        default=REPETICOES_PADRAO,
        help=f"Repetições por condição no benchmark (padrão: {REPETICOES_PADRAO}; mínimo: {REPETICOES_MINIMAS})."
    )
    parser.add_argument(
        "--cobertura",
        type=str,
        default="nao_declarada",
        help="Cobertura da conta de API declarada no cabeçalho do resultado ('credito', 'pago' ou 'nao_declarada')."
    )
    parser.add_argument(
        "--condicoes",
        type=str,
        default=None,
        help="Condições a medir no benchmark, separadas por vírgula (ON, OFF, SEM_PODA). Padrão: todas as três."
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=MAX_TURNS,
        help=f"Número máximo de turnos de execução (padrão: {MAX_TURNS})."
    )
    return parser


def parse_args(args=None) -> argparse.Namespace:
    """Carrega o ambiente .env antes de inicializar o parser e avalia os argumentos."""
    carregar_env()
    parser = criar_parser()
    return parser.parse_args(args)


def main():
    # Antes de qualquer saída: terminal hostil não pode derrubar uma execução já paga.
    garantir_saida_utf8()
    args = parse_args()

    if args.contexto and args.contexto_repo:
        print("ERRO: As opções --contexto e --contexto-repo são mutuamente exclusivas.", file=sys.stderr)
        sys.exit(1)

    contexto_conteudo = None
    if args.contexto_repo:
        contexto_conteudo = gerar_contexto_repo(Path.cwd())
    elif args.contexto:
        caminho_contexto = Path(args.contexto)
        if not caminho_contexto.is_file():
            print(f"ERRO: Arquivo de contexto não encontrado: '{args.contexto}'", file=sys.stderr)
            sys.exit(1)
        tamanho_bytes = caminho_contexto.stat().st_size
        if tamanho_bytes > 200 * 1024:
            print(
                f"ERRO: Arquivo de contexto excede o limite máximo de 200 KB ({tamanho_bytes} bytes): '{args.contexto}'",
                file=sys.stderr
            )
            sys.exit(1)
        try:
            contexto_conteudo = caminho_contexto.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            print(f"ERRO ao ler arquivo de contexto '{args.contexto}': {e}", file=sys.stderr)
            sys.exit(1)

    cache_habilitado = not args.no_cache

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
        if args.bench:
            if args.tarefa is not None:
                print(
                    "[AVISO] --tarefa ignorada no modo --bench: o benchmark possui sua própria suíte de tarefas.",
                    file=sys.stderr
                )
            if args.contexto or args.contexto_repo:
                print(
                    "[AVISO] --contexto / --contexto-repo ignorado no modo --bench: o benchmark gera contexto do repo para cada tarefa.",
                    file=sys.stderr
                )
            if args.no_cache:
                print(
                    "[AVISO] --no-cache ignorado no modo --bench: o benchmark avalia as três condições "
                    "(cache ON, cache OFF e sem poda).",
                    file=sys.stderr
                )

            from harness.bench import (agregar_por_celula, imprimir_resumo_celulas,
                                       imprimir_tabela, rodar_benchmark)

            def factory():
                return criar_provider(
                    provider_name=provider_name,
                    api_key=api_key,
                    modelo=args.modelo,
                    base_url=os.environ.get("HARNESS_BASE_URL", None)
                )

            resultados = rodar_benchmark(
                provider_factory=factory,
                max_turns=args.max_turns,
                repeticoes=args.repeticoes,
                cobertura=args.cobertura,
                condicoes=args.condicoes.split(",") if args.condicoes else None,
            )
            imprimir_tabela(resultados)
            imprimir_resumo_celulas(agregar_por_celula(resultados))
        else:
            tarefa_final = args.tarefa if args.tarefa is not None else "liste os arquivos desta pasta"
            provider = criar_provider(
                provider_name=provider_name,
                api_key=api_key,
                modelo=args.modelo,
                base_url=os.environ.get("HARNESS_BASE_URL", None)
            )
            executar_loop(
                tarefa=tarefa_final,
                provider=provider,
                max_turns=args.max_turns,
                contexto_projeto=contexto_conteudo,
                cache_habilitado=cache_habilitado,
            )
    except ValueError as e:
        # Erro de argumento (ex.: --condicoes com nome inválido): mensagem limpa, sem stack trace
        print(f"ERRO: {e}", file=sys.stderr)
        sys.exit(1)
    except HarnessError as e:
        print(f"ERRO: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERRO INESPERADO: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
