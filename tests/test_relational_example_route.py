"""
The route behind the mapping page's Example button, pinned at the HTTP layer.

``projectService.getMappingExample()`` is the only thing in the browser that reads this URL,
and what it can read is fixed by ``MappingTextResponse`` in ``frontend/src/types/project.ts``:
one text field, taken as ``text`` and failing to ``yaml``. Nothing here reaches a database, a
project or a credential — the route answers from one file inside the package and from nothing
else, which is why it is the easiest route in the surface to pin and was the only one pinned
nowhere: ``tests/test_relational_mapping_io.py`` proves the file ships, and this proves the
route is the thing that hands the browser those bytes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kineviz_database_proxy.api.auth import verify_admin_token
from kineviz_database_proxy.drivers.relational import mapping_io
from kineviz_database_proxy.main import app

#: The one URL the Example button asks for.
EXAMPLE_URL = "/api/relational/mapping/example"


@pytest.fixture
def client():
    """The real app with the admin token let through, exactly as the other route files do it."""
    app.dependency_overrides[verify_admin_token] = lambda: None
    with TestClient(app) as made:
        yield made
    app.dependency_overrides.pop(verify_admin_token, None)


def shipped_text() -> str:
    return mapping_io.EXAMPLE_PATH.read_text(encoding="utf-8")


def test_the_example_route_answers_the_body_the_button_reads(client: Any) -> None:
    """
    The shape ``textOf()`` consumes, and nothing wrapped around it.

    The management surface answers bare objects (``api/relational.py``'s own module docstring);
    an ``{success, data}`` envelope here is still a 200 with the right text inside it, and the
    modal would show ``—`` because the service takes ``response.data`` and reads ``.text`` off
    that object directly.
    """
    reply = client.get(EXAMPLE_URL)

    assert reply.status_code == 200, reply.text
    assert reply.headers["content-type"].startswith("application/json")
    body = reply.json()
    assert isinstance(body, dict)
    assert body["name"] == "mapping.example.yml"
    assert "data" not in body and "success" not in body, list(body)
    text = body.get("text", body.get("yaml"))
    assert isinstance(text, str) and text.strip(), "an example that arrives empty is an empty modal"


def test_the_example_route_serves_the_shipped_file_and_not_a_paraphrase(client: Any) -> None:
    """
    The route's own sentence: *byte for byte*.

    ``EXAMPLE_PATH`` is the pin ``test_relational_mapping_io.py`` already holds — that the file
    sits inside the package. This is the other half: the bytes that route gives the browser are
    those of that file, so a page cannot teach a shape the parser refuses.
    """
    body = client.get(EXAMPLE_URL).json()

    assert body["text"] == shipped_text()
    assert body["text"].lstrip().startswith("# Graph mapping:")
    assert len(body["text"]) > 1000


def test_the_served_example_is_the_annotated_source_and_not_a_re_export(client: Any) -> None:
    """
    Re-serialising the same document would still load, and would still satisfy a reader — and
    it is not the same file.

    Every note in the example is a comment, and the model the loader hands back cannot carry a
    comment: ``to_yaml`` is the route that drops them all. "Byte for byte" is what tells the
    two apart, so it is what gets pinned.
    """
    text = client.get(EXAMPLE_URL).json()["text"]

    rewritten = mapping_io.to_yaml(mapping_io.parse(text))
    assert text != rewritten
    assert any(one.lstrip().startswith("#") for one in text.splitlines())
    assert not any(one.lstrip().startswith("#") for one in rewritten.splitlines())


def test_the_served_example_loads_through_the_route_that_accepts_an_upload(client: Any) -> None:
    """
    The file's whole purpose is to be a document this proxy accepts, so the promise is tested
    through ``mapping_io.parse`` — the loader ``POST /{project}/mapping`` saves with — and not
    through a reader of its own.
    """
    document = mapping_io.parse(client.get(EXAMPLE_URL).json()["text"])

    assert document.schema_name == "dbo"
    assert "AuditLog" in {node.label for node in document.nodes if not node.include}
    assert "sysdiagrams" in document.exclude


def test_an_unreadable_example_is_a_404_the_page_can_report(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The route's own failure branch, and the sentence the docstring carries with it.

    A wheel that shipped without the data file answers the document's name and a 404; a raw
    ``FileError`` escaping here is the 500 that costs a person the draft they were editing.
    """
    missing = tmp_path / "mapping.example.yml"
    assert not missing.exists()
    monkeypatch.setattr(mapping_io, "EXAMPLE_PATH", missing)

    reply = client.get(EXAMPLE_URL)

    assert reply.status_code == 404
    assert "shipped mapping example is not readable" in reply.json()["detail"]


def test_the_example_route_is_behind_the_admin_token(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    What the signature promises — ``Depends(verify_admin_token)`` — against a real gate.

    The fixture above overrides the dependency and so cannot see this: with ``ADMIN_PASSWORD``
    set and no override, an anonymous GET has to be refused. This is the management surface,
    and a route that quietly dropped its ``Depends`` would still answer the whole file.
    """
    app.dependency_overrides.pop(verify_admin_token, None)
    monkeypatch.setenv("ADMIN_PASSWORD", "route-test-password")

    assert client.get(EXAMPLE_URL).status_code == 401
