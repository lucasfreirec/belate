#!/usr/bin/env bash
# Database backups in the cluster.
#
#   deploy/k8s/backup.sh now                     take a dump now
#   deploy/k8s/backup.sh list                    list the dumps on the backups volume
#   deploy/k8s/backup.sh restore FILE DATABASE   load a dump into a new database
#
# restore refuses an existing database, so it cannot overwrite data by mistake. To replace
# the live database, follow "Restore a backup" in docs/runbook.md.
# Set KUBECTL to choose the cluster, for example KUBECTL="kubectl --context minikube".
set -euo pipefail

kubectl=(${KUBECTL:-kubectl} -n belate)

# Run a shell script in a one-off pod that has the backups volume and the database password.
run_job() {
  local name=$1 script=$2
  "${kubectl[@]}" apply -f - >/dev/null <<YAML
apiVersion: batch/v1
kind: Job
metadata:
  name: $name
  labels:
    app.kubernetes.io/part-of: belate
    app.kubernetes.io/component: backup
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 600
  template:
    spec:
      restartPolicy: Never
      securityContext:
        runAsNonRoot: true
        runAsUser: 999
        fsGroup: 999
      containers:
        - name: job
          image: postgres:16
          env:
            - name: PGPASSWORD
              valueFrom:
                secretKeyRef:
                  name: belate-db
                  key: POSTGRES_PASSWORD
          command:
            - bash
            - -c
            - |
              set -euo pipefail
$(printf '%s\n' "$script" | sed 's/^/              /')
          volumeMounts:
            - name: backups
              mountPath: /backups
      volumes:
        - name: backups
          persistentVolumeClaim:
            claimName: backups
YAML
  wait_for "$name"
}

wait_for() {
  local name=$1
  # Wait for either outcome, then show the output and report a failure.
  until "${kubectl[@]}" get job "$name" -o jsonpath='{.status.conditions[*].type}' |
    grep -qE 'Complete|Failed'; do
    sleep 2
  done
  "${kubectl[@]}" logs "job/$name"
  if ! "${kubectl[@]}" get job "$name" -o jsonpath='{.status.conditions[*].type}' |
    grep -q Complete; then
    echo "Job $name failed." >&2
    exit 1
  fi
}

case ${1:-} in
  now)
    name="backup-now-$(date +%s)"
    "${kubectl[@]}" create job "$name" --from=cronjob/backup >/dev/null
    wait_for "$name"
    ;;
  list)
    run_job "backup-list-$(date +%s)" "ls -lh /backups"
    ;;
  restore)
    file=${2:-}
    database=${3:-}
    # Both end up in a shell command: accept only the shapes they are expected to have.
    if [[ ! $file =~ ^belate-[0-9-]+\.sql\.gz$ ]]; then
      echo "FILE must be a dump name as shown by 'list', such as belate-2026-11-20-0430.sql.gz" >&2
      exit 1
    fi
    if [[ ! $database =~ ^[a-z][a-z0-9_]{0,62}$ ]]; then
      echo "DATABASE must be lower-case letters, digits and underscores." >&2
      exit 1
    fi
    run_job "backup-restore-$(date +%s)" "createdb -h postgres -U belate $database
gunzip -c /backups/$file | psql -h postgres -U belate -v ON_ERROR_STOP=1 -q $database
echo 'Restored $file into $database.'
psql -h postgres -U belate -At $database -c 'select count(*) from departures' | sed 's/$/ departures/'"
    ;;
  *)
    sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
