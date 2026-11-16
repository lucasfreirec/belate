# RER B Reliability — project plan

A service that continuously collects **real-time data for the RER B** (Paris), stores the
history, and answers the questions every RER B commuter asks:

- *How punctual is the RER B, by branch, direction, day and hour?*
- *Where on the line do trains lose time?* (for example around Gare du Nord)
- *How many trains are cancelled, and when?*
- *At my station, how long do I really wait for a train at 8 am versus 10 am?*
- *Is the line disrupted right now?*

It also lets a user subscribe to their commute (station + direction + time window) and get an
alert when there is a disruption or unusual delays.

**Goal:** a portfolio project for software engineering / backend / infrastructure internships.
It must show the full lifecycle that most job postings ask for: **build, test, ship, run and
monitor** a service, with **Python/FastAPI, PostgreSQL, background workers, an external API
with quotas, Docker, Kubernetes, CI/CD, Prometheus/Grafana and a cloud deployment**. Focusing
on one line I use every day keeps the scope realistic and lets the analysis go deeper.

The project name is a placeholder. Pick a better one in M0.

---

## Data source

Île-de-France Mobilités' **PRIM** portal (prim.iledefrance-mobilites.fr). My account's daily
quotas (from the "My API usage" page):

| API | Use in this project | Daily quota |
|---|---|---|
| Next Departures – **global query** (`GET /estimated-timetable`) | Real-time times for all covered stops in one call; filter to RER B | 1,000 |
| Next Departures – **unitary request** (`GET /stop-monitoring/{MonitoringRef}`) | Extra polling of *my* station during commute hours | 1,000 |
| **Traffic Info Messages – Global Query** | Disruptions on the RER B | 1,000 |
| Messages displayed on the screens (`GET /general-message`) | Backup / comparison for disruption messages | 1,000 |
| Stops and lines referentials (ICAR, ILICO) | Station and line reference data (rarely called) | 100 / 10 |

Each API has its **own** quota, so the budgets add up. The default plan:

- **Global query every 100 seconds, during service hours only** (about 04:45 to 01:30; M1
  confirms the window): about 750 calls per day, leaving about 250 in reserve for retries.
  Each call returns every covered stop, so the whole RER B is captured at once. No polling
  while the line is closed.
- **Unitary request for my station** (for example Cité Universitaire) **every minute during
  the morning and evening commute** (for example 7–10 am and 5–8 pm): 360 calls per day if one
  call covers both directions, 720 if each direction needs its own call (M1 confirms).
- **Traffic info every 5 minutes**, all day: 288 calls per day.
- Static timetables (GTFS) come from the IDFM open data portal as a file download, not from a
  quota'd API. They are used only as **reference data** (station order, branches), never to
  compute delays.
- Ask for a **quota increase** on the global and unitary APIs from the portal. The plan must
  work without it.

Constraints to design around:
- A free account and an **API key** are required.
- The system must **never exceed any quota**, even with several workers and retries.
- The global response covers the whole network and may be **large**. Parse it as a stream if
  M1 shows the need, filter to the RER B as early as possible, and never store the full
  response.
- Real-time coverage is still **partial** on the network. M1 must confirm that the **RER B**
  (both the RATP and SNCF parts of the line) is in the real-time perimeter.
- **Check the license and terms of use** of each dataset, and put the required attribution in
  the README.

What makes the RER B interesting to analyse:
- **Two branches at each end**: Aéroport CDG 2 or Mitry-Claye in the north, Robinson or
  Saint-Rémy-lès-Chevreuse in the south. Punctuality can be compared per branch.
- **Two operators**: SNCF north of Gare du Nord, RATP south of it.
- **Missions**: trains have mission codes and different stopping patterns (all stations
  versus semi-direct). M1 checks whether the data exposes them.
- **The same train appears at many stations.** If the data gives a stable vehicle journey ID,
  a train can be **followed along the line**, which shows *where* delays appear and grow.
- **Cancellations** and short-turned trains must be detected and counted.

---

## Definitions and rules

These are the defaults. M1 checks them against the real data and records the final versions
in `docs/data.md`.

- **The API only gives predictions**, never an observed departure time. The project uses the
  **last expected time seen** before a train leaves as its actual departure time.
- **Departure row**: one row per (vehicle journey, station). It stores the aimed time, the
  first and last expected times, when it was first and last seen, and its status. The
  project does **not** store one row per poll.
- **Final**: a departure becomes final when it is absent from the feed and its last expected
  time is in the past.
- **Cancelled**: a departure is cancelled when the feed flags it as cancelled, or when it is
  absent from two consecutive polls while its last expected time is still more than
  5 minutes in the future. A train that stops short of its planned terminus counts as
  cancelled at the stations it skips.
- **Delay** = last expected time − aimed time, at each station. If M1 shows that aimed times
  are missing, delay metrics are **dropped** and the project keeps cancellations and waiting
  time. There is no attempt to match trains to the static GTFS timetable.
- **Waiting time**: for the gaps *h* between consecutive trains at a station in one
  direction, the average wait of a passenger arriving at a random moment is
  Σh² / (2 · Σh). This is not half the average gap.
- **Time**: store every timestamp in UTC. Aggregate by hour and day in `Europe/Paris`. A
  train running after midnight belongs to the previous **service day**.
- **Two feeds, one row**: the global and unitary queries both update the same departure rows
  at my station. The comparison between the two (1-minute versus 100-second polling) is used
  to measure how accurate the global-only data is on the rest of the line.

---

## Working agreement (for Claude Code)

- Work **one milestone at a time**. At the end of each milestone, stop, summarise what
  changed and what I should check, and wait for my review.
- **Tests first** for core logic. Every milestone ends with `make test` and `make lint`
  passing locally and in CI.
- **Some parts are mine to write** (marked ✍️), because I will be asked about them in
  interviews. For those, scaffold the files, write the tests and explain the design options,
  then let me implement. Review my code and point out bugs instead of rewriting it.
- Keep it simple and readable. Every new dependency needs a one-line reason in the commit
  message.
- Small commits with conventional messages (`feat:`, `fix:`, `test:`, `docs:`, `chore:`,
  `ci:`, `infra:`).
- **Never commit the API key** or any secret. Use `.env` (git-ignored) locally, Kubernetes
  Secrets in the cluster, and provide `.env.example`.
- Tests must never call the real PRIM API. Use recorded fixtures from M1.
- If a decision is not covered here, propose 2 options with trade-offs and ask.

---

## Stack

| Layer          | Choice                                              | Why                                                  |
|----------------|-----------------------------------------------------|------------------------------------------------------|
| Language       | Python 3.12, managed with `uv`                      | Most common backend language in my target postings   |
| API            | FastAPI + Pydantic v2                               | Typed, OpenAPI docs for free                         |
| Database       | PostgreSQL 16, SQLAlchemy 2.0 (**sync** sessions in both the API and the workers), Alembic | Asked for in almost every posting; one session style everywhere |
| Time series    | Plain PostgreSQL tables with good indexes           | One row per departure is small; no partitioning or TimescaleDB unless M3 measurements show the need |
| Workers        | Celery + Redis (worker + beat scheduler), with `celery-types` for mypy | Standard pattern for periodic background jobs |
| HTTP client    | `httpx` with timeouts, retries and backoff          | Robust calls to a rate-limited external API          |
| Frontend       | React + TypeScript + Vite, a chart library           | Small, focused UI to show the results               |
| Observability  | `prometheus-client`, Prometheus, Grafana, Alertmanager, structured JSON logs | The "run it" part        |
| Containers     | Docker (multi-stage), Docker Compose                | Local dev in one command                             |
| Orchestration  | Kubernetes manifests with Kustomize, tested on minikube | Asked for in most backend / infra postings       |
| CI/CD          | GitHub Actions, images on GHCR                      | Tests, build and deploy on every push / tag          |
| Cloud          | One cloud VM with about 4 GB of RAM (most free tiers are too small; check the provider) | Runs the collector from M2, then k3s in M8 |
| Quality        | pytest, testcontainers, ruff, mypy (strict), Vitest | Integration tests against a real PostgreSQL          |

---

## Repository layout

```
rer-b/
├── app/
│   ├── api/            # FastAPI routers: stations, punctuality, segments, cancellations, disruptions, subscriptions, health
│   ├── core/           # config, logging, metrics
│   ├── db/             # models, session, repositories
│   ├── prim/           # PRIM client, SIRI Lite parsing, quota guard
│   ├── ingest/         # polling tasks, departure lifecycle (dedup, final, cancelled), train tracking
│   ├── analytics/      # delays, cancellations, waiting times, segment analysis
│   ├── alerts/         # subscription matching, notification senders
│   ├── worker/         # Celery app, beat schedule
│   └── main.py
├── frontend/           # React + TypeScript
├── migrations/
├── tests/
│   ├── fixtures/       # recorded PRIM responses, trimmed to the RER B, API key removed
│   ├── unit/
│   └── integration/
├── deploy/
│   ├── docker/
│   ├── compose/        # docker-compose.yml + prometheus/grafana/alertmanager config
│   └── k8s/            # base/ and overlays/ (dev = minikube, prod = cloud)
├── docs/
│   ├── data.md         # what the PRIM data contains for the RER B, quotas, definitions
│   ├── architecture.md
│   └── runbook.md
├── .github/workflows/
├── Makefile
└── README.md
```

---

## Milestones

**Cut line:** M0 to M4, with the collector running in the cloud since M2, is already a
complete portfolio project. M5 to M8 add to it; none of them should delay data collection.

### M0 — Repository setup
- `git init`, `.gitignore`, and a GitHub repository (I create it and add the remote).
- `uv` project, ruff, mypy `--strict`, pytest, pre-commit; Vite React-TS app in `frontend/`.
- A minimal `docker-compose.yml` with **PostgreSQL and Redis** for local development.
- `Makefile` targets: `setup`, `test`, `lint`, `up`, `down`.
- GitHub Actions `ci.yml`: lint, type check and tests for backend and frontend.
- Pick the project name.
- **Done when:** CI is green on trivial tests on both sides, and `make up` starts PostgreSQL
  and Redis.

### M1 — Data discovery (RER B)
- Claude writes a small exploration script; I run it with my API key.
- Call the **global** `estimated-timetable`, the **unitary** request for my station, and the
  traffic info API a few times, at different hours (rush hour, off-peak, and around the
  first and last trains).
- **Save fixtures** in `tests/fixtures/`: trimmed to the RER B, small enough to commit, with
  the API key removed from URLs and headers.
- Measure the size and parsing time of the global response, and decide whether streaming
  parsing is needed.
- Confirm and document in `docs/data.md`:
  - that the RER B is covered, and which **stations** appear (all ~47, or only some)
  - whether responses contain **aimed** (scheduled) times, **expected** (real-time) times, or
    both
  - whether there is a stable **vehicle journey ID** that lets me follow one train across
    stations, **including across Gare du Nord**, where the operator changes
  - whether the global and unitary queries use the **same** journey IDs
  - whether **mission codes**, **destinations** and **cancellation** status are present
  - how the line, branches and directions are identified (line reference, stop references)
  - whether one unitary call covers both directions at my station
  - the real **service hours** of the line, for the polling window
  - **when the daily quota resets**, and whether failed calls count against it
  - how many departure rows one day produces
  - the final **polling budget** for each API
- Check every rule in "Definitions and rules" against the data and record the final versions
  in `docs/data.md`. If aimed times are missing, apply the fallback written there.
- **Done when:** `docs/data.md` answers these questions and I have approved the definitions
  and the polling budget.

### M2 — Ingestion and first deployment ✍️
- `prim/client.py`: a PRIM client with timeouts, retries with exponential backoff on 5xx and
  429, and the API key from config.
- ✍️ **I implement the quota guard**: per API, an atomic daily counter in Redis that resets
  when the quota resets, refuses calls once the budget is used, and keeps a reserve for
  retries; plus a minimum interval between calls. It must stay correct with several workers.
- ✍️ **I implement the departure lifecycle**: each poll updates one *departure* row (keyed by
  vehicle journey + station) instead of creating duplicates, and applies the **final** and
  **cancelled** rules. Both feeds (global and unitary) go through the same code.
- Parser for SIRI Lite into clean internal objects, filtered to the **RER B only**,
  unit-tested on the M1 fixtures, including missing fields and malformed responses.
- Celery beat schedules: global query every 100 seconds, unitary query for my station during
  commute hours, traffic info every 5 minutes. The polling tasks do nothing outside service
  hours.
- A `status` command that prints the age of the latest poll and the quota used today per API.
- **Start collecting for real**: a simple Dockerfile and a Compose stack (worker, beat,
  PostgreSQL, Redis) running on the cloud VM, with a nightly `pg_dump` kept off the VM. The
  history collected from here on feeds every later milestone, so it must not be lost.
- **Done when:** the stack has run on the VM for a full day, the database holds clean,
  deduplicated RER B departures, every quota counter stayed under its limit, and a PRIM
  outage only causes retries and a log line, not a crash.

### M3 — RER B analytics ✍️
Core (required):
- **Delay per departure** at each station, as defined above.
- **Cancellations**, as defined above. Document the limits of the detection rule.
- ✍️ **I write the aggregate queries**: by branch, direction, station, weekday/weekend and
  hour (average and p90 delay, share of trains more than 5 minutes late, cancellation rate).
- Store traffic info messages as **disruptions** with start/end times.
- **Measurement error**: at my station, compare the departure times obtained from the global
  feed alone with those from the 1-minute unitary feed, and report the difference.

Second tier (only if M1 showed the data supports it):
- **Train tracking**: follow each train along the line and compute the delay at each
  station, to find the **segments where delay is gained**.
- **Waiting time at a station** by hour, with the formula above.

Retention: keep departure rows and aggregates indefinitely (they are small); delete
disruption message bodies after 90 days if they take space.

- **Done when:** tests with fixture data produce the expected results for each metric, and
  the queries behind the API run in under 100 ms on the data collected since M2 (measure it).

### M4 — API and commute alerts
- Endpoints:
  - `GET /stations`
  - `GET /punctuality?branch=&direction=&station=&window=7d`
  - `GET /segments?direction=&window=7d` (where delay is gained; only if tracking was built)
  - `GET /cancellations?window=7d`
  - `GET /waiting-time?station=&direction=` (only if waiting time was built)
  - `GET /disruptions?active=true`
  - `GET /live?station=&direction=` (next trains from the latest data, with an "as of" time)
  - `POST/GET/DELETE /subscriptions` (station + direction + days + time window + channel)
- **Security**, because the API will be public:
  - reading is open; **creating, listing and deleting subscriptions requires an API token**
  - webhook URLs must be `https` and must not resolve to a private, loopback or link-local
    address (checked when the subscription is created and again before each send)
  - rate limiting on all public endpoints
- Alert logic, during the subscription window. Default thresholds, to tune on real data:
  - **disruption**: an active traffic info message on the RER B
  - **delays**: the median delay of the last 30 minutes at the station and direction is above
    both 5 minutes and the usual p90 for that hour, with at least 3 trains observed
  - **cancellations**: 2 or more cancelled trains in the last 30 minutes
  - until 2 weeks of history exist, only the fixed thresholds apply
- **One alert per incident**: a disruption is identified by its message ID; a delay or
  cancellation incident stays open until the situation has been normal for 30 minutes.
- Notification senders behind a small interface: webhook first, then e-mail or a chat bot if I
  want.
- **Done when:** integration tests cover the endpoints, the token check, the webhook URL
  validation, and the alert logic, including "don't notify twice for the same incident".

### M5 — Frontend
- **Line view**: a diagram of the RER B with its stations and branches, colored by average
  delay (or delay gained per segment, if tracking was built), with a selector for direction
  and hour.
- **Station view**: next trains (live), punctuality history, waiting time by hour if built.
- **Insights page**: the week's numbers (punctuality per branch, worst hours, cancellations).
- **"My commute" form** that creates a subscription (asks for the API token).
- Loading, empty and error states handled properly.
- **Done when:** Vitest covers the API client and data formatting, and the app works end to
  end against the local API.

### M6 — Observability
- `/metrics` with `prometheus-client`:
  - PRIM requests by API and status, latency, and **quota used today per API**
  - ingestion freshness (age of the latest RER B observation)
  - a `polling_active` gauge (1 during service hours, 0 when the line is closed)
  - parse errors, deduplicated vs new departures, detected cancellations
  - API request count and latency
- Structured JSON logs with request ID / task ID.
- Provisioned **Grafana dashboards**: ingestion health, quota usage, API latency, and a
  "RER B today" view.
- Alertmanager rules: ingestion stale for 10 minutes **while `polling_active` is 1**, any
  quota above 90%, error rate high.
- **Done when:** stopping the workers during service hours fires the "stale ingestion" alert,
  restarting them resolves it, and no alert fires overnight while the line is closed.

### M7 — Containers and Kubernetes
- Multi-stage Dockerfiles (non-root), one image for API and workers with different commands,
  one for the frontend.
- `docker compose up` starts everything: api, worker, beat, frontend, postgres, redis,
  prometheus, grafana, alertmanager.
- Kustomize `base`:
  - Deployments for api, frontend, workers (**2 replicas**, to prove the quota guard and the
    departure lifecycle are safe under concurrency) and beat (**exactly 1 replica**)
  - PostgreSQL as a **StatefulSet with a PersistentVolumeClaim**
  - an **Alembic migration Job** that runs before the new version starts
  - a **nightly `pg_dump` CronJob**
  - Services, ConfigMap, Secret (API key, API token), probes, resource requests and limits
  - no autoscaler: throughput is capped by the PRIM quota, not by CPU
- `overlays/dev` for minikube, `overlays/prod` for the cloud.
- **Done when:** `make up` and `make k8s-up` both run the full system, deleting a worker pod
  doesn't stop ingestion, and a restore from a `pg_dump` backup has been tested once.

### M8 — CI/CD, cloud deployment and polish
- `release.yml` on git tag: tests, build and push images to GHCR, deploy to the cloud cluster.
- Move the VM from the M2 Compose stack to **k3s**, with ingress and HTTPS. **Migrate the
  collected history** (dump and restore) and keep the gap in collection as short as possible.
- Check the memory budget of the full stack on the VM; reduce Prometheus retention or drop
  Alertmanager in prod if it doesn't fit.
- Public URL for the frontend and the API docs. Grafana stays behind a login or is not
  exposed.
- `docs/runbook.md`: deploy, roll back, rotate the API key, restore the database, debug "no
  new data", handle quota exhaustion.
- README: one-line pitch, **live demo link**, screenshots, architecture diagram, **two or three
  findings from the data** (for example "trains towards CDG lose most of their delay between X
  and Y"), the measured accuracy of the data, quick start, data attribution, what I'd do next.
- **Done when:** someone can open the live site, read the README and understand the project in
  two minutes.

### Stretch goals (only after M8)
- **Weekly report**: an automatically generated summary of the RER B's week.
- **Simple delay prediction** for a station and hour, compared with a naive baseline.
- **Adaptive polling**: more frequent calls during rush hour, fewer off-peak, same daily total.
- **Load test** of the API (k6 or Locust) and a short performance write-up.
- **Terraform** for the cloud VM and DNS.
- **Other lines** (RER A, Metro 13), since the global query already returns them.

---

## First prompt to give Claude Code

> Read `PLAN.md`. Follow the working agreement. Start with **M0 — Repository setup** only.
> When M0 is done and CI passes, stop and summarise what you did and what I should check.
