#!/usr/bin/env bash
# Print the production manifests for one release, ready for `kubectl apply -f -`.
#
#   deploy/k8s/render-release.sh TAG HOST
#
# TAG is the image tag pushed by the release workflow (v0.1.0); HOST the public host name
# (belate.example.org). Used by the release workflow, for a manual deploy and to roll back:
#
#   deploy/k8s/render-release.sh v0.1.0 belate.example.org | ssh deploy@SERVER kubectl apply -f -
set -euo pipefail

tag=${1:-}
host=${2:-}
if [[ ! $tag =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "TAG must look like v1.2.3" >&2
  exit 1
fi
if [[ ! $host =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]]; then
  echo "HOST must be a host name such as belate.example.org" >&2
  exit 1
fi

root=$(cd "$(dirname "$0")/../.." && pwd)
# A throwaway overlay on top of overlays/prod. Kustomize only reads files under the
# repository, so it lives there; it is removed on exit and ignored by git.
work="$root/deploy/k8s/overlays/.release-$$"
mkdir -p "$work"
trap 'rm -rf "$work"' EXIT

cat > "$work/kustomization.yaml" <<YAML
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../prod
images:
  - name: ghcr.io/lucasfreirec/belate-app
    newTag: "$tag"
  - name: ghcr.io/lucasfreirec/belate-web
    newTag: "$tag"
patches:
  - target:
      kind: Ingress
      name: web
    patch: |
      - op: replace
        path: /spec/rules/0/host
        value: $host
      - op: replace
        path: /spec/tls/0/hosts/0
        value: $host
YAML

kubectl kustomize "$work"
