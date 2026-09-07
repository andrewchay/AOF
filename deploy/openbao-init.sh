#!/usr/bin/env sh
# W02.03: provision the OpenBao transit engine + ed25519 anchor key.
# Idempotent. Requires the openbao container running (dev mode).
set -eu
exec docker exec \
  -e BAO_ADDR=http://127.0.0.1:8200 \
  -e BAO_TOKEN=aof-dev-root-token \
  aof-infra-openbao-1 sh -c '
    bao secrets list 2>/dev/null | grep -q "^transit" || bao secrets enable transit
    bao read -format=json transit/keys/aof-anchor 2>/dev/null | grep -q ed25519 || \
      bao write -f transit/keys/aof-anchor type=ed25519 >/dev/null
    echo "openbao transit ready (key=aof-anchor, type=ed25519)"
  '
