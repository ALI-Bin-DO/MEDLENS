"""Generate and validate (but never execute) SQL for an English question. Run from the project root:

    python -m backend.scripts.preview_sql "What is the total amount of paid bills?"

Builds the "full schema + retrieved documentation/examples" context, sends it to OLLAMA_SQL_MODEL,
prints the reply, and runs sql_validator on replies classified as SQL. Nothing is sent to MySQL.
This is a preview tool, not an API endpoint.
"""

import argparse
import sys

from backend.app.config import MissingSettingError, get_settings
from backend.app.repositories.vector_repository import VectorRepository, VectorStoreError
from backend.app.services.llm_service import LLMError, LLMService, build_sql_prompt, estimate_prompt_tokens
from backend.app.services.ollama_client import OllamaError
from backend.app.services.rag.context_builder import ContextBuilder
from backend.app.services.rag.embedding_service import EmbeddingService
from backend.app.services.rag.retriever import Retriever
from backend.app.services.sql_validator import validate

RULE = "-" * 72


def fmt(seconds: float | None) -> str:
    return "not reported" if seconds is None else f"{seconds:.2f} s"


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview SQL generation (no validation, no execution).")
    parser.add_argument("question", help="English question (quote it)")
    args = parser.parse_args()
    settings = get_settings()
    llm = LLMService(settings)

    try:
        # Close the vector store before the (slow) LLM call so other processes are not locked out.
        with VectorRepository(settings) as repo:
            context = ContextBuilder(Retriever(EmbeddingService(settings), repo), repo).build(args.question)
        prompt = build_sql_prompt(args.question, context.text)
        print(f"Question: {args.question}")
        print(f"Context strategy: {context.strategy}")
        print(f"  schema (always included): {', '.join(c.id for c in context.schema)}")
        print(f"  retrieved: {', '.join(f'{r.chunk.id} ({r.score:.3f})' for r in context.retrieved) or '(none)'}")
        print(f"Prompt: {prompt.characters} characters, ~{estimate_prompt_tokens(prompt)} tokens (pessimistic estimate); "
              f"num_ctx={settings.ollama_sql_num_ctx}, num_predict={settings.ollama_sql_num_predict}")
        result = llm.generate_sql(args.question, context.text)
    except (OllamaError, MissingSettingError, VectorStoreError) as exc:
        kind = "Generation failed" if isinstance(exc, LLMError) else "Failed"
        sys.exit(f"{kind}: {exc}")

    s = result.stats
    print(f"Model: {result.model} | done_reason: {s.done_reason} | "
          f"prompt tokens (Ollama): {s.prompt_tokens} | output tokens: {s.output_tokens}")
    print(f"Timings: load {fmt(s.load_seconds)} | prompt processing {fmt(s.prompt_seconds)} | "
          f"generation {fmt(s.generation_seconds)} | total {fmt(s.total_seconds)}")
    if result.markdown_removed:
        print("Note: the model wrapped its reply in a Markdown code fence; the fence was removed.")

    if result.kind != "sql":
        label = {
            "cannot_answer": "MODEL SAYS: CANNOT ANSWER (no SQL generated, nothing to validate)",
            "needs_clarification": "MODEL ASKS FOR CLARIFICATION (no SQL generated, nothing to validate)",
        }[result.kind]
        print(f"\n{RULE}\n{label}\n{RULE}\n{result.text}\n{RULE}")
        return

    print(f"\n{RULE}\nGENERATED SQL\n{RULE}\n{result.text}\n{RULE}")
    validation = validate(result.text)
    if validation.ok:
        print("VALIDATION PASSED — NOT EXECUTED")
        print(f"  tables read: {', '.join(validation.tables) or '(none)'}")
        print("  (passing means the query is in the allowed read-only subset; it does not prove the answer is correct)")
    else:
        print("VALIDATION REJECTED")
        print(f"  code:   {validation.code}")
        print(f"  reason: {validation.reason}")


if __name__ == "__main__":
    main()
