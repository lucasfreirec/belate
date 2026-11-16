"""Time the analytics queries on a synthetic history.

Creates a separate database `belate_bench` on the local PostgreSQL (`make up`), fills it with
the given number of days of invented departures at the real daily volume, ending now, and
prints the median time of each query. The real database is not touched.

With --keep the database stays, which also makes it a demo data set for the frontend:

    DATABASE_URL=postgresql+psycopg://belate:belate@localhost:5432/belate_bench make api

    uv run python -m scripts.bench_queries --days 30
"""

from __future__ import annotations

import argparse
import statistics
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.orm import Session

from app.analytics.derive import DERIVE_SQL
from app.analytics.queries import (
    Dimension,
    measurement_error,
    punctuality,
    segments,
    waiting_time,
)
from app.core.config import get_settings
from app.prim.models import Direction

BENCH_DATABASE = "belate_bench"
TRAINS_PER_DAY = 600
STOPS_PER_TRAIN = 25
CITE_UNIVERSITAIRE = "STIF:StopArea:SP:473843:"
RUNS = 7

# One row per (day, train, stop). Trains are spread over 19 hours; delays are skewed like
# real ones (most small, a few large); 3% of the departures are cancelled.
FILL = text(
    """
    INSERT INTO departures (
        journey_ref, station_ref, direction, destination_ref, aimed_departure, aimed_time,
        first_expected_departure, last_expected_departure, global_expected_departure,
        unitary_expected_departure, last_status, first_seen_at, last_seen_at, outcome,
        closed_at
    )
    SELECT
        'bench-' || day || '-' || train,
        stop.ref,
        CASE WHEN train % 2 = 0 THEN 'southbound' ELSE 'northbound' END,
        CASE WHEN train % 2 = 0 THEN :south_terminal ELSE :north_terminal END,
        aimed, aimed, aimed + delay, aimed + delay, aimed + delay,
        CASE WHEN stop.ref = :my_station THEN aimed + delay + make_interval(secs => train % 40)
        END,
        CASE WHEN cancelled THEN 'cancelled' ELSE 'on_time' END,
        aimed - interval '2 hours', aimed + delay + interval '90 seconds',
        CASE WHEN cancelled THEN 'cancelled' ELSE 'departed' END,
        aimed + delay + interval '2 minutes'
    FROM generate_series(0, :days - 1) AS day
    CROSS JOIN generate_series(0, :trains - 1) AS train
    CROSS JOIN LATERAL (
        SELECT ref, row_number() OVER (ORDER BY section, position) AS rank
        FROM stations
        ORDER BY section, position
        LIMIT :stops OFFSET (train % 20)
    ) AS stop
    CROSS JOIN LATERAL (
        SELECT :start + make_interval(days => day, secs => train * 114 + stop.rank * 150) AS aimed
    ) AS schedule
    CROSS JOIN LATERAL (
        SELECT
            -- Rush hours run later, as on the real line.
            make_interval(secs => floor(
                600 * power(random(), 4)
                * CASE WHEN extract(hour FROM aimed) IN (6, 7, 16, 17) THEN 2.5 ELSE 1 END
            )::int) AS delay,
            random() < 0.03 AS cancelled
    ) AS timing
    """
)


def bench_url() -> str:
    return (
        make_url(get_settings().database_url).set(database=BENCH_DATABASE).render_as_string(False)
    )


def recreate_database() -> None:
    admin = create_engine(get_settings().database_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f"DROP DATABASE IF EXISTS {BENCH_DATABASE} WITH (FORCE)"))
        connection.execute(text(f"CREATE DATABASE {BENCH_DATABASE}"))
    admin.dispose()

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", bench_url())
    command.upgrade(config, "head")


def median_ms(run: Callable[[], object]) -> float:
    run()  # warm up
    times = []
    for _ in range(RUNS):
        started = time.perf_counter()
        run()
        times.append((time.perf_counter() - started) * 1000)
    return statistics.median(times)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=30, help="days of history to invent")
    parser.add_argument("--keep", action="store_true", help="keep the bench database afterwards")
    args = parser.parse_args()

    # The history ends now, so the API's "last 7 days" windows find it.
    start = (datetime.now(UTC) - timedelta(days=args.days)).replace(
        hour=4, minute=0, second=0, microsecond=0
    )
    recreate_database()
    engine = create_engine(bench_url())
    with engine.begin() as connection:
        connection.execute(
            FILL,
            {
                "days": args.days,
                "trains": TRAINS_PER_DAY,
                "stops": STOPS_PER_TRAIN,
                "start": start,
                "my_station": CITE_UNIVERSITAIRE,
                "south_terminal": "STIF:StopArea:SP:47889:",
                "north_terminal": "STIF:StopArea:SP:473364:",
            },
        )
        connection.execute(text(DERIVE_SQL))
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        # The bulk update leaves dead rows behind; a real table is vacuumed regularly.
        connection.execute(text("VACUUM (FULL, ANALYZE) departures"))
        rows = connection.execute(text("SELECT count(*) FROM departures")).scalar_one()
        size = connection.execute(
            text("SELECT pg_size_pretty(pg_total_relation_size('departures'))")
        ).scalar_one()
    print(f"{args.days} days, {rows:,} departures, table and indexes: {size}\n")

    end = start + timedelta(days=args.days)
    week_ago = end - timedelta(days=7)
    south = Direction.SOUTHBOUND
    with Session(engine) as session:
        cases: dict[str, Callable[[], object]] = {
            "punctuality, 7 days, overall": lambda: punctuality(session, since=week_ago, until=end),
            "punctuality, 7 days, by branch and direction": lambda: punctuality(
                session,
                since=week_ago,
                until=end,
                group_by=[Dimension.BRANCH, Dimension.DIRECTION],
            ),
            "punctuality, 7 days, by station": lambda: punctuality(
                session, since=week_ago, until=end, group_by=[Dimension.STATION]
            ),
            "punctuality, 7 days, one station by hour": lambda: punctuality(
                session,
                since=week_ago,
                until=end,
                station_ref=CITE_UNIVERSITAIRE,
                group_by=[Dimension.HOUR],
            ),
            f"punctuality, {args.days} days, by day type and hour": lambda: punctuality(
                session, since=start, until=end, group_by=[Dimension.DAY_TYPE, Dimension.HOUR]
            ),
            "segments, 7 days, southbound": lambda: segments(
                session, since=week_ago, until=end, direction=south
            ),
            "waiting time, 7 days, one station": lambda: waiting_time(
                session, since=week_ago, until=end, station_ref=CITE_UNIVERSITAIRE, direction=south
            ),
            "measurement error, 7 days": lambda: measurement_error(
                session, since=week_ago, until=end, station_ref=CITE_UNIVERSITAIRE
            ),
        }
        for name, run in cases.items():
            print(f"{median_ms(run):8.1f} ms  {name}")
    engine.dispose()

    if not args.keep:
        admin = create_engine(get_settings().database_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            connection.execute(text(f"DROP DATABASE {BENCH_DATABASE} WITH (FORCE)"))
        admin.dispose()


if __name__ == "__main__":
    main()
