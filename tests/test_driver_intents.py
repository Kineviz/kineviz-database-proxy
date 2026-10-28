"""
Every shipped driver carries a capability record and a dialect, so the GraphXR
client can use intent mode instead of building statements itself.

The point of the parametrised cases is that adding a driver cannot quietly skip
this: a new backend that forgets its dialect, or declares an intent it has not
implemented, fails here rather than at the user's first click.
"""

from __future__ import annotations

import pytest

from kineviz_database_proxy.contract.capabilities import assert_capabilities_consistent
from kineviz_database_proxy.drivers.bigquery import BigQueryDriver
from kineviz_database_proxy.drivers.dialect import (
    BIGQUERY_DIALECT,
    KUZU_DIALECT,
    LADYBUG_DIALECT,
    LATTICEDB_DIALECT,
    MEMGRAPH_DIALECT,
    MSSQL_DIALECT,
    MYSQL_DIALECT,
    NEO4J_DIALECT,
    POSTGRESQL_DIALECT,
    ROCKETGRAPH_DIALECT,
    SPANNER_DIALECT,
)
from kineviz_database_proxy.drivers.factory import DriverFactory
from kineviz_database_proxy.drivers.graph_support import (
    BIGQUERY_CAPABILITIES,
    KUZU_CAPABILITIES,
    LADYBUG_CAPABILITIES,
    LATTICEDB_CAPABILITIES,
    MEMGRAPH_CAPABILITIES,
    MSSQL_CAPABILITIES,
    MYSQL_CAPABILITIES,
    NEO4J_CAPABILITIES,
    POSTGRESQL_CAPABILITIES,
    ROCKETGRAPH_CAPABILITIES,
    SPANNER_CAPABILITIES,
    GraphIntentSupport,
)
from kineviz_database_proxy.drivers.kuzu import KuzuDriver
from kineviz_database_proxy.drivers.ladybug import LadybugDriver
from kineviz_database_proxy.drivers.latticedb import LatticeDbDriver
from kineviz_database_proxy.drivers.memgraph import MemgraphDriver
from kineviz_database_proxy.drivers.mssql import MssqlDriver
from kineviz_database_proxy.drivers.mysql import MysqlDriver
from kineviz_database_proxy.drivers.neo4j import Neo4jDriver
from kineviz_database_proxy.drivers.postgresql import PostgresqlDriver
from kineviz_database_proxy.drivers.snowflake import SnowflakeDriver
from kineviz_database_proxy.drivers.relational.driver import RelationalDriver
from kineviz_database_proxy.drivers.rocketgraph import RocketGraphDriver
from kineviz_database_proxy.drivers.spanner import SpannerDriver
from tests.test_relational_capability_record import relational_backends

ALL_DRIVERS = (
    SpannerDriver,
    BigQueryDriver,
    RocketGraphDriver,
    Neo4jDriver,
    MemgraphDriver,
    KuzuDriver,
    LadybugDriver,
    LatticeDbDriver,
    MssqlDriver,
    PostgresqlDriver,
    MysqlDriver,
    SnowflakeDriver,
)


@pytest.mark.parametrize(
    "capabilities",
    [
        SPANNER_CAPABILITIES,
        BIGQUERY_CAPABILITIES,
        ROCKETGRAPH_CAPABILITIES,
        NEO4J_CAPABILITIES,
        MEMGRAPH_CAPABILITIES,
        KUZU_CAPABILITIES,
        LADYBUG_CAPABILITIES,
        LATTICEDB_CAPABILITIES,
        MSSQL_CAPABILITIES,
        POSTGRESQL_CAPABILITIES,
        MYSQL_CAPABILITIES,
    ],
)
def test_every_shipped_capability_record_is_consistent(capabilities):
    assert_capabilities_consistent(capabilities)


@pytest.mark.parametrize("driver_class", ALL_DRIVERS, ids=lambda cls: cls.__name__)
def test_every_driver_exposes_the_intent_surface(driver_class):
    assert hasattr(driver_class, "graph_capabilities"), driver_class.__name__
    assert hasattr(driver_class, "graph_dialect"), driver_class.__name__
    assert hasattr(driver_class, "expand"), driver_class.__name__


@pytest.mark.parametrize("driver_class", ALL_DRIVERS, ids=lambda cls: cls.__name__)
def test_a_driver_declares_the_predicate_its_dialect_actually_emits(driver_class):
    # A record claiming "internal-id" over a key-based dialect would build a
    # statement that silently returns nothing.
    assert driver_class.graph_capabilities.expand.predicate == driver_class.graph_dialect.predicate


def test_every_registered_database_type_reaches_a_driver_that_carries_a_dialect():
    for database_type in DriverFactory.get_supported_types():
        driver_class = DriverFactory._drivers[database_type]
        assert driver_class in ALL_DRIVERS, database_type
        assert driver_class.graph_dialect.name == database_type.value


def test_the_dialects_line_up_with_the_declared_predicates():
    assert SpannerDriver.graph_dialect is SPANNER_DIALECT
    assert SpannerDriver.graph_capabilities.expand.predicate == SPANNER_DIALECT.predicate

    assert RocketGraphDriver.graph_dialect is ROCKETGRAPH_DIALECT
    assert RocketGraphDriver.graph_capabilities.expand.predicate == ROCKETGRAPH_DIALECT.predicate
    # XGT has no node-identity function, so its ids carry the label and key.
    assert RocketGraphDriver.graph_capabilities.identity.nodeId == "label-key"

    # The embedded pair has RocketGraph's identity problem for a different reason:
    # ID(n) exists but `n._id` is rejected as reserved, so there is no literal a
    # predicate could match on and the key is all that is left.
    assert KuzuDriver.graph_dialect is KUZU_DIALECT
    assert LadybugDriver.graph_dialect is LADYBUG_DIALECT
    for driver in (KuzuDriver, LadybugDriver):
        assert driver.graph_capabilities.identity.nodeId == "label-key"
        # An edge's endpoints arrive as internal ids, so both nodes must come back
        # with it for the edge to be placed at all.
        assert driver.graph_dialect.return_vars == ("n", "r", "m")
        # `<-[r]->` is a parse error on both engines.
        assert "both" not in driver.graph_capabilities.expand.directions


def test_ladybug_declares_kuzus_surface_under_its_own_name():
    # One engine, two names: the capability records must differ only in the name,
    # so a difference that creeps in later is a deliberate one.
    kuzu = KUZU_CAPABILITIES.model_dump(by_alias=True)
    ladybug = LADYBUG_CAPABILITIES.model_dump(by_alias=True)
    assert kuzu.pop("type") == "kuzu"
    assert ladybug.pop("type") == "ladybug"
    assert kuzu == ladybug


@pytest.mark.parametrize("driver_class", ALL_DRIVERS, ids=lambda cls: cls.__name__)
def test_declared_intents_are_all_implemented(driver_class):
    for intent in driver_class.graph_capabilities.intents or []:
        method = {"expand": "expand", "pullCategory": "pull_category",
                  "pullRelationship": "pull_relationship", "search": "fulltext_search"}[intent]
        assert callable(getattr(driver_class, method, None)), f"{driver_class.__name__}.{method}"


def test_only_the_backend_with_an_index_declares_the_search_intent():
    # The base mixin raises NotImplementedError, and the endpoint gates on the
    # declaration, so an undeclared search is a 501 rather than a 500.
    searchable = [cls.__name__ for cls in ALL_DRIVERS if "search" in (cls.graph_capabilities.intents or [])]
    assert searchable == ["Neo4jDriver"]


def test_bigquery_declares_spanners_surface_under_its_own_name():
    # BigQuery graph is the same GQL; the one real difference is identity, which
    # lives in the dialect rather than the capability record.
    spanner = SPANNER_CAPABILITIES.model_dump(by_alias=True)
    bigquery = BIGQUERY_CAPABILITIES.model_dump(by_alias=True)
    assert bigquery.pop("type") == "bigquery"
    assert spanner.pop("type") == "spanner"
    assert bigquery == spanner


def test_key_types_are_read_from_the_remembered_schema():
    support = GraphIntentSupport()
    support.remember_graph_categories(
        {
            "Person": {"keys": ["id"], "keysTypes": {"id": "INT64"}},
            "Loose": {"keys": [], "keysTypes": {}},
        }
    )
    assert support.graph_key_types() == {"Person": "INT64"}
    # A category with no key contributes nothing rather than a null entry.
    assert "Loose" not in support.graph_key_types()


def test_an_unremembered_schema_yields_no_key_types_rather_than_an_error():
    assert GraphIntentSupport().graph_key_types() == {}
    assert GraphIntentSupport().graph_categories() == {}


def test_latticedb_is_the_only_embedded_driver_that_selects_seeds_by_identity():
    """
    Kuzu and Ladybug have to go through a primary key because their ids have no
    literal to match against; LatticeDB's ``id(n)`` matches directly. A record that
    got this wrong would build statements that quietly return nothing.
    """
    assert LatticeDbDriver.graph_dialect is LATTICEDB_DIALECT
    assert LatticeDbDriver.graph_capabilities.expand.predicate == "internal-id"
    assert LatticeDbDriver.graph_capabilities.identity.nodeId == "internal-id"
    assert KuzuDriver.graph_capabilities.expand.predicate == "primary-key"
    assert LadybugDriver.graph_capabilities.expand.predicate == "primary-key"


def test_latticedb_does_not_offer_multi_hop_that_would_crash_its_engine():
    # The dialect and the record have to agree, or the client offers a hop count
    # the proxy then silently ignores.
    assert LatticeDbDriver.graph_capabilities.expand.multiHop is False
    assert LATTICEDB_DIALECT.supports_multi_hop is False


def test_the_relational_records_differ_in_nothing_but_their_name():
    # One family function (`_relational_capabilities`) behind three names, in the shape the
    # embedded and GQL pairs above are pinned: a difference that appears later is a
    # deliberate one, and the client mirrors one record for all three.
    base = MSSQL_CAPABILITIES.model_dump(by_alias=True)
    assert base.pop("type") == "mssql"
    for record in (POSTGRESQL_CAPABILITIES, MYSQL_CAPABILITIES):
        other = record.model_dump(by_alias=True)
        assert other.pop("type") in ("postgresql", "mysql")
        assert base == other


def test_the_relational_drivers_the_factory_registers_are_the_ones_this_list_hands_round():
    """
    One answer rather than two: the hand-typed `ALL_DRIVERS` above and the backend list the
    capability-record tests derive from the registry have to cover the same engines, so a
    fourth relational driver cannot be recorded in one file and skipped in the other.
    """
    derived = {DriverFactory._drivers[backend] for backend in relational_backends()}
    assert derived <= set(ALL_DRIVERS)
    assert {cls for cls in ALL_DRIVERS if issubclass(cls, RelationalDriver)} == derived
    for driver_class in derived:
        assert driver_class.graph_capabilities.type == driver_class.database_type
        assert driver_class.graph_dialect.name == driver_class.database_type
