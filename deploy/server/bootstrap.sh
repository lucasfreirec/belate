#!/usr/bin/env bash
# Prepare a fresh Ubuntu server to run Belate. Run once, as root, on the server:
#
#   scp deploy/server/bootstrap.sh deploy/server/cluster-issuer.yaml root@SERVER:
#   ssh root@SERVER 'bash bootstrap.sh "ssh-ed25519 AAAA... github-actions-deploy"'
#
# The argument is the PUBLIC key the release workflow uses to deploy (its private half goes
# into the DEPLOY_SSH_KEY secret). Safe to run again. See docs/runbook.md, "Set up the server".
#
# Installs k3s (Kubernetes with the Traefik ingress), cert-manager with a Let's Encrypt
# issuer, and a `deploy` user that can run kubectl and nothing else as an administrator.
set -euo pipefail

K3S_VERSION=v1.37.2+k3s1
CERT_MANAGER_VERSION=v1.21.3

deploy_key=${1:-}
if [[ $(id -u) -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi
if [[ ! $deploy_key =~ ^ssh-(ed25519|rsa)\  ]]; then
  echo "Usage: bash bootstrap.sh \"ssh-ed25519 AAAA... comment\"" >&2
  exit 1
fi
here=$(cd "$(dirname "$0")" && pwd)

echo "== k3s $K3S_VERSION"
if ! command -v k3s >/dev/null; then
  # The official installer, pinned to a version. It sets up k3s as a systemd service.
  curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=$K3S_VERSION sh -s - \
    --write-kubeconfig-mode 600
fi
until kubectl get nodes >/dev/null 2>&1; do sleep 2; done
kubectl wait --for=condition=Ready node --all --timeout=5m

echo "== cert-manager $CERT_MANAGER_VERSION"
kubectl apply -f "https://github.com/cert-manager/cert-manager/releases/download/$CERT_MANAGER_VERSION/cert-manager.yaml"
kubectl -n cert-manager rollout status deployment/cert-manager-webhook --timeout=5m
# The webhook can take a few more seconds to accept requests after it is ready.
until kubectl apply -f "$here/cluster-issuer.yaml"; do sleep 5; done

echo "== deploy user"
id deploy >/dev/null 2>&1 || useradd --create-home --shell /bin/bash deploy
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh /home/deploy/.kube
printf '%s\n' "$deploy_key" > /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys
# The cluster's admin credentials, readable by `deploy` only.
install -m 600 -o deploy -g deploy /etc/rancher/k3s/k3s.yaml /home/deploy/.kube/config

echo
echo "Done. Next, from your machine (see docs/runbook.md):"
echo "  KUBECTL='ssh deploy@SERVER kubectl' deploy/k8s/create-secrets.sh server.env"
