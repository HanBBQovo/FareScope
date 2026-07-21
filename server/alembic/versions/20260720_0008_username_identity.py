"""move account identity from email to username

Revision ID: 20260720_0008
Revises: 20260720_0007
Create Date: 2026-07-20 23:30:00
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260720_0008"
down_revision: str | None = "20260720_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USERNAME_MAX_LENGTH = 64
EMAIL_MAX_LENGTH = 320
_INVALID_USERNAME_CHARS = re.compile(r"[^a-z0-9_.-]+")


def _username_base(email: object, user_id: object) -> str:
    """Create a stable, valid base from legacy data without depending on row order."""

    raw_email = str(email or "").strip().casefold()
    local_part = raw_email.partition("@")[0]
    candidate = _INVALID_USERNAME_CHARS.sub("-", local_part).strip("._-")
    if not candidate:
        candidate = "user"
    if not candidate[0].isalnum():
        candidate = f"user-{candidate}"
    if len(candidate) < 3:
        candidate = f"user-{candidate}"
    candidate = candidate[:USERNAME_MAX_LENGTH].rstrip("._-") or "user"
    if len(candidate) < 3:
        candidate = "user"

    # A missing/blank email must still be distinguishable when several rows exist.
    if not raw_email:
        candidate = f"user-{str(user_id).replace('-', '')[:8]}"
    return candidate[:USERNAME_MAX_LENGTH]


def _unique_username(email: object, user_id: object, used: set[str]) -> str:
    base = _username_base(email, user_id)
    candidate = base
    user_id_hex = str(user_id).replace("-", "").casefold()
    attempt = 0
    while candidate in used:
        suffix = user_id_hex[:8] or "00000000"
        if attempt:
            suffix = f"{user_id_hex[:6] or '000000'}{attempt:02d}"
        root_length = USERNAME_MAX_LENGTH - len(suffix) - 1
        root = base[:root_length].rstrip("._-") or "user"
        candidate = f"{root}-{suffix}"
        attempt += 1
    used.add(candidate)
    return candidate


def _unique_legacy_email(email: object, username: str, user_id: object, used: set[str]) -> str:
    """Restore a non-null unique legacy email during a downgrade.

    New accounts may have no email, and multiple accounts may intentionally share a
    notification destination. The old schema could not represent either case, so
    deterministic local-part suffixes keep the downgrade reversible at the schema
    level without silently violating the old unique constraint.
    """

    candidate = str(email or "").strip().casefold() or f"{username}@example.invalid"
    local_part, separator, domain = candidate.partition("@")
    if not separator or not local_part:
        local_part, domain = username, "example.invalid"
    domain = domain or "example.invalid"
    suffix = str(user_id).replace("-", "").casefold()[:8] or "00000000"
    attempt = 0

    while True:
        full = f"{local_part}@{domain}"
        if full not in used and len(full) <= EMAIL_MAX_LENGTH:
            used.add(full)
            return full
        marker = f"+{suffix}" if attempt == 0 else f"+{suffix}{attempt}"
        max_local_length = EMAIL_MAX_LENGTH - len(domain) - len(marker) - 1
        local_root = (local_part[:max_local_length] or "user").rstrip("._-") or "user"
        local_part = local_root + marker
        attempt += 1


def _backfill_usernames(connection: object) -> None:
    rows = connection.execute(
        sa.text("SELECT id, email FROM users ORDER BY created_at, id")
    ).mappings()
    used_usernames: set[str] = set()
    for row in rows:
        username = _unique_username(row["email"], row["id"], used_usernames)
        connection.execute(
            sa.text(
                "UPDATE users "
                "SET username = :username, normalized_username = :normalized_username "
                "WHERE id = :id"
            ),
            {
                "id": row["id"],
                "username": username,
                "normalized_username": username,
            },
        )


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(length=64), nullable=True))
    op.add_column(
        "users", sa.Column("normalized_username", sa.String(length=64), nullable=True)
    )

    _backfill_usernames(op.get_bind())

    op.alter_column(
        "users",
        "username",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.alter_column(
        "users",
        "normalized_username",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.alter_column(
        "users",
        "email",
        existing_type=sa.String(length=320),
        nullable=True,
    )
    op.create_unique_constraint(
        "uq_users_normalized_username", "users", ["normalized_username"]
    )
    op.drop_constraint("uq_users_normalized_email", "users", type_="unique")
    op.drop_column("users", "normalized_email")


def downgrade() -> None:
    op.add_column(
        "users", sa.Column("normalized_email", sa.String(length=320), nullable=True)
    )

    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, username, email FROM users ORDER BY created_at, id")
    ).mappings()
    used_emails: set[str] = set()
    for row in rows:
        email = _unique_legacy_email(row["email"], row["username"], row["id"], used_emails)
        connection.execute(
            sa.text(
                "UPDATE users SET email = :email, normalized_email = :normalized_email "
                "WHERE id = :id"
            ),
            {
                "id": row["id"],
                "email": email,
                "normalized_email": email,
            },
        )

    op.alter_column(
        "users",
        "email",
        existing_type=sa.String(length=320),
        nullable=False,
    )
    op.alter_column(
        "users",
        "normalized_email",
        existing_type=sa.String(length=320),
        nullable=False,
    )
    op.create_unique_constraint("uq_users_normalized_email", "users", ["normalized_email"])
    op.drop_constraint("uq_users_normalized_username", "users", type_="unique")
    op.drop_column("users", "normalized_username")
    op.drop_column("users", "username")
