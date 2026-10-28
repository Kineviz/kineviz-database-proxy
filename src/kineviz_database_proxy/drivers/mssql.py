# -*- coding: utf-8 -*-
"""
MSSQL driver — SQL Server served as a graph.

Thin on purpose, per ``CLAUDE.md``: one engine spelling, one capability record, one
dialect, and all of the traversal and SQL in ``drivers/relational``. A table becomes a
label and a foreign key becomes a link through the mapping document, which this module
never touches.

The engine's own rules — ``ARITHABORT``, the ``AVG`` cast over an integer column,
``OPENJSON`` needing both a 2016 server and a database left at compatibility level 130,
the 2 100-parameter ceiling — are ``engines/mssql.py``'s, and the regex refusal that
follows from T-SQL having no ``=~`` is wired in ``RelationalDriver``, which reads
``supports_regex`` off that spelling.
"""

from __future__ import annotations

from .graph_support import MssqlGraphIntents
from .relational.driver import RelationalDriver


class MssqlDriver(MssqlGraphIntents, RelationalDriver):
    """SQL Server, over the relational pipeline."""

    #: The URL segment, the engine registry key and the dialect name: one word.
    database_type = "mssql"
    engine_name = "mssql"
