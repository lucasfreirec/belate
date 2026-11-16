"""Exploration script for M1 (data discovery).

Three commands:

    stations   List the RER B stations from the IDFM open data portal (no API key needed).
    snapshot   Call each PRIM API once and save the raw responses under data/raw/.
    watch      Poll the global and unitary APIs several times in a row, keeping only the
               RER B part of the global response, to see how the feed changes over time.

Each `snapshot` costs one request on each of the four PRIM quotas; each `watch` poll costs
one request on the global quota and one on the unitary quota.

Run through the Makefile (`make stations`, `make snapshot`, `make watch`), which loads `.env`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

PRIM_BASE_URL = "https://prim.iledefrance-mobilites.fr/marketplace"
OPEN_DATA_URL = (
    "https://data.iledefrance-mobilites.fr/api/explore/v2.1/catalog/datasets/arrets-lignes/records"
)

RER_B_LINE_ID = "C01743"
RER_B_LINE_REF = f"STIF:Line::{RER_B_LINE_ID}:"
# Cité Universitaire. Override with PRIM_STATION_REF in .env.
DEFAULT_STATION_REF = "STIF:StopArea:SP:473843:"

DATA_DIR = Path("data")
# The global response covers the whole network, so allow a slow download.
TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)


@dataclass(frozen=True)
class Call:
    """One PRIM request to record."""

    name: str
    path: str
    params: dict[str, str]


def prim_calls(station_ref: str) -> list[Call]:
    return [
        Call("estimated_timetable", "/estimated-timetable", {"LineRef": "ALL"}),
        Call("stop_monitoring", "/stop-monitoring", {"MonitoringRef": station_ref}),
        Call("traffic_info", "/disruptions_bulk/disruptions/v2", {}),
        Call("general_message", "/general-message", {"LineRef": RER_B_LINE_REF}),
    ]


def describe_json(path: Path) -> str:
    """Return the top-level shape of a saved response, or say why it can't be read."""
    try:
        body = json.loads(path.read_bytes())
    except ValueError:
        return "not JSON"
    if isinstance(body, dict):
        return "object with keys " + ", ".join(sorted(body))
    if isinstance(body, list):
        return f"array of {len(body)} items"
    return type(body).__name__


def record(client: httpx.Client, call: Call, out_dir: Path) -> dict[str, object]:
    """Send one request, stream the body to disk, and return what happened."""
    target = out_dir / f"{call.name}.json"
    started = time.monotonic()
    try:
        with client.stream("GET", call.path, params=call.params) as response:
            size = 0
            with target.open("wb") as file:
                for chunk in response.iter_bytes():
                    file.write(chunk)
                    size += len(chunk)
            status: int | None = response.status_code
            headers = dict(response.headers)
            error = None
    except httpx.HTTPError as exc:
        status, headers, size, error = None, {}, 0, f"{type(exc).__name__}: {exc}"
    seconds = round(time.monotonic() - started, 2)

    shape = describe_json(target) if size else "empty"
    outcome = error or f"HTTP {status}"
    print(f"  {call.name:<20} {outcome:<10} {size / 1e6:8.2f} MB  {seconds:6.2f} s  {shape}")
    return {
        "name": call.name,
        "path": call.path,
        "params": call.params,
        "status": status,
        "error": error,
        "bytes": size,
        "seconds": seconds,
        "response_headers": headers,
    }


def trim_to_rer_b(out_dir: Path) -> dict[str, object]:
    """Replace the saved global response with its RER B journeys only.

    Returns the parse time and journey counts, so the cost of handling the full
    response is measured on every poll.
    """
    full = out_dir / "estimated_timetable.json"
    started = time.monotonic()
    body = json.loads(full.read_bytes())
    parse_seconds = round(time.monotonic() - started, 2)

    delivery = body["Siri"]["ServiceDelivery"]
    journeys = [
        journey
        for timetable in delivery["EstimatedTimetableDelivery"]
        for frame in timetable["EstimatedJourneyVersionFrame"]
        for journey in frame["EstimatedVehicleJourney"]
    ]
    rer_b = [j for j in journeys if j["LineRef"]["value"] == RER_B_LINE_REF]
    trimmed = {"ResponseTimestamp": delivery["ResponseTimestamp"], "EstimatedVehicleJourney": rer_b}
    (out_dir / "estimated_timetable_rer_b.json").write_text(json.dumps(trimmed, ensure_ascii=False))
    full.unlink()

    print(
        f"  parsed in {parse_seconds} s; kept {len(rer_b)} RER B journeys of {len(journeys)}; "
        f"feed timestamp {delivery['ResponseTimestamp']}"
    )
    return {
        "parse_seconds": parse_seconds,
        "journeys_total": len(journeys),
        "journeys_rer_b": len(rer_b),
        "feed_timestamp": delivery["ResponseTimestamp"],
    }


def take(client: httpx.Client, calls: list[Call], station_ref: str, trim: bool) -> bool:
    """Record one set of calls into a new timestamped folder. Return True if all succeeded."""
    taken_at = datetime.now(UTC)
    out_dir = DATA_DIR / "raw" / taken_at.strftime("%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True)

    print(f"Saving to {out_dir} (one request per API):")
    results = [record(client, call, out_dir) for call in calls]
    ok = all(r["status"] == 200 for r in results)
    meta: dict[str, object] = {
        "taken_at": taken_at.isoformat(),
        "station_ref": station_ref,
        "calls": results,
    }
    if trim and ok:
        meta["global"] = trim_to_rer_b(out_dir)
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    return ok


def prim_client() -> httpx.Client | None:
    api_key = os.environ.get("PRIM_API_KEY", "")
    if not api_key:
        print("PRIM_API_KEY is not set. Copy .env.example to .env and add your key.")
        return None
    # The key travels only in a request header; it is never written to disk.
    headers = {"apikey": api_key, "Accept": "application/json"}
    return httpx.Client(base_url=PRIM_BASE_URL, headers=headers, timeout=TIMEOUT)


def snapshot(args: argparse.Namespace) -> int:
    station_ref = os.environ.get("PRIM_STATION_REF") or DEFAULT_STATION_REF
    calls = [c for c in prim_calls(station_ref) if not args.only or c.name in args.only]
    client = prim_client()
    if client is None:
        return 1
    with client:
        return 0 if take(client, calls, station_ref, trim=False) else 1


def watch(args: argparse.Namespace) -> int:
    station_ref = os.environ.get("PRIM_STATION_REF") or DEFAULT_STATION_REF
    wanted = ("estimated_timetable", "stop_monitoring")
    calls = [c for c in prim_calls(station_ref) if c.name in wanted]
    client = prim_client()
    if client is None:
        return 1
    with client:
        for poll in range(args.count):
            started = time.monotonic()
            if not take(client, calls, station_ref, trim=True):
                return 1
            if poll < args.count - 1:
                time.sleep(max(0.0, args.interval - (time.monotonic() - started)))
    return 0


def stations(_: argparse.Namespace) -> int:
    params = {
        "where": f'id="IDFM:{RER_B_LINE_ID}"',
        "select": "stop_id,stop_name,stop_lat,stop_lon,nom_commune",
        "limit": "100",
    }
    response = httpx.get(OPEN_DATA_URL, params=params, timeout=30.0)
    response.raise_for_status()
    rows = sorted(response.json()["results"], key=lambda row: row["stop_name"])

    out_file = DATA_DIR / "reference" / "rer_b_stations.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(rows, indent=2, ensure_ascii=False))

    print(f"{len(rows)} RER B stations, saved to {out_file}")
    print("Monitoring ref (for PRIM_STATION_REF)   Station")
    for row in rows:
        stop_number = row["stop_id"].rsplit(":", 1)[-1]
        print(f"STIF:StopArea:SP:{stop_number}:".ljust(40) + row["stop_name"])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("stations", help="list RER B stations (no API key)").set_defaults(
        run=stations
    )
    snap = commands.add_parser("snapshot", help="call each PRIM API once and save the responses")
    snap.add_argument(
        "--only",
        nargs="+",
        choices=[c.name for c in prim_calls(DEFAULT_STATION_REF)],
        help="record only these APIs, to save quota",
    )
    snap.set_defaults(run=snapshot)

    watcher = commands.add_parser("watch", help="poll the global and unitary APIs repeatedly")
    watcher.add_argument("--count", type=int, default=10, help="number of polls (default 10)")
    watcher.add_argument(
        "--interval", type=float, default=30.0, help="seconds between polls (default 30)"
    )
    watcher.set_defaults(run=watch)

    args = parser.parse_args()
    result: int = args.run(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
