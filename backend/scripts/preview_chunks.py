"""Preview how the knowledge files are chunked, without saving anything. Run from the project root:

    python -m backend.scripts.preview_chunks                     # counts and titles
    python -m backend.scripts.preview_chunks --show schema:billing  # full content of one chunk
"""

import argparse
import sys
from collections import Counter

from backend.app.services.rag.chunker import Chunk, chunk_documents
from backend.app.services.rag.document_loader import load_documents


def print_summary(chunks: list[Chunk]) -> None:
    """Print chunk counts per source and one line per chunk."""
    print(f"Total chunks: {len(chunks)}")
    for source, count in Counter(chunk.source for chunk in chunks).items():
        print(f"  {source}: {count}")
    print()
    width = max(len(chunk.id) for chunk in chunks)
    for chunk in chunks:
        print(f"{chunk.id:<{width}}  {chunk.type:<13}  {len(chunk.content):>5} chars  {chunk.title}")


def print_chunk(chunk: Chunk) -> None:
    """Print one chunk's fields and full content."""
    print(f"id:     {chunk.id}\nsource: {chunk.source}\ntype:   {chunk.type}\ntitle:  {chunk.title}")
    print("content:\n" + chunk.content)


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview MedLens knowledge chunks.")
    parser.add_argument("--show", metavar="ID", help="print the full content of the chunk with this id")
    args = parser.parse_args()

    chunks = chunk_documents(load_documents())
    if args.show is None:
        print_summary(chunks)
        return

    by_id = {chunk.id: chunk for chunk in chunks}
    if args.show not in by_id:
        sys.exit(f"Unknown chunk id {args.show!r}. Run without --show to list ids.")
    print_chunk(by_id[args.show])


if __name__ == "__main__":
    main()
