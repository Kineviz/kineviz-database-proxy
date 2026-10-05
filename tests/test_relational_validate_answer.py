"""
``POST /api/relational/{project}/validate`` and ``POST …/discover``, pinned at the route.

The editor installs a document from these two replies — an upload or a paste lands from
``/validate``, a Re-discover lands from ``/discover`` — and each row can then carry a badge
saying who decided it. So the question is which reply hands the badges along with the rows,
and the answer both routes now record in their own docstrings is: ``/validate`` answers the
provenance of the document it was just sent, computed by the same ``from_document`` pass
``GET /mapping`` and the save use; ``/discover`` answers none, because a proposal is
discovery's own through and through and ``source: "draft"`` already says that about the whole
document. What is pinned below is that agreement and nothing else — the five kinds themselves
are ``tests/test_relational_mapping_provenance.py``, and the row counts of a proposal are
``tests/test_relational_endpoints.py``.
"""

from __future__ import annotations

import copy
from typing import Any

from graphxr_database_proxy.drivers.relational import mapping_io
from graphxr_database_proxy.models.graph_mapping import GraphMapping
from tests.test_relational_endpoints import client, projects, server  # noqa: F401  (fixtures)

VALIDATE = "/api/relational/mapped/validate"
MAPPING = "/api/relational/mapped/mapping"
DISCOVER = "/api/relational/mapped/discover"


def stored_document(projects: Any) -> dict:
    """The override the fixture's project has saved: discovery's draft, kept as a person's file."""
    return projects.by_name["mapped"].database_config.graph_mapping


def test_a_checked_draft_answers_the_tags_of_the_draft_and_not_of_what_is_stored(
    client: Any, projects: Any, server: Any
) -> None:
    """
    The stale badge, at the route that causes it.

    The page applies a checked file to the tabs, so the tags have to be the checked document's
    own: a name that left the draft has no tag left beside it, and an unticked record or a
    flipped link is answered under the word this document earns. Answering the *stored*
    mapping's tags here is what put yesterday's badge on today's row.
    """
    stored = stored_document(projects)
    draft = copy.deepcopy(stored)
    draft["nodes"][0]["label"] = "Renamed"
    draft["edges"][0]["flip"] = True

    read = client.get(MAPPING).json()
    checked = client.post(VALIDATE, json={"model": draft}).json()

    first_label = stored["nodes"][0]["label"]
    assert read["provenance"][first_label] == "excluded"
    assert first_label not in checked["provenance"], checked["provenance"]
    assert checked["provenance"]["Renamed"] == "excluded"
    assert checked["provenance"][draft["edges"][0]["type"]] == "flipped"
    assert checked["valid"] is True, checked["problems"]


def test_the_check_and_the_stored_read_answer_one_set_of_tags_for_one_document(
    client: Any, projects: Any, server: Any
) -> None:
    """
    One authority: the same document checked and read is the same answer.

    ``GET /mapping`` has always carried these tags, and a page that reads them from one route
    and installs a document from another needs the two computed the same way — which they are,
    both being the loader's pass over that document with no catalog beside it. That is also why
    a saved draft's own rows read ``added`` here rather than ``auto``: reaching no database is
    the promise ``GET /mapping`` makes, and with nothing to compare against a declared record is
    a record nothing proposed.
    """
    document = stored_document(projects)

    read = client.get(MAPPING).json()
    checked = client.post(VALIDATE, json={"model": document}).json()

    assert checked["provenance"] == read["provenance"]
    assert checked["valid"] is True and checked["problems"] == []
    assert set(checked["provenance"]) == {node["label"] for node in document["nodes"]} | {
        edge["type"] for edge in document["edges"]
    }


def test_a_check_that_reports_problems_still_answers_the_tags_it_resolved(
    client: Any, projects: Any, server: Any
) -> None:
    """
    Reported, not withheld.

    A refused record has no tag because the loader never built it, and the records beside it keep
    theirs — the same half-answer ``problems`` and ``warnings`` already give. The key is always
    there, so the page never has to tell "nothing decided" from "this build answers no tags".
    """
    mixed = {
        "nodes": [
            {"label": "Whole", "table": "Person", "key": "person_id"},
            {"label": "Broken", "table": "Organization", "key": "org_id,legal_name"},
        ],
        "edges": [],
    }

    answer = client.post(VALIDATE, json={"model": mixed}).json()

    assert answer["valid"] is False
    assert [problem["path"] for problem in answer["problems"]] == ["nodes[1].key"]
    assert answer["provenance"] == {"Whole": "added"}


def test_the_pasted_text_and_the_edited_object_answer_the_same_tags(
    client: Any, projects: Any, server: Any
) -> None:
    """
    The two bodies one route takes, and the one the upload uses.

    Tags are computed off the parsed document, so a person who pasted a file gets what the
    structured tabs get — otherwise the badge depends on which tab the draft was written in.
    """
    text = mapping_io.to_yaml(GraphMapping.model_validate(stored_document(projects)))

    as_text = client.post(VALIDATE, json={"yaml": text}).json()
    as_model = client.post(VALIDATE, json={"model": stored_document(projects)}).json()

    assert as_text["provenance"] == as_model["provenance"]


def test_the_check_answers_exactly_the_document_keys_the_editor_reads(
    client: Any, projects: Any, server: Any
) -> None:
    """
    The reply's whole shape, named.

    A page that installs rows from a reply also reads the tags off it, so the key set is the
    contract. ``nodes`` and ``edges`` are counts for the summary line, and there is no
    ``saved``: a check stores nothing.
    """
    answer = client.post(VALIDATE, json={"model": stored_document(projects)}).json()

    assert set(answer) == {
        "valid",
        "problems",
        "warnings",
        "provenance",
        "nodes",
        "edges",
        "model",
        "text",
    }
    assert answer["nodes"] == len(answer["model"]["nodes"])
    assert "saved" not in answer and "removed" not in answer


def test_a_proposal_answers_no_provenance_and_says_who_wrote_the_document_instead(
    client: Any, projects: Any, server: Any
) -> None:
    """
    The other half of the decision, pinned so the next reader does not "fix" it.

    ``/discover`` answers the proposal, its source and the sentences discovery arrived with —
    each entry's ``notes``, and the caps and skips in ``warnings``. A tag per row could only
    ever read ``auto`` here, which is what ``source: "draft"`` already says about the whole
    document, so the page shows no badges for a proposal and drops the ones it had.
    """
    answer = client.post(DISCOVER, json={}).json()

    assert set(answer) == {"project", "model", "source", "warnings"}
    assert answer["source"] == "draft"
    assert "provenance" not in answer
    assert answer["model"]["nodes"], "a proposal with no rows has nothing to tag either way"
    assert any(node.get("notes") for node in answer["model"]["nodes"]), (
        "the reasoning a proposal does carry lives on its entries"
    )
