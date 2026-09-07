#!/usr/bin/env sh
# W01.02: provision the AOF Keycloak realm, client, roles and test users.
# Idempotent. Runs kcadm INSIDE the keycloak container (dev mode,
# admin/kc-dev-only).

set -eu

docker exec -i aof-infra-keycloak-1 sh -s << 'INNER'
set -eu
KC="/opt/keycloak/bin/kcadm.sh"
SERVER="http://localhost:8080"
$KC config credentials --server "$SERVER" --realm master --user admin --password kc-dev-only >/dev/null 2>&1

if ! $KC get realms/aof --server "$SERVER" >/dev/null 2>&1; then
  $KC create realms --server "$SERVER" -s realm=aof -s enabled=true -s displayName="AOF Governance"
  echo "realm aof created"
fi

if ! $KC get clients -r aof --server "$SERVER" -q clientId=aof-api 2>/dev/null | grep -q aof-api; then
  CLIENT_UUID=$($KC create clients -r aof --server "$SERVER" -i \
    -s clientId=aof-api -s enabled=true -s directAccessGrantsEnabled=true \
    -s secret=aof-dev-client-secret \
    -s 'redirectUris=["http://localhost:5173/*","http://localhost:5199/*"]')
  echo "client aof-api created"
fi

for role in admin owner editor reviewer publisher operator viewer analyst worker ingestor; do
  $KC create roles -r aof --server "$SERVER" -s name="$role" >/dev/null 2>&1 || true
done
echo "realm roles ensured"

for spec in "alice:admin:acme" "bob:viewer:acme" "carol:reviewer:acme" "dave:publisher:acme"; do
  username=${spec%%:*}; rest=${spec#*:}; role=${rest%%:*}; tenant=${rest#*:}
  if ! $KC get users -r aof --server "$SERVER" -q username="$username" 2>/dev/null | grep -q username; then
    $KC create users -r aof --server "$SERVER" \
      -s username="$username" -s enabled=true \
      -s 'attributes={"aof_tenant":["'"$tenant"'"]}'
    $KC set-password -r aof --username "$username" --password "$username-dev-pass"
    $KC add-roles -r aof --server "$SERVER" --uusername "$username" --rolename "$role"
    $KC add-roles -r aof --server "$SERVER" --uusername "$username" --rolename operator
    echo "user $username created ($role @ $tenant)"
  fi
done

CLIENT_UUID=$($KC get clients -r aof --server "$SERVER" -q clientId=aof-api | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['id'])" 2>/dev/null || \
  $KC get clients -r aof --server "$SERVER" -q clientId=aof-api | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
if ! $KC get "clients/$CLIENT_UUID/protocol-mappers/models" -r aof --server "$SERVER" 2>/dev/null | grep -q aof_tenant; then
  $KC create "clients/$CLIENT_UUID/protocol-mappers/models" -r aof --server "$SERVER" \
    -s name=aof-tenant -s protocol=openid-connect \
    -s protocolMapper=oidc-usermodel-attribute-mapper \
    -s 'config={"user.attribute":"aof_tenant","claim.name":"aof_tenant","jsonType.label":"String","access.token.claim":"true","id.token.claim":"true"}'
  echo "aof_tenant mapper created"
fi
echo "keycloak provisioning complete"
INNER
