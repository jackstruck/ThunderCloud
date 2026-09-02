import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from worker.db import Database


class EmptyCursor:
    def execute(self, statement, parameters):
        self.statement = statement
        self.parameters = parameters

    def fetchone(self):
        return None


def test_match_can_return_unknown():
    match = Database._match(EmptyCursor(), [1, 0], top_k=5, threshold=0.6)
    assert match.decision == "unknown"
    assert match.subject_id is None


def test_database_selects_private_connector_path():
    database = Database("project:region:instance", "user", "db", "PRIVATE")
    connector_class = Mock()
    ip_types = SimpleNamespace(PUBLIC="public", PRIVATE="private")
    connector_module = ModuleType("google.cloud.sql.connector")
    connector_module.Connector = connector_class
    connector_module.IPTypes = ip_types
    modules = {
        "google": ModuleType("google"),
        "google.cloud": ModuleType("google.cloud"),
        "google.cloud.sql": ModuleType("google.cloud.sql"),
        "google.cloud.sql.connector": connector_module,
    }
    with patch.dict(sys.modules, modules):
        database.connect()
    connector_class.return_value.connect.assert_called_once_with(
        "project:region:instance",
        "pg8000",
        user="user",
        db="db",
        enable_iam_auth=True,
        ip_type=ip_types.PRIVATE,
    )


def test_database_rejects_unknown_connector_path():
    with pytest.raises(ValueError, match="PUBLIC or PRIVATE"):
        Database("instance", "user", "db", "INTERNAL")


class RankingCursor:
    def __init__(self):
        self.results = []
        self.statements = []

    def execute(self, statement, parameters):
        self.statements.append((statement, parameters))
        self.results = (
            [("b-subject", 0.8, "label-b"), ("a-subject", 0.8, None)]
            if "FROM subject" in statement
            else []
        )

    def fetchall(self):
        return self.results


def test_rank_subjects_filters_version_and_has_deterministic_tie_break():
    cursor = RankingCursor()
    ranked = Database.rank_subjects(cursor, [1, 0], "adaface-v1", 2)
    sql, parameters = cursor.statements[0]
    assert "model_version = %s" in sql
    assert "subject_id" in sql.split("ORDER BY", 1)[1]
    assert parameters[1] == "adaface-v1"
    assert [candidate.subject_id for candidate in ranked] == ["b-subject", "a-subject"]
    assert ranked[0].display_name == "label-b"
