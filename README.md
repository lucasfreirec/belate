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

## API

`make api` serves it on http://localhost:8000, with interactive documentation at `/docs`.

| Endpoint | What it returns |
|---|---|
| `GET /stations` | The 47 stations, in line order |
| `GET /punctuality?window=7d&group_by=branch&group_by=hour` | Delay and cancellation figures, broken down by `branch`, `direction`, `station`, `day_type`, `hour` or `service_date`, and filtered by `direction`, `station` or `branch` |
| `GET /segments?direction=southbound` | Where trains lose or recover time between stops |
| `GET /cancellations` | Cancelled departures, in total and per day |
| `GET /waiting-time?station=...&direction=...` | Average wait by hour at a station |
| `GET /disruptions?active=true` | Disruptions PRIM publishes for the line |
| `GET /live?station=...` | Next trains at a station, with the time of the data |
| `POST`, `GET`, `DELETE /subscriptions` | Commute alerts, sent to a webhook |

`window` is a number of days or hours (`7d`, `24h`), up to 31 days.

Reading is open and rate limited per client address. Subscriptions require
`Authorization: Bearer <API_TOKEN>`; without an `API_TOKEN` in the configuration they are
closed. A subscription watches one station and direction on chosen weekdays, in a Paris-time
window, and notifies an `https` webhook once per incident:

- a disruption published by PRIM that applies now to the line or the station
- trains running well above the usual delay: the median of the last 30 minutes is over
  5 minutes and over the usual 90th percentile for that hour, with at least 3 trains
- 2 or more trains cancelled in the last 30 minutes

Webhook URLs must resolve to public addresses only. This is checked when the subscription is
created and before every notification, and redirects are not followed.

## Frontend

`make web` serves it on http://localhost:5173 and sends its `/api` requests to `make api`.

- **Overview**: the period's headline figures, delay by hour and by destination,
  cancellations per day, and the segments where trains lose the most time.
- **Line**: a diagram of the line with each station coloured by its average delay, by
  direction and hour.
- **Station**: the next trains, and the average wait and delay by hour.
- **My commute**: create and delete alerts (needs the API token).

Every chart has a table view, and every value shown on hover is also reachable with the
keyboard.

With no collected history the pages are empty. `make demo-data` fills a separate database
with 30 invented days; point the API at it to see the pages with data:

```sh
make demo-data
DATABASE_URL=postgresql+psycopg://belate:belate@localhost:5432/belate_bench make api
```

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
