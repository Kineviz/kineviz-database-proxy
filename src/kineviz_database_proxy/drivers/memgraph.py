# -*- coding: utf-8 -*-
"""
Memgraph driver.

Memgraph speaks Neo4j's bolt protocol and Neo4j's Cypher, so this is a true
subclass rather than a second implementation: the connection, the query path and
the record mapping are all inherited unchanged. Three things differ, and each one
is a bug that was found rather than a preference:

  - **No multi-database concept.** Passing a database name to a session is an error
    there, so ``database`` is pinned to None.
  - **Its own schema surface.** ``db.schema.visualization()`` does not exist —
    Memgraph 3.x answers "There is no procedure named 'db.schema.visualization'" —
    and neither does ``db.labels()`` or ``SHOW LABELS``. Reusing Neo4j's probe made
    the very first statement throw and left every project with no categories at
    all, so ``bolt_schema.load_memgraph_schema`` is used instead.
  - **No Neo4j-style full-text index.** ``SHOW INDEXES yield ...`` is a parse error
    ("mismatched input 'yield'"): Memgraph spells index listing ``SHOW INDEX INFO``
    and puts text search in a separate experimental module (``CREATE TEXT INDEX`` /
    ``text_search.search``, behind ``--experimental-enabled=text-search``). Declaring
    search unsupported in ``MEMGRAPH_CAPABILITIES`` is what stops the inherited
    Neo4j implementation from ever being called.
"""

from __future__ import annotations

from typing import Optional

from .bolt_schema import load_memgraph_schema
from .graph_support import MemgraphGraphIntents
from .neo4j import Neo4jDriver


class MemgraphDriver(MemgraphGraphIntents, Neo4jDriver):
    """Memgraph driver — Neo4j's, with its own schema probe."""

    schema_loader = staticmethod(load_memgraph_schema)

    database_type = "memgraph"

    @property
    def database(self) -> Optional[str]:
        """Memgraph has no multi-database concept; a session names no database."""
        return None
