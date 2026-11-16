# Runbook

How to run and look after the collector. The collector polls PRIM around the clock and
stores the history in PostgreSQL; everything else in the project reads that history.

## Deploy the collector on a server

Requirements: a Linux server with Docker and the Compose plugin, about 2 GB of free memory,
and `make` and `git`.

```sh
git clone https://github.com/lucasfreirec/belate.git
cd belate
cp .env.example .env
```

Edit `.env`:

- `PRIM_API_KEY`: the key from the PRIM portal.
- `POSTGRES_PASSWORD`: a long random password, for example from `openssl rand -hex 24`. Set it
  before the first start; changing it later does not change the existing database.
- `PRIM_STATION_REF`: the station polled every minute at commute hours (`make stations`
  lists them).

Then:

```sh
make collector-up       # build the image, migrate the database, start everything
make collector-status   # data age and quota used today
make collector-logs     # follow the worker and the scheduler
```

The same API key must not be used by a second collector at the same time: both would draw
on the same PRIM quota without seeing each other's requests. The quota guard corrects its
count from PRIM's response headers, but a second collector still halves the budget.

## Update to a new version

```sh
git pull
make collector-up
```

Migrations run before the workers start. Polling stops for the few seconds of the restart.

## Check that it is healthy

`make collector-status` prints, and exits with an error if the newest data is more than
10 minutes old:

```
Latest departure data: 41 s ago
Departures: 2431 open, 1502 departed, 3 cancelled, 12 unknown
Disruptions recorded: 4
Quota used today (UTC):
  global         421 / 1000
  unitary        180 / 1000
  traffic_info   169 / 1000
```

## No new data

1. `make collector-logs`. A line such as `global poll skipped: PrimUnavailable` means PRIM
   is failing; the next poll retries by itself.
2. `QuotaExhausted` in the logs: the daily budget is used. It resets at midnight UTC. Check
   that no other program uses the same key.
3. No log lines at all from the scheduler: `docker compose -f
   deploy/compose/docker-compose.collector.yml ps` and check that `beat` is running, and that
   there is exactly one.

## Backups

The `backup` service writes one compressed dump a day to `backups/` on the server and deletes
dumps older than 14 days. A dump on the same disk does not protect against losing the server:
copy the folder elsewhere regularly, for example from your own machine:

```sh
rsync -av user@server:belate/backups/ ./belate-backups/
```

Restore a dump into a running stack:

```sh
gunzip -c backups/belate-2026-11-20.sql.gz | docker compose \
  -f deploy/compose/docker-compose.collector.yml --env-file .env \
  exec -T postgres psql -U belate belate
```

Restore into an empty database only; stop `worker` and `beat` first.

## Rotate the API key

Generate a new key on the PRIM portal, replace `PRIM_API_KEY` in `.env`, then
`make collector-up`.
