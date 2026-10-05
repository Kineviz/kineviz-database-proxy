"""
The mapping file: text in, document out, and a document back out as text.

YAML is the human format and it parses JSON, so ``.yml``, ``.yaml`` and ``.json``
all take one path through ``parse``. The browser never parses or emits YAML — the
file's text crosses the wire and this module is the only thing that reads it, so
there is no second authority on the format to keep in step.

Every problem this reports carries the path of the control that is wrong
(``edges[1].endpoints[0].dst.label``), never a bare "invalid mapping": an upload
that fails has to light up the field, not the page.

``to_yaml`` writes no comments. Comments do not survive a regenerate, which is
exactly why the reasoning a person records lives in ``notes`` and ``show_as``. The
shipped example is the authority for the file's *shape*; this serializer is the
authority for what an export looks like.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Sequence, Tuple, Union

import yaml
from pydantic import ValidationError

from ...models.graph_mapping import GraphMapping

#: A mapping is a description of a schema, not a schema dump. Past this it is a
#: person having pasted a database into the wrong box, and saying so is kinder than
#: parsing 40 MB of YAML.
MAX_MAPPING_BYTES = 1024 * 1024

#: Path used for a problem with the file as a whole, which no control can highlight.
FILE_PATH = "file"

#: The annotated example a person downloads and edits. Shipped inside the package;
#: ``GET /api/relational/mapping/example`` serves it byte-for-byte.
EXAMPLE_PATH = Path(__file__).resolve().parent / "data" / "mapping.example.yml"


class MappingFileError(ValueError):
    """
    A mapping file that will not load.

    ``problems`` is ``(path, message)`` per problem so the UI can highlight the
    control each one names, and everything is collected in one pass rather than
    raised on the first: a person fixing a file wants the whole list.
    """

    def __init__(self, problems: Sequence[Tuple[str, str]]) -> None:
        self.problems: List[Tuple[str, str]] = list(problems)
        super().__init__("; ".join("{}: {}".format(path, text) for path, text in self.problems))

    @classmethod
    def at(cls, path: str, message: str) -> "MappingFileError":
        """The common case: one problem, named by path."""
        return cls([(path, message)])


def parse(text: str) -> GraphMapping:
    """
    Read mapping text — YAML or JSON — into a document.

    Raises ``MappingFileError`` with every problem found, each named by path.
    """
    raw = _load_text(text)
    if raw is None:
        raise MappingFileError.at(FILE_PATH, "the file has no mapping in it")
    if not isinstance(raw, dict):
        raise MappingFileError.at(
            FILE_PATH,
            "the top level must be a mapping (version, nodes, edges), not a {}".format(
                type(raw).__name__
            ),
        )
    try:
        return GraphMapping.model_validate(raw)
    except ValidationError as exc:
        raise MappingFileError(_validation_problems(exc)) from exc


def to_yaml(doc: GraphMapping) -> str:
    """
    Write the document as YAML: stable key order, no timestamps, no anchors.

    Two saves of the same document are byte-identical, which is what lets the YAML
    tab show a live view of the draft without the diff flickering.
    """
    data = doc.model_dump(mode="json", by_alias=True, exclude_none=True)
    return yaml.dump(
        data,
        Dumper=_StableDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        indent=2,
        width=1_000_000,
    )


# --------------------------------------------------------------------- internals


def _load_text(text: str) -> Any:
    """Size-capped, UTF-8-checked ``yaml.safe_load`` of mapping text."""
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise MappingFileError.at(
            FILE_PATH, "the file is not UTF-8 text ({})".format(exc.reason)
        ) from exc
    if size > MAX_MAPPING_BYTES:
        raise MappingFileError.at(
            FILE_PATH,
            "the mapping is {} bytes and the limit is {}; a file this large is a "
            "database dump, not a mapping — narrow it with include/exclude".format(
                size, MAX_MAPPING_BYTES
            ),
        )
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise MappingFileError(_syntax_problems(exc)) from exc


def _syntax_problems(exc: yaml.YAMLError) -> List[Tuple[str, str]]:
    """Name the line a YAML error happened on, since no field path exists yet."""
    message = str(getattr(exc, "problem", "") or "").strip() or str(exc).strip()
    if "\\t" in message or "'\t'" in message:
        # PyYAML quotes the offending character as an escape, which is not what a person
        # reading the page needs: the fix is spaces, and saying `\t` does not say that.
        message = "indentation uses a tab; YAML indents with spaces"
    mark = getattr(exc, "problem_mark", None)
    line = getattr(mark, "line", None)
    if line is None:
        return [(FILE_PATH, message)]
    return [("line {}".format(line + 1), message)]


def _validation_problems(exc: ValidationError) -> List[Tuple[str, str]]:
    """Turn pydantic errors into ``(control path, message)`` pairs."""
    return [(_control_path(err["loc"]), _message(err)) for err in exc.errors()]


def _control_path(loc: Sequence[Union[str, int]]) -> str:
    """``("edges", 1, "endpoints", 0, "dst", "label")`` -> ``edges[1].endpoints[0].dst.label``."""
    path = ""
    for part in loc:
        if isinstance(part, int):
            path = "{}[{}]".format(path or "item", part)
        else:
            path = "{}.{}".format(path, part) if path else str(part)
    return path


def _message(err: dict) -> str:
    """A pydantic message, rewritten where pydantic's own wording cannot name the fix."""
    kind = str(err.get("type", ""))
    if kind == "extra_forbidden":
        return "not a field of the mapping document (a typo is refused, not ignored)"
    return str(err.get("msg", "invalid value"))


class _StableDumper(yaml.SafeDumper):
    """SafeDumper that never shares a node, so an export has no anchors or aliases."""

    def ignore_aliases(self, data: Any) -> bool:  # type: ignore[override]
        return True
