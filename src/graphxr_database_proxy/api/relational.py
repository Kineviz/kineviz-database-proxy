# -*- coding: utf-8 -*-
"""
The mapping page's endpoints: a project's graph mapping, its tables, and a link's direction.

These are management endpoints behind the admin token — they read a customer schema and write
a project's configuration — and their bodies are **unwrapped JSON objects**, not the
``{success, data}`` the generic ``/api/{database_type}`` routes return. A management surface
that answers ``200`` with ``success: false`` makes a client check two things where one is
enough: the status code already carries the verdict, so the body carries the document.

Every failure is a 4xx naming the problem. A missing DBAPI, a database that will not answer
and a mapping that will not parse are all things a person can act on from a sentence, and a
500 in the middle of an editor loses the draft they were fixing.

The prefix is literal and this router is included **before** ``database_router``: the generic
``/api/{database_type}/{project_name}`` route would otherwise swallow ``/api/relational/...``.
That is the same reason ``embedded_router`` sits where it does in ``main.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

import yaml
from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, ValidationError

from ..drivers.factory import DriverFactory
from ..drivers.relational import mapping_io
from ..drivers.relational.driver import RelationalDriver
from ..drivers.relational.mapping import from_document
from ..drivers.relational.pool import DEFAULT_NAME_PAGE, MAX_NAME_PAGE, POOL
from ..drivers.relational.reads import PREVIEW_ROWS_MAX, RelationalProblem
from ..models.graph_mapping import GraphMapping
from ..models.project import DatabaseConfig, Project, ProjectUpdate
from ..services.graph_schema_cache import GRAPH_SCHEMA_CACHE
from ..services.project_service import ProjectService
from .auth import verify_admin_token

router = APIRouter(prefix="/api/relational", tags=["relational"])

#: The three backends this surface serves. A project of any other type is refused by name,
#: because a mapping written against a Neo4j database is not a thing that can be edited here.
_RELATIONAL = ("mssql", "postgresql", "mysql", "snowflake")


def get_project_service() -> ProjectService:
    """Injectable the same way the project and database routers make it."""
    return ProjectService()


# ---------------------------------------------------------------------------
# shapes
# ---------------------------------------------------------------------------


class MappingBody(BaseModel):
    """
    A mapping, as the editor sends it: the object it edited, or the text it edited.

    Exactly one document. ``model`` is what a form holds; the text fields carry a file.
    It is parsed by ``mapping_io`` so the text path and the upload path get one
    set of rules and one set of paths in the errors.
    """

    model: Optional[Dict[str, Any]] = Field(
        None, description="The mapping document, as the editor's state"
    )
    yaml: Optional[str] = Field(None, description="The mapping document, as YAML or JSON text")
    text: Optional[str] = Field(None, description="The mapping document, as JSON or YAML text")
    json_text: Optional[Union[str, Dict[str, Any]]] = Field(
        None, alias="json", description="The mapping document, as JSON text or an object"
    )


class PreviewBody(BaseModel):
    """
    One link to check against real data.

    ``table`` and the two ``column`` names are what the join actually uses; ``edge`` and the
    endpoint labels are for naming the answer and resolving a record's table when the draft
    has not saved them. The editor holds the pair it is checking, so it sends the two ends;
    ``pair`` is the index it holds them at, and an object form is accepted for a caller that
    has the ends and no index.
    """

    edge: str = Field("", description="The relationship type being checked, for the answer's name")
    table: str = Field(..., description="The table holding this link's rows")
    pair: Union[int, Dict[str, Any]] = Field(
        ..., description="The endpoint pair, as the index in the editor or as {'src': …, 'dst': …}"
    )
    src: Optional[Dict[str, Any]] = Field(None, description="The near end, when the pair came by index")
    dst: Optional[Dict[str, Any]] = Field(None, description="The far end, when the pair came by index")
    limit: int = Field(20, description="Rows per direction, capped at {}".format(PREVIEW_ROWS_MAX))


class ConnectionBody(BaseModel):
    """
    A connection that has not been saved yet.

    The picker has to work before a password has been typed: a person choosing which tables
    are their graph is looking at a database they have not committed to the proxy, and a page
    that only shows tables for a project that already exists makes them save a connection they
    may still abandon. So the same read is offered against the draft in the body.

    It is a body and never a query string: this is where a password arrives, and a URL ends up
    in an access log.
    """

    project: str = Field("", description="A name for the draft, so the answer says what it is about")
    connection: Dict[str, Any] = Field(..., description="A database config, as the form holds it")
    schema_name: Optional[str] = Field(None, alias="schema", description="Read this schema")
    page: int = Field(1, ge=1, description="1-based page of table names")
    page_size: Optional[int] = Field(None, ge=1, description="Names on the page")
    include: Optional[str] = Field(None, description="'columns' for the columns, not the count")
    table: Optional[str] = Field(None, description="One table, in full")
    search: Optional[str] = Field(None, description="Names containing this, case-insensitively")

    class Config:
        populate_by_name = True


class DiscoverBody(BaseModel):
    """Optional overrides for one discovery walk — a page reading a schema the project has not saved."""

    schema_name: Optional[str] = Field(None, alias="schema", description="Read this schema, not the project's")
    connection: Optional[Dict[str, Any]] = Field(
        None, description="A draft config, for a project that has not been saved yet"
    )

    class Config:
        populate_by_name = True


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _problem(exc: Exception) -> HTTPException:
    """
    Any failure this surface can explain, as a 4xx that says what to do.

    A missing DBAPI keeps the engine's own install sentence; a request that named nothing
    keeps its path. Neither becomes a 500, because the person on the other end of one of
    these has a draft open and a 500 gives them no reason to keep it.
    """
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, RelationalProblem):
        return HTTPException(status_code=422, detail=exc.problems)
    if isinstance(exc, mapping_io.MappingFileError):
        return HTTPException(
            status_code=422,
            detail=[{"path": path, "message": text} for path, text in exc.problems],
        )
    return HTTPException(status_code=400, detail=str(exc) or type(exc).__name__)


async def _project(service: ProjectService, name: str) -> Project:
    """The project, or a 404 naming what was asked for."""
    found = await service.get_project_by_name(name)
    if not found:
        raise HTTPException(status_code=404, detail="project {} does not exist".format(name))
    return found


def _relational_driver(project: Project) -> RelationalDriver:
    """
    A driver for a relational project, or a refusal that names the type it found.

    The factory is the only thing that knows which driver a type maps to; checking the type
    first keeps a ``mongodb`` project from arriving here and being served a relational
    mapping it cannot answer.
    """
    backend = str(getattr(project.database_config.type, "value", project.database_config.type))
    if backend not in _RELATIONAL:
        raise HTTPException(
            status_code=400,
            detail="project {} is a {} project; /api/relational serves {}".format(
                project.name, backend, ", ".join(_RELATIONAL)
            ),
        )
    driver = DriverFactory.create_driver(project)
    if not isinstance(driver, RelationalDriver):  # a registration that points somewhere else
        raise HTTPException(
            status_code=500,
            detail="{} projects are not served by a relational driver".format(backend),
        )
    return driver


async def _driver(
    service: ProjectService, name: str, connection: Optional[Dict[str, Any]] = None
) -> RelationalDriver:
    """
    A driver for a saved project, or for the draft one a page has not committed yet.

    A draft is given an id of its own so the pool never mistakes it for the saved project of
    the same name — and two different drafts cannot share an engine either, because the key is
    a digest of the whole config and a different password is a different digest.
    """
    if connection:
        try:
            draft = DatabaseConfig.model_validate(connection)
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=[
                    {"path": ".".join(str(part) for part in item["loc"]), "message": item["msg"]}
                    for item in exc.errors()
                ],
            ) from exc
        project = Project(
            id="draft:{}".format(name), name=name, database_type=draft.type, database_config=draft
        )
        return _relational_driver(project)
    target = await _project(service, name)
    return _relational_driver(target)


def _document_from(body: MappingBody) -> GraphMapping:
    """
    A request body into a validated document.

    The ``model`` half is dumped to text and parsed, rather than validated straight into the
    model, so both halves of this endpoint share ``mapping_io.parse``: one size cap, one set
    of path-named problems, one rule about what an unknown key means. The round trip costs a
    YAML dump and buys the same error a file upload would have produced.
    """
    try:
        supplied = [value for value in (body.model, body.yaml, body.text, body.json_text) if value is not None]
        if not supplied:
            raise mapping_io.MappingFileError.at(
                "file", "send the mapping as 'model', 'yaml', 'json' or 'text'"
            )
        if len(supplied) != 1:
            raise mapping_io.MappingFileError.at("file", "send exactly one mapping document")
        value = supplied[0]
        return mapping_io.parse(value if isinstance(value, str) else yaml.safe_dump(value, sort_keys=False))
    except mapping_io.MappingFileError as exc:
        raise _problem(exc) from exc


def _mapping_answer(project: Project, document: Optional[GraphMapping]) -> Dict[str, Any]:
    """
    One document, as the page wants it: ``{"valid", "problems", "text", "model", "provenance"}``.

    ``model`` is the document the editor holds and ``text`` is the same thing as a file, so
    the form and the YAML tab are two views of one answer rather than two answers. The
    document reaching this function has already parsed; what is reported here is whether it
    *loads* — an endpoint naming a label nothing declares, a composite key — and those
    problems carry the path of the control to fix.

    Problems are reported and never refused at save time: a record with no ``key`` is a legal
    thing to save and discovery fills it in, so refusing the save would make the draft
    impossible to keep while still being wrong.
    """
    effective = from_document(document) if document is not None else None
    problems = (
        [{"path": path, "message": text} for path, text in effective.problems]
        if effective is not None
        else []
    )
    return {
        "project": project.name,
        "id": project.id,
        "backend": str(getattr(project.database_config.type, "value", project.database_config.type)),
        "source": "override" if document is not None else "none",
        "valid": bool(document is not None and not problems),
        "problems": problems,
        "warnings": list(effective.warnings) if effective is not None else [],
        "provenance": dict(effective.provenance) if effective is not None else {},
        "model": document.model_dump(by_alias=True) if document is not None else None,
        "text": mapping_io.to_yaml(document) if document is not None else None,
        "json": mapping_io.to_json(document) if document is not None else None,
    }


def _invalidate(project: Project) -> None:
    """
    Drop everything that was derived from this project's old configuration.

    The cached graph schema is the one that matters for correctness: a saved mapping changes
    what a seed id resolves to, and a stale cache would keep answering with the labels that
    were just replaced. The engine is keyed by a digest of the config, so it would dispose
    itself on the next request — but a pool open against a database whose mapping just
    changed has no reason to stay open until then.
    """
    GRAPH_SCHEMA_CACHE.invalidate_project(project.id)
    POOL.dispose_project(project.id)


def _page(value: Optional[int], default: int, ceiling: int) -> int:
    try:
        wanted = int(value) if value is not None else default
    except (TypeError, ValueError):
        wanted = default
    return max(1, min(wanted, ceiling))


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------


@router.get("/mapping/example")
async def mapping_example(_: Optional[str] = Depends(verify_admin_token)) -> Dict[str, Any]:
    """
    The annotated example, byte for byte, as text.

    Served from inside the package rather than from a fixture: it is the authority for the
    file's shape, and a page that paraphrases it teaches a different format from the one the
    parser accepts.
    """
    try:
        text = mapping_io.EXAMPLE_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(
            status_code=404, detail="the shipped mapping example is not readable: {}".format(exc)
        ) from exc
    return {"name": "mapping.example.yml", "text": text}


@router.get("/{project_name}/mapping")
async def get_mapping(
    project_name: str = Path(..., description="Project name"),
    output: Optional[str] = Query(None, alias="format", description="'json' or 'yaml' for the file itself"),
    service: ProjectService = Depends(get_project_service),
    _: Optional[str] = Depends(verify_admin_token),
) -> Any:
    """
    The mapping this project has saved, as a document or as a file.

    Reaches no database: what a person saved is answerable without asking the customer's
    server, and a page that cannot load because the host is down is worse than one that says
    the draft is not there yet. ``POST /discover`` is the route that proposes one.
    """
    project = await _project(service, project_name)
    driver = _relational_driver(project)
    document = driver.override_document()
    if output not in (None, "yaml", "json"):
        raise HTTPException(status_code=422, detail="format must be json or yaml")
    if output in ("yaml", "json"):
        if document is None:
            raise HTTPException(
                status_code=404,
                detail="project {} has no saved mapping; POST /api/relational/{}/discover "
                "and save what it proposes".format(project_name, project_name),
            )
        return PlainTextResponse(
            mapping_io.to_json(document) if output == "json" else mapping_io.to_yaml(document),
            media_type="application/json" if output == "json" else "text/yaml",
            headers={
                "Content-Disposition": 'attachment; filename="{}.mapping.{}"'.format(
                    project_name, "json" if output == "json" else "yml"
                )
            },
        )
    return _mapping_answer(project, document)


@router.put("/{project_name}/mapping")
async def put_mapping(
    project_name: str = Path(..., description="Project name"),
    body: Optional[MappingBody] = Body(
        None, description="{'model': …}, {'yaml': …}, or null to remove the override"
    ),
    service: ProjectService = Depends(get_project_service),
    _: Optional[str] = Depends(verify_admin_token),
) -> Dict[str, Any]:
    """
    Save a mapping, or take the override away.

    A null body deletes it, which returns the project to discovery — the honest inverse of
    saving one, and the only way a person gets their original answer back.
    """
    project = await _project(service, project_name)
    driver = _relational_driver(project)
    if body is None:
        document = None
    else:
        document = _document_from(body)
    config = project.database_config.model_copy(update={"graph_mapping": document.model_dump(by_alias=True) if document else None})
    try:
        saved = await service.update_project(
            project.id, ProjectUpdate(database_config=config)
        )
    except Exception as exc:
        raise _problem(exc) from exc
    if saved is None:
        raise HTTPException(status_code=404, detail="project {} vanished".format(project_name))
    _invalidate(saved)
    answer = _mapping_answer(saved, document)
    answer["saved"] = True
    answer["removed"] = document is None
    return answer


@router.post("/{project_name}/validate")
async def validate_mapping(
    project_name: str = Path(..., description="Project name"),
    body: MappingBody = Body(..., description="The draft to check"),
    service: ProjectService = Depends(get_project_service),
    _: Optional[str] = Depends(verify_admin_token),
) -> Dict[str, Any]:
    """
    Check a draft without saving it, and without touching the customer's database.

    The shape problems come from ``mapping_io.parse`` with the control's path, the semantic
    ones from ``from_document`` — so a file that parses and still cannot load is reported as
    the list of paths that need fixing rather than as a mapping that will fail later.

    The answer carries the document itself, not only its rendering: an upload or a paste is
    applied to the editor's structured tabs from this reply, so a page that gets text back
    and nothing else replaces forty rows with none. ``GET /mapping`` and the ``PUT`` answer
    it the same way, and one caller reading two shapes is how a silently emptied editor
    ships.

    It carries this document's provenance for the same reason. The check is the route a page
    installs a document *from*, and the tags are computed off the document by the pass this
    route already runs — so a reply that handed back the rows but not the word for who chose
    each one would leave the editor showing the previous document's badges beside the new
    document's rows. A check that finds problems answers the tags it could resolve, same as
    its ``problems`` list: reported, and the page decides what to keep.
    """
    await _driver(service, project_name)
    document = _document_from(body)
    effective = from_document(document)
    dumped = document.model_dump(by_alias=True)
    return {
        "valid": not effective.problems,
        "problems": [{"path": path, "message": text} for path, text in effective.problems],
        "warnings": list(effective.warnings),
        "provenance": dict(effective.provenance),
        "nodes": len(dumped.get("nodes") or []),
        "edges": len(dumped.get("edges") or []),
        "model": dumped,
        "text": mapping_io.to_yaml(document),
        "json": mapping_io.to_json(document),
    }


@router.post("/{project_name}/discover")
async def discover_mapping(
    project_name: str = Path(..., description="Project name"),
    body: Optional[DiscoverBody] = Body(None, description="Optional schema and page overrides"),
    service: ProjectService = Depends(get_project_service),
    _: Optional[str] = Depends(verify_admin_token),
) -> Dict[str, Any]:
    """
    Read the live schema and propose a mapping.

    Nothing is saved: a proposal the person has not looked at is not a mapping, and the page
    PUTs it when they accept it. The walk is capped and paged by ``catalog``, and the answer
    carries the caps and skips so "three hundred tables became forty labels" has a reason on
    the screen.

    No provenance, and that is the decision rather than an oversight. Every entry here is
    ``propose``'s own, so the loader could only ever answer one word for all of them, and
    ``source: "draft"`` already says that word about the whole document; a tag per row would
    be a second, weaker place to read it. The page therefore shows no badges for a proposal —
    ``POST …/validate`` is where a document arrives with tags, and a save is where the stored
    one does.
    """
    driver = await _driver(
        service,
        project_name,
        body.connection if body is not None else None,
    )
    wanted = (body.schema_name if body is not None else None) or None
    try:
        found = await driver.discover(schema=wanted)
    except Exception as exc:
        raise _problem(exc) from exc
    return {"project": project_name, **found}


@router.get("/{project_name}/tables")
async def list_tables(
    project_name: str = Path(..., description="Project name"),
    page: int = Query(1, ge=1, description="1-based page of table names"),
    page_size: Optional[int] = Query(None, ge=1, description="Names on the page"),
    schema: Optional[str] = Query(None, description="This schema, not the project's"),
    include: Optional[str] = Query(None, description="'columns' for the columns, not the count"),
    table: Optional[str] = Query(None, description="One table, in full"),
    search: Optional[str] = Query(None, description="Names containing this, case-insensitively"),
    service: ProjectService = Depends(get_project_service),
    _: Optional[str] = Depends(verify_admin_token),
) -> Dict[str, Any]:
    """
    The tables of a saved project's schema, a page of names at a time.

    ``?include=columns&table=Order`` asks one table for its columns and its key candidates,
    which is what the pickers read; with no ``table`` the same ``include`` adds the columns to
    every row of the page. A connection that has not been saved yet is the same read one
    method over: ``POST`` this path with it in the body.
    """
    driver = await _driver(service, project_name)
    try:
        return await _tables_answer(
            driver, _Page(page=page, page_size=page_size, search=search), include, table, schema
        )
    except Exception as exc:
        raise _problem(exc) from exc


@router.post("/{project_name}/tables")
async def list_draft_tables(
    body: ConnectionBody,
    project_name: str = Path(..., description="Draft name"),
    service: ProjectService = Depends(get_project_service),
    _: Optional[str] = Depends(verify_admin_token),
) -> Dict[str, Any]:
    """
    The picker's read against a connection that has not been saved.

    A person choosing which tables are their graph is looking at a database they have not
    committed to the proxy, and a page that only lists tables for a project that already
    exists makes them save a connection they may still abandon. The credentials are in a body
    and never in a query string, because a URL ends up in an access log and a body does not.
    """
    driver = await _driver(service, project_name, body.connection)
    try:
        return await _tables_answer(
            driver,
            _Page(page=body.page, page_size=body.page_size, search=body.search),
            body.include,
            body.table,
            body.schema_name,
        )
    except Exception as exc:
        raise _problem(exc) from exc


@router.post("/{project_name}/preview")
async def preview_link(
    project_name: str = Path(..., description="Project name"),
    body: PreviewBody = Body(..., description="The link to run both ways"),
    service: ProjectService = Depends(get_project_service),
    _: Optional[str] = Depends(verify_admin_token),
) -> Dict[str, Any]:
    """
    One link's join, run in both directions against real data.

    The answer is the closed set ``settled | backwards | you-decide | neither``, and an empty
    result is ``neither`` — never ``settled``. A link whose ends are swapped answers nothing
    rather than an error, which is precisely why this route exists: the rows are the only
    thing that can say which way round the key columns point.
    """
    driver = await _driver(service, project_name)
    try:
        answer = await driver.preview(
            body.table, _preview_pair(body), edge=body.edge, limit=body.limit
        )
        if isinstance(body.pair, int):
            answer["pair"] = body.pair
        return answer
    except Exception as exc:
        raise _problem(exc) from exc


def _preview_pair(body: PreviewBody) -> Dict[str, Any]:
    """
    The two ends, whichever shape the caller sent them in.

    A pair *index* is the editor's address for a pair it already holds, and means nothing to a
    join until the two columns come with it, so a request that names none is refused with the
    path of the control that was left empty rather than run against a guess.
    """
    if isinstance(body.pair, dict) and body.pair:
        return body.pair
    if body.src and body.dst:
        return {"src": body.src, "dst": body.dst}
    raise RelationalProblem(
        "pair",
        "send the two ends as 'src' and 'dst', or the pair itself as {'src': …, 'dst': …} — "
        "a direction check joins on two named columns and a pair number alone does not carry them",
    )


# ---------------------------------------------------------------------------
# the table list
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Page:
    """What one read of the picker asked for, so the GET and the POST share the walk."""

    page: int = 1
    page_size: Optional[int] = None
    search: Optional[str] = None

    @property
    def size(self) -> int:
        return _page(self.page_size, DEFAULT_NAME_PAGE, MAX_NAME_PAGE)

    @property
    def number(self) -> int:
        return max(1, int(self.page))

    def narrowed(self, names: List[str]) -> List[str]:
        """
        The names this page is about, before anything is reflected.

        A search filters names and nothing else, so the reflection that follows is still one
        page: a picker that pulled the whole library down to filter it in the browser is the
        mistake the paging rule exists to stop.
        """
        if not self.search:
            return names
        needle = str(self.search).casefold()
        return [name for name in names if needle in name.casefold()]

    def window(self, names: List[str]) -> List[str]:
        """The slice of the narrowed list this page shows."""
        start = (self.number - 1) * self.size
        return names[start : start + self.size]


async def _tables_answer(
    driver: RelationalDriver,
    page: _Page,
    include: Optional[str],
    table: Optional[str],
    schema: Optional[str],
) -> Dict[str, Any]:
    """
    The page, or one table in full — answered the same way for a saved and a draft connection.

    Paging is on *names*, and a page of names is the unit of reflection: asking for twenty
    tables costs twenty tables' worth of catalog reads, never the library's. ``total`` counts
    the names that match — all the visible ones, or the ones a search left — which is one
    catalog query an inspector cannot offset: the only whole read left in the walk. With
    ``table=`` and ``include=columns`` it counts *columns* instead, because that read is the
    column picker paging a wide table.
    """
    wanted_schema = schema or driver.access.schema
    if table:
        found = await driver.access.catalog(names=[table], page_size=1, schema=schema)
        entry = found.by_name.get(table)
        # The catalog only holds names the engine answered for, so a caller that invented
        # one gets no entry: ``by_name`` says whether this schema has that table.
        if entry is None:
            raise HTTPException(
                status_code=404,
                detail="{} is not a table in {} — a view is not, and neither is anything the "
                "engine keeps for itself".format(table, wanted_schema or "this schema"),
            )
        if include == "columns":
            return _one_table(entry, page)
        return {
            "rows": [_table_row(entry, columns=include == "columns")],
            "total": 1,
            "page": 1,
            "page_size": 1,
        }
    page_reader = getattr(driver.access, "names_page", None)
    source_page = await page_reader(
        schema=schema, offset=(page.number - 1) * page.size, limit=page.size, search=page.search
    ) if page_reader else None
    if source_page is not None:
        page_names, total = source_page
    else:
        names = page.narrowed(await driver.access.names(schema=schema))
        page_names, total = page.window(names), len(names)
    if not page_names:
        return {"rows": [], "total": total, "page": page.number, "page_size": page.size}
    found = await driver.access.catalog(
        names=page_names, page_size=len(page_names), schema=schema
    )
    read = found.by_name
    return {
        "rows": [
            _table_row(read[name], columns=include == "columns")
            if name in read
            else {
                "table": name,
                "schema": wanted_schema,
                "columns": None,
                "row_estimate": None,
            }
            for name in page_names
        ],
        "total": total,
        "page": page.number,
        "page_size": page.size,
    }


def _table_row(entry: Any, columns: bool = False) -> Dict[str, Any]:
    """
    A table as the picker shows it: its name, where it lives, how wide and how full.

    ``columns`` swaps the count for the columns themselves, which is what a page that lists
    and a page that lets someone pick from the same list need from one read. Whether the table
    *can* be a node is not repeated here as a flag: it is ``key_candidates``, and a table with
    none is refused by the save with the catalog's reason, not by a checkbox.
    """
    return {
        "table": entry.name,
        "schema": entry.schema,
        "columns": _column_list(entry) if columns else len(entry.columns),
        "row_estimate": entry.row_estimate,
    }


def _one_table(entry: Any, page: _Page) -> Dict[str, Any]:
    """
    One table's columns, a page at a time, with the key candidates beside them.

    The candidates are for the whole table and not for the page, because they are the
    catalog's judgement over every column it read: a picker offered only the first 200
    columns of a 400-column table would hide the key that sits past them.
    """
    names = list(entry.columns)
    window = names[(page.number - 1) * page.size : page.number * page.size]
    return {
        "table": entry.name,
        "schema": entry.schema,
        "row_estimate": entry.row_estimate,
        "columns": [_column_row(entry, name) for name in window],
        "key_candidates": _key_candidates(entry),
        "total": len(names),
        "page": page.number,
        "page_size": page.size,
    }


def _column_list(entry: Any) -> List[Dict[str, Any]]:
    return [_column_row(entry, name) for name in entry.columns]


def _column_row(entry: Any, name: str) -> Dict[str, Any]:
    """One column, in the engine's own spelling: this is the relational view, not the graph."""
    return {
        "name": name,
        "type": entry.columns.get(name, ""),
        "nullable": name not in entry.not_null,
        "is_primary_key": name in entry.pk_columns,
        "is_unique": name in entry.uniques,
    }


def _key_candidates(entry: Any) -> List[str]:
    """
    The columns that could key this table, in the order the catalog ranks them.

    The judgement stays ``SchemaTable``'s — a single-column primary key, else a single-column
    NOT NULL unique — because a composite key is refused outright, and a picker that
    re-derived candidates from the per-column flags would quietly offer the first column of a
    composite key and let a person save a mapping the validator will not load.
    """
    chosen = [entry.key_candidate()] if entry.key_candidate() else []
    return chosen + [name for name in entry.uniques if name not in chosen]
