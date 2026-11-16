# RER B Reliability

Collects real-time data for the RER B (Paris), stores the history and reports on punctuality,
cancellations and disruptions. Work in progress; see [PLAN.md](PLAN.md).

## Development

Requirements: `uv`, Node.js 22, Docker.

```sh
make setup   # install dependencies and git hooks
make up      # start PostgreSQL and Redis
make test    # backend and frontend tests
make lint    # linters and type checks
make down    # stop PostgreSQL and Redis
```

Copy `.env.example` to `.env` to change local settings. Never commit `.env`.
