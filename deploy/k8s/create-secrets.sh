#!/usr/bin/env bash
# Create or update the secrets of the cluster from an env file, so that they never sit in a
# file of the repository.
#
#   deploy/k8s/create-secrets.sh [env-file]        (default: .env)
#
# Reads PRIM_API_KEY and POSTGRES_PASSWORD (required), API_TOKEN (optional: without it the
# subscription endpoints are closed) and GRAFANA_PASSWORD (default "admin", for local use
# only). Set KUBECTL to choose the cluster, for example KUBECTL="kubectl --context minikube",
# or KUBECTL="ssh deploy@SERVER kubectl" for the server. The secrets are built on this machine
# and sent on standard input, never on a command line.
set -euo pipefail

env_file=${1:-.env}
kubectl=(${KUBECTL:-kubectl})
namespace=belate

if [[ ! -f $env_file ]]; then
  echo "No $env_file. Copy .env.example to .env and fill it in." >&2
  exit 1
fi

# Read KEY=value lines without executing the file.
value() {
  local line
  line=$(grep -E "^$1=" "$env_file" | tail -n 1 || true)
  printf '%s' "${line#*=}"
}

prim_api_key=$(value PRIM_API_KEY)
postgres_password=$(value POSTGRES_PASSWORD)
api_token=$(value API_TOKEN)
grafana_password=$(value GRAFANA_PASSWORD)

if [[ -z $prim_api_key || -z $postgres_password ]]; then
  echo "PRIM_API_KEY and POSTGRES_PASSWORD must be set in $env_file." >&2
  exit 1
fi
# The password goes into a database URL; keep it to characters that need no escaping.
if [[ ! $postgres_password =~ ^[A-Za-z0-9._~-]+$ ]]; then
  echo "POSTGRES_PASSWORD may only contain letters, digits and . _ ~ -" >&2
  echo "Generate one with: openssl rand -hex 24" >&2
  exit 1
fi
if [[ -z $grafana_password ]]; then
  echo "GRAFANA_PASSWORD is not set: using 'admin'. Set it for anything but local testing." >&2
  grafana_password=admin
fi

# Secrets are written to private temporary files rather than passed on the command line,
# where other users of the machine could see them.
workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT
umask 077

apply_secret() {
  local name=$1 file=$2
  # Built by the local kubectl (no cluster needed), applied by the chosen one.
  kubectl -n "$namespace" create secret generic "$name" \
    --from-env-file="$file" --dry-run=client -o yaml | "${kubectl[@]}" apply -f - >/dev/null
  echo "secret/$name updated"
}

"${kubectl[@]}" apply -f - >/dev/null < "$(dirname "$0")/base/namespace.yaml"

{
  echo "PRIM_API_KEY=$prim_api_key"
  echo "DATABASE_URL=postgresql+psycopg://belate:$postgres_password@postgres:5432/belate"
  echo "API_TOKEN=$api_token"
} > "$workdir/app.env"
apply_secret belate-app "$workdir/app.env"

echo "POSTGRES_PASSWORD=$postgres_password" > "$workdir/db.env"
apply_secret belate-db "$workdir/db.env"

echo "GRAFANA_PASSWORD=$grafana_password" > "$workdir/grafana.env"
apply_secret belate-grafana "$workdir/grafana.env"
