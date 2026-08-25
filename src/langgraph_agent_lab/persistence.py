"""Checkpointer adapter."""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver


def build_checkpointer(
    kind: str = "memory", database_url: str | None = None
) -> BaseCheckpointSaver | None:
    """Return a LangGraph checkpointer.

    SQLite and Postgres are optional extension backends and are not enabled in the core build.
    The starter provides MemorySaver only — SQLite/Postgres are extension tasks.

    For SQLite:
    - pip install langgraph-checkpoint-sqlite
    - Use SqliteSaver with sqlite3.connect() and WAL mode
    - See: https://langchain-ai.github.io/langgraph/how-tos/persistence/
    """
    if kind == "none":
        return None
    if kind == "memory":
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()
    if kind == "sqlite":
        raise RuntimeError(
            "SQLite persistence is an optional extension and is not enabled in this submission. "
            "Use checkpointer: memory for the supported core workflow."
        )
    if kind == "postgres":
        raise RuntimeError(
            "Postgres persistence is an optional extension and is not enabled in this submission. "
            "Use checkpointer: memory for the supported core workflow."
        )
    raise ValueError(f"Unknown checkpointer kind: {kind}")
