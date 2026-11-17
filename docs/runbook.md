# Runbook

How to run and look after Belate in production: a single server running k3s, deployed by
the release workflow. The collector polls PRIM around the clock and stores the history in
PostgreSQL; everything else reads that history.

Commands that start with `ssh deploy@SERVER` run on the server as the `deploy` user, which
the bootstrap creates and which is allowed to run `kubectl`.

## Set up the server

Once, on a new machine.

1. **A server**: Ubuntu 24.04 or later, 4 GB of memory (the system requests 1.3 GB and can
   use up to 4.1 GB at worst; k3s itself takes about 0.7 GB), 2 CPUs, 20 GB of disk, x86 or
   ARM. Ports 22, 80 and 443 open to the internet; 6443 closed.
2. **A host name** pointing at its address (an `A` record), for example
   `belate.example.org`. Let's Encrypt needs it to issue the HTTPS certificate.
3. **A deploy key** for the release workflow, on your machine:

   ```sh
   ssh-keygen -t ed25519 -N "" -C github-actions-deploy -f belate-deploy
   ```

4. **Bootstrap** (installs k3s, cert-manager with a Let's Encrypt issuer, and the `deploy`
   user with the public key):

   ```sh
   scp deploy/server/bootstrap.sh deploy/server/cluster-issuer.yaml root@SERVER:
   ssh root@SERVER "bash bootstrap.sh '$(cat belate-deploy.pub)'"
   ```

5. **Secrets**: copy `.env.example` to `.env.server` (git-ignored), set `PRIM_API_KEY` and
   strong values for `POSTGRES_PASSWORD`, `API_TOKEN` and `GRAFANA_PASSWORD`
   (`openssl rand -hex 24` each), then:

   ```sh
   KUBECTL="ssh deploy@SERVER kubectl" deploy/k8s/create-secrets.sh .env.server
   ```

   Do not run a collector elsewhere with the same PRIM key: both would share its quota.

6. **GitHub** (repository Settings): variables `DEPLOY_HOST` (the server's address) and
   `PUBLIC_HOST` (the host name); secrets `DEPLOY_SSH_KEY` (contents of `belate-deploy`) and
   `DEPLOY_KNOWN_HOSTS` (output of `ssh-keyscan SERVER`). Then delete `belate-deploy` from
   your machine.

7. **First release**: `git tag v0.1.0 && git push origin v0.1.0`. After it, make the two
   packages `belate-app` and `belate-web` public on GitHub (Packages, Package settings),
   or the server cannot pull them, and re-run the deploy job if it failed for that reason.

## Release and roll back

A release is a version tag. The `Release` workflow runs the checks, builds both images for
x86 and ARM, pushes them to GHCR as `ghcr.io/lucasfreirec/belate-app:TAG` and `belate-web`,
applies the manifests of that tag on the server, waits for the new pods and checks
`https://HOST/api/ready`.

```sh
git tag v0.2.0 && git push origin v0.2.0
```

**Roll back**: Actions, Release, Run workflow, with the previous tag. It deploys the images
already published for that tag, without rebuilding. Without GitHub:

```sh
deploy/k8s/render-release.sh v0.1.0 belate.example.org | ssh deploy@SERVER kubectl apply -f -
```

Migrations are not undone by a rollback: each pod upgrades the database before starting,
and nothing downgrades it. Keep migrations additive (new tables and columns), so the
previous version still works with the newer schema.

**Watch it**:

```sh
ssh deploy@SERVER kubectl -n belate get pods
ssh deploy@SERVER kubectl -n belate logs deployment/worker -f
ssh deploy@SERVER kubectl -n belate exec deployment/worker -- python -m app.status
```

**Grafana and Prometheus** are not published. Through an SSH tunnel:

```sh
ssh -L 3000:localhost:3000 deploy@SERVER kubectl -n belate port-forward service/grafana 3000:3000
```

then open http://localhost:3000 (user `admin`, the `GRAFANA_PASSWORD` of `.env.server`).
Replace `grafana 3000:3000` by `prometheus 9090:9090` for Prometheus.

## Alternative: the collector alone with Docker Compose

Before k3s, or on a machine without it, the collector can run alone (no API, frontend or
monitoring). Requirements: a Linux server with Docker and the Compose plugin, about 2 GB of free memory,
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
Latest departure data: 120 s ago
Departures: 2642 open, 44 departed, 22 cancelled, 0 unknown
Disruptions recorded: 4
Quota used today (UTC):
  global          16 / 1000
  unitary          0 / 1000
  traffic_info     3 / 1000
```

## Alerts

Prometheus shows these on its Alerts page. What to do for each:

- **IngestionStale**: no global poll has stored data for 10 minutes. Follow "No new data"
  below.
- **CollectorSilent**: the API exports no poll at all. Either nothing was ever collected, or
  the API cannot reach Redis. Check that Redis and the API are running.
- **QuotaNearlyUsed**: an API's daily quota is over 90% used. Polling stops at the limit and
  resumes at midnight UTC. Look for another program using the same API key, and for
  `TooSoon` or repeated retries on the dashboard.
- **PrimFailing**: most requests to PRIM fail. Usually PRIM itself; polling retries on its
  own. If it lasts, check the key and the PRIM status page.

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

## Kubernetes

The same system runs on Kubernetes from `deploy/k8s/`. On minikube, `make k8s-up` does every
step below; on another cluster, run them with that cluster's context.

```sh
deploy/k8s/create-secrets.sh .env          # secrets from the env file
kubectl apply -k deploy/k8s/overlays/prod  # everything else
kubectl -n belate get pods
```

Every application pod runs `alembic upgrade head` in an init container before it starts, so
a new version migrates the database by itself. Migrations must keep working with the
previous version of the code, which keeps running until the new pods are ready.

Common tasks:

- **Logs**: `kubectl -n belate logs deployment/worker -f` (also `api`, `beat`, `web`).
- **Status**: `kubectl -n belate exec deployment/worker -- python -m app.status`.
- **Restart one part**: `kubectl -n belate rollout restart deployment/worker`. Restarting
  `beat` loses one poll.
- **Change a secret**: edit the env file, run `create-secrets.sh` again, then restart the
  deployments that use it.
- **Grafana and Prometheus** are not published:
  `kubectl -n belate port-forward service/grafana 3000:3000`.

### Backups on Kubernetes

The `backup` CronJob dumps the database every night at 04:30 Paris time onto the `backups`
volume and keeps 14 days.

```sh
deploy/k8s/backup.sh now                          # take a dump now
deploy/k8s/backup.sh list                         # list the dumps
deploy/k8s/backup.sh restore FILE belate_check    # load a dump into a new database
```

`restore` refuses a database that already exists. Restoring into a scratch database is how
to check that a dump is usable. To replace the live database:

```sh
kubectl -n belate scale deployment/api deployment/worker deployment/beat --replicas=0
kubectl -n belate exec postgres-0 -- dropdb -U belate belate
deploy/k8s/backup.sh restore FILE belate
kubectl -n belate scale deployment/api deployment/beat --replicas=1
kubectl -n belate scale deployment/worker --replicas=2
```

## Rotate the API key

Generate a new key on the PRIM portal and replace `PRIM_API_KEY` in `.env.server`. Then:

```sh
KUBECTL="ssh deploy@SERVER kubectl" deploy/k8s/create-secrets.sh .env.server
ssh deploy@SERVER kubectl -n belate rollout restart deployment/worker deployment/beat
```

With the Compose collector: replace it in `.env`, then `make collector-up`.

## Quota used up

The `QuotaNearlyUsed` alert fires at 90%. At 100% the quota guard refuses every request and
polling stops until midnight UTC; nothing else breaks. Check the quota panel in Grafana and
`kubectl -n belate exec deployment/worker -- python -m app.status`. The usual causes are a
second program using the same key, or PRIM failing so that every poll is retried three
times (see the `PrimFailing` alert).
