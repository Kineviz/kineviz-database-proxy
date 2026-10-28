# -*- coding: utf-8 -*-
"""
The Cypher-to-SQL stages, reached through one façade.

``plan``/``expression``/``compile``/``convert``/``ids`` are a separate lane's work and
this module is the only place that names their symbols. Two reasons, both load-bearing:

* **a proxy that cannot compile SQL must still start.** ``drivers/factory.py`` imports
  every driver, and every router imports the factory, so an eager import here would
  take the whole application down with it. A relational project whose compile stage is
  missing can still answer ``/capabilities``, ``/graphSchema`` and its mapping — all of
  which the UI needs *before* anyone runs a query.
* **a rename stays in one file.** Every assumed name is listed on the methods below.
  When the lane settles on something else, this is where it is reconciled.

``expression.py`` is deliberately absent: ``compile_statements`` already asks it for the
value lists too big for one statement and returns one compiled statement per chunk, so
the driver never holds a predicate AST and never re-derives a fan-out.
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

_cache: Dict[str, Any] = {}


class PipelineMissing(RuntimeError):
    """
    A stage is not importable.

    ``str`` of it is what a query response carries: which stage, and that a query cannot
    be compiled until it is there. The intent routes have no exception wrapper, so this
    is raised for the driver to turn into ``success=false``, never to reach a client.
    """


def _module(name: str) -> Any:
    """One stage module, imported on first use and memoised after."""
    found = _cache.get(name)
    if found is not None:
        return found
    try:
        found = importlib.import_module(".".join((__package__ or "", name)))
    except ImportError as exc:
        raise PipelineMissing(
            "the relational {} stage is not available ({}), so a query cannot be "
            "compiled to SQL yet".format(name, exc)
        ) from exc
    _cache[name] = found
    return found


def _reset_cache() -> None:
    """Forget the memoised stages. Test seam only."""
    _cache.clear()


class SqlPipeline:
    """
    The stages, one method per assumed name.

    =======================  ==========================================================
    ``branches``             ``plan.concrete_plans_for(plan, mapping, max_branches=)``
                             -> ``List[ConcretePlan]``
    ``statements``           ``compile.compile_statements(plan, nodes, edges,
                             spelling=, server=, spec=)`` -> ``List[CompiledStatement]``
                             each carrying ``.select``, ``.parameters``, ``.manifest``,
                             ``.kind``, ``.columns``, ``.limit``
    ``graph``                ``convert.convert_rows(rows, manifest)`` -> an object with
                             ``.graph`` and ``.summary``
    ``table``                ``convert.rows_to_table(rows, columns)`` -> the 2D array
    ``json_safe``            ``convert.json_safe(value)``
    ``mint`` / ``decode``    ``ids.mint(label, key, key_type)`` /
                             ``ids.decode(node_id)``
    =======================  ==========================================================

    A statement's ``.select`` is the executable; ``.parameters`` holds its bound values,
    and a caller that merges request parameters into them does so *last*, because a name
    collision is the client overriding a compiled literal.
    """

    def branches(self, plan: Any, mapping: Any, max_branches: int) -> List[Any]:
        stage = _module("plan")
        return list(stage.concrete_plans_for(plan, mapping, max_branches=max_branches))

    def default_max_branches(self) -> int:
        """
        The branch ceiling this lane stands behind, so the driver does not invent one.

        Raising it is a ``tuning`` decision a person can make per project; inventing a
        second default here would quietly widen the ceiling the planner's own message
        promises.
        """
        return int(getattr(_module("plan"), "DEFAULT_MAX_BRANCHES", 10))

    def statements(
        self,
        branch: Any,
        mapping: Any,
        spelling: Any,
        server: Any,
        spec: Any,
    ) -> List[Any]:
        stage = _module("compile")
        return list(
            stage.compile_statements(
                branch,
                mapping.nodes,
                mapping.edges,
                spelling=spelling,
                server=server,
                spec=spec,
            )
        )

    def graph(self, rows: Sequence[Mapping[str, object]], manifest: Any) -> Any:
        return _module("convert").convert_rows(rows, manifest)

    def table(self, rows: Sequence[Mapping[str, object]], columns: Sequence[str]) -> List[List[Any]]:
        return _module("convert").rows_to_table(rows, columns)

    def json_safe(self, value: object) -> Any:
        return _module("convert").json_safe(value)

    def mint(self, label: str, key: object, key_type: Optional[str] = None) -> Optional[str]:
        return _module("ids").mint(label, key, key_type)

    def decode(self, node_id: object) -> Optional[Tuple[str, str]]:
        return _module("ids").decode(node_id)

    def planning_error(self) -> type:
        """The base of the planner's refusals, so the driver can name it without importing it."""
        return _module("plan").PlanningError


PIPELINE = SqlPipeline()
