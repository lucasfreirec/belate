"""add stations and analytics columns

Revision ID: 5adfd81f3508
Revises: e7471ac15d2e
Create Date: 2026-11-16 14:42:48.346634

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.analytics.derive import DERIVE_SQL
from app.reference.stations import STATIONS

# revision identifiers, used by Alembic.
revision: str = "5adfd81f3508"
down_revision: str | Sequence[str] | None = "e7471ac15d2e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    stations = op.create_table(
        "stations",
        sa.Column("ref", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("section", sa.String(length=16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("ref"),
    )
    # The line does not change between releases, so the reference list is used as it is.
    op.bulk_insert(
        stations,
        [
            {"ref": s.ref, "name": s.name, "section": s.section.value, "position": s.position}
            for s in STATIONS
        ],
    )
    op.add_column("departures", sa.Column("aimed_time", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "departures",
        sa.Column("global_expected_departure", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "departures",
        sa.Column("unitary_expected_departure", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("departures", sa.Column("delay_seconds", sa.Integer(), nullable=True))
    op.add_column("departures", sa.Column("service_date", sa.Date(), nullable=True))
    op.add_column("departures", sa.Column("local_hour", sa.SmallInteger(), nullable=True))
    op.add_column("departures", sa.Column("reliable", sa.Boolean(), nullable=True))
    # Fill the new columns for the departures already collected.
    op.execute("UPDATE departures SET aimed_time = COALESCE(aimed_departure, aimed_arrival)")
    op.execute(DERIVE_SQL)
    op.drop_index(op.f("ix_departures_station_aimed"), table_name="departures")
    op.create_index("ix_departures_aimed_time", "departures", ["aimed_time"], unique=False)
    op.create_index(
        "ix_departures_station_aimed_time",
        "departures",
        ["station_ref", "aimed_time"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_departures_station_aimed_time", table_name="departures")
    op.drop_index("ix_departures_aimed_time", table_name="departures")
    op.create_index(
        op.f("ix_departures_station_aimed"),
        "departures",
        ["station_ref", "aimed_departure"],
        unique=False,
    )
    op.drop_column("departures", "reliable")
    op.drop_column("departures", "local_hour")
    op.drop_column("departures", "service_date")
    op.drop_column("departures", "delay_seconds")
    op.drop_column("departures", "unitary_expected_departure")
    op.drop_column("departures", "global_expected_departure")
    op.drop_column("departures", "aimed_time")
    op.drop_table("stations")
