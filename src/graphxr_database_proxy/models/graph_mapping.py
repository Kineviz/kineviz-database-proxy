"""
The graph-mapping override document.

This is the shape a person edits and the shape the admin UI reads and writes, so
it has to survive ``model_dump()`` / ``model_validate()`` without loss: every
judgement discovery made is a field here, never a comment. That is what ``notes``
and ``show_as`` are for — the editor regenerates the file from this model, and a
YAML comment is the first thing a save destroys.

The document is stored inside ``DatabaseConfig.graph_mapping``, which is what the
graph-schema cache digest keys on, so saving a mapping invalidates the probe.

Two format decisions worth knowing before you use it:

* **``schema`` is the YAML key, ``schema_name`` is the attribute.** A field called
  ``schema`` would shadow ``BaseModel.schema``, so the alias carries the human
  spelling and the model keeps a usable attribute. ``populate_by_name`` means both
  spellings parse; ``model_dump(by_alias=True)`` emits the short one.
* **Unknown keys are refused, not dropped.** A typo in a hand-written file
  (``exclue:``) would otherwise load as "nothing excluded", which reads back as a
  mapping that silently does nothing. The refusal is reported by path.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .mapping_v2 import canonical_mapping

SCHEMA_ALIAS = "schema"

#: The type recorded for a property nobody could name. ``is_numeric_key_type`` says
#: no to it, so a key of unknown type is quoted rather than pasted bare.
UNKNOWN_TYPE = "STRING"


def _types_from(value: Any) -> Dict[str, str]:
    """
    Accept both spellings of a property list.

    A machine-written document carries ``{name: type}``; a person writing YAML by
    hand naturally writes ``[name, age]``. Both mean the same set of columns, and
    the ones with no stated type get ``STRING``.
    """
    if value is None:
        return {}
    if isinstance(value, dict):
        out: Dict[str, str] = {}
        for name, type_name in value.items():
            text = "" if type_name is None else str(type_name).strip()
            out[str(name)] = text or UNKNOWN_TYPE
        return out
    if isinstance(value, (list, tuple, set)):
        return {str(name): UNKNOWN_TYPE for name in value}
    raise ValueError("properties must be a mapping of column to type, or a list of columns")


def _flat_key(value: Any) -> Optional[str]:
    """
    Keep a composite key readable enough to refuse properly.

    A person writing YAML naturally puts the two key columns in a list. Flattening it
    to ``"a, b"`` means ``mapping.from_document`` can name both columns and say why the
    contract cannot carry them, instead of pydantic answering "not a string" about a
    file that is perfectly clear.
    """
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(part).strip() for part in value if str(part).strip())
    return str(value)


class _DocumentModel(BaseModel):
    """Shared config: alias-aware, strict about unknown keys."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class MappingEndpoint(_DocumentModel):
    """One end of a link, as written in the document."""

    label: str = Field(
        ...,
        description=(
            "Node label this end points at. It must be declared under `nodes`; a typo "
            "here is an edge type that silently never matches anything."
        ),
    )
    column: str = Field(
        ...,
        description=(
            "The column **on the link table** that holds this end's key — not the "
            "node's own key column. The join is `<link>.<column> = <node>.<key>`."
        ),
    )
    column_type: Optional[str] = Field(
        default=None,
        description=(
            "Declared type of that column, carried so validation and export need no "
            "live catalog. Discovery fills it in; a hand-written file may leave it out."
        ),
    )
class MappingPair(_DocumentModel):
    """One ``(src, dst)`` the edge type connects; several of these is a polymorphic link."""

    src: MappingEndpoint = Field(..., description="The end the arrow leaves.")
    dst: MappingEndpoint = Field(..., description="The end the arrow arrives at.")
    row_estimate: Optional[int] = Field(
        default=None,
        description=(
            "Rows discovery saw for this edge's table, or absent when its statistics never "
            "named one — which is not an empty table and must not be written as one. It "
            "picks which pair a schema panel reports for a polymorphic type, so it is kept, "
            "not decorative."
        ),
    )


class MappingNode(_DocumentModel):
    """A table read as a node label."""

    label: str = Field(
        ..., description="The name GraphXR sees. Renaming this rewrites every link that points at it."
    )
    table: str = Field(
        default="", description="Physical table; defaults to the label when left out."
    )
    schema_name: Optional[str] = Field(
        default=None,
        alias=SCHEMA_ALIAS,
        description="Table's schema, when the engine has one. Defaults to the document's.",
    )
    key: Optional[str] = Field(
        default=None,
        description=(
            "The single column that identifies a row. Composite keys cannot be carried "
            "by the graph contract and are refused when the document is loaded, with "
            "the columns named. Empty means discovery must choose one."
        ),
    )
    properties: Dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Columns this label exposes, as ``{column: type}`` or as a bare list. "
            "This is a whitelist: a column that is not here is invisible in GraphXR. "
            "Empty means every column of the table, minus ``exclude_properties``."
        ),
    )
    property_columns: Dict[str, str] = Field(default_factory=dict)
    exclude_properties: List[str] = Field(
        default_factory=list,
        description="Columns to drop from the whitelist — the usual way to leave a blob out.",
    )
    include: bool = Field(
        default=True,
        description=(
            "False keeps the entry as a record of a decision without putting it in the "
            "graph. Discovery uses it to say *why* a table is not a node."
        ),
    )
    show_as: Optional[str] = Field(
        default=None,
        description=(
            "The property a row should be labelled with. Direction previews print "
            "these instead of ids, because `1001 -> 2001` cannot read as wrong while "
            "`Avery Chen -> Northwind Logistics` can."
        ),
    )
    notes: Optional[str] = Field(
        default=None, description="Why this record is a node, and why its key is the one it is."
    )

    @field_validator("properties", mode="before")
    @classmethod
    def _accept_property_lists(cls, value: Any) -> Dict[str, str]:
        return _types_from(value)

    @field_validator("key", mode="before")
    @classmethod
    def _accept_key_lists(cls, value: Any) -> Optional[str]:
        return _flat_key(value)


class MappingEdge(_DocumentModel):
    """A link table or a foreign key read as an edge type."""

    type: str = Field(..., description="The relationship type GraphXR sees.")
    table: str = Field(
        default="", description="Physical table holding the links; defaults to the type."
    )
    schema_name: Optional[str] = Field(
        default=None, alias=SCHEMA_ALIAS, description="Table's schema, when the engine has one."
    )
    key: Optional[str] = Field(
        default=None,
        description=(
            "The edge table's **own** key (its PK, else its widest single-column "
            "unique), so parallel links between the same two records stay distinct. "
            "Left out, discovery supplies the table's candidate; with no candidate the "
            "id is minted from the two endpoint keys sorted and duplicate links "
            "collapse onto one id."
        ),
    )
    flip: bool = Field(
        default=False,
        description=(
            "Read this link the other way round from what discovery proposed. Discovery "
            "follows the foreign key, which is a real direction but not always the one "
            "the sentence in the room uses. True swaps every endpoint pair."
        ),
    )
    properties: Dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Columns of the link table carried as relationship properties. Endpoint "
            "columns and the key are never properties, and discovery leaves them out."
        ),
    )
    property_columns: Dict[str, str] = Field(default_factory=dict)
    exclude_properties: List[str] = Field(default_factory=list, description="Columns to drop.")
    include: bool = Field(
        default=True,
        description="False records that this link is out of the graph, and why.",
    )
    show_as: Optional[str] = Field(
        default=None, description="The property a link should be labelled with."
    )
    notes: Optional[str] = Field(
        default=None,
        description="The reasoning behind the guess, so a person can disagree with a specific one.",
    )
    endpoints: List[MappingPair] = Field(
        default_factory=list,
        description=(
            "Every (src, dst) this type connects. An edge with no endpoints is a "
            "relationship that can never be drawn, which is why loading one is refused."
        ),
    )

    @field_validator("properties", mode="before")
    @classmethod
    def _accept_property_lists(cls, value: Any) -> Dict[str, str]:
        return _types_from(value)

    @field_validator("key", mode="before")
    @classmethod
    def _accept_key_lists(cls, value: Any) -> Optional[str]:
        return _flat_key(value)


class GraphMapping(_DocumentModel):
    """The whole override document: one project's answer to "what is this schema as a graph"."""

    version: int = Field(
        default=1, description="Document format version: proxy v1 or GraphXR v2."
    )
    schema_name: Optional[str] = Field(
        default=None,
        alias=SCHEMA_ALIAS,
        description=(
            "Schema every table below lives in, unless an entry says otherwise. "
            "Engine defaults: dbo (mssql), public (postgresql), PUBLIC (snowflake), the database itself (mysql)."
        ),
    )
    include: List[str] = Field(
        default_factory=list,
        description=(
            "When non-empty, only these records are in the graph; everything else is "
            "out. Names match a label or a table, whichever is written."
        ),
    )
    exclude: List[str] = Field(
        default_factory=list,
        description=(
            "Records out of the graph by name — the short form of an entry with "
            "``include: false``, and what discovery writes for a table it cannot key."
        ),
    )
    nodes: List[MappingNode] = Field(
        default_factory=list, description="Node labels, in the order they were declared."
    )
    edges: List[MappingEdge] = Field(
        default_factory=list, description="Edge types, in the order they were declared."
    )
    tuning: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Per-project overrides the query layer reads: id_list_inline_max, "
            "id_list_chunk_size, max_branches, auto_limit and friends. Unknown keys are "
            "kept for a later build; each one is documented where it is read."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _accept_graphxr_mapping(cls, value: Any) -> Any:
        return canonical_mapping(value)

    @field_validator("version")
    @classmethod
    def _supported_version(cls, value: int) -> int:
        if value not in (1, 2):
            raise ValueError("only versions 1 and 2 are defined")
        return value
