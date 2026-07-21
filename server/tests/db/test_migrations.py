from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory


def _username_migration_module():
    path = Path(__file__).parents[2] / "alembic" / "versions" / "20260720_0008_username_identity.py"
    spec = spec_from_file_location("username_identity_migration", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_alembic_has_one_linear_head() -> None:
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_heads() == ["20260720_0012"]
    assert scripts.get_base() == "20260720_0001"


def test_username_backfill_is_unique_and_preserves_display_names() -> None:
    migration = _username_migration_module()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE users ("
                "id VARCHAR(64) PRIMARY KEY, email VARCHAR(320), "
                "username VARCHAR(64), normalized_username VARCHAR(64), "
                "display_name VARCHAR(120) NOT NULL, created_at INTEGER NOT NULL)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO users (id, email, display_name, created_at) VALUES "
                "('1', 'Alice@example.test', 'Alice from import', 1), "
                "('2', 'alice@example.test', 'Second legacy name', 2), "
                "('3', NULL, 'No email name', 3)"
            )
        )

        migration._backfill_usernames(connection)
        rows = (
            connection.execute(
                sa.text("SELECT username, normalized_username, display_name FROM users ORDER BY id")
            )
            .mappings()
            .all()
        )

    usernames = [row["username"] for row in rows]
    assert usernames[0] == "alice"
    assert usernames[1].startswith("alice-")
    assert usernames[2].startswith("user-")
    assert usernames == [row["normalized_username"] for row in rows]
    assert len(set(usernames)) == len(usernames)
    assert [row["display_name"] for row in rows] == [
        "Alice from import",
        "Second legacy name",
        "No email name",
    ]
