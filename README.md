# Belate

Collects real-time data for the RER B (Paris), stores the history and reports on punctuality,
cancellations and disruptions. Work in progress.

## How it works

A scheduler triggers three polling tasks against the Île-de-France Mobilités PRIM APIs:

| What | How often | Why |
|---|---|---|
| Next departures for the whole network | every 2 minutes | every RER B train at every station |
| Next departures at one station | every minute at commute hours | fresher data where I wait |
| Traffic info | every 5 minutes | disruptions |

Each train at each station becomes one row in PostgreSQL, updated at every poll until the
train has left, was cancelled, or disappeared. A quota guard in Redis keeps every API under
its limit of 1,000 requests a day.

- [docs/data.md](docs/data.md): what the PRIM data contains, and the definitions used.
- [docs/runbook.md](docs/runbook.md): how to deploy and look after the collector.

## Development

Requirements: `uv`, Node.js 22, Docker.

```sh
make setup   # install dependencies and git hooks
make up      # start PostgreSQL and Redis
make test    # backend and frontend tests (the integration tests start Docker containers)
make lint    # linters and type checks
make down    # stop PostgreSQL and Redis
```

Copy `.env.example` to `.env` and add a PRIM API key to poll the real APIs:

```sh
make migrate   # create the tables
make worker    # in one terminal
make beat      # in another
make status    # data age and quota used today
```

Never commit `.env`.
