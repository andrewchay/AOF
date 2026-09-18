#!/usr/bin/env sh
# W01.02: provision the AOF Keycloak realm, client, roles and test users.
# Idempotent. Runs kcadm INSIDE the keycloak container (dev mode,
# admin/kc-dev-only).

set -eu

docker exec -i aof-infra-keycloak-1 sh -s << 'INNER'
set -eu
KC="/opt/keycloak/bin/kcadm.sh"
SERVER="http://localhost:8080"

# The compose service has no healthcheck, so `docker compose up --wait`
# returns while start-dev is still initializing; the first kcadm call then
# fails and `set -e` aborts the whole script with no diagnostics. Poll until
# the admin login actually succeeds (max ~120s).
READY=0
i=0
while [ "$i" -lt 60 ]; do
  if $KC config credentials --server "$SERVER" --realm master --user admin --password kc-dev-only >/dev/null 2>&1; then
    READY=1
    break
  fi
  i=$((i + 1))
  sleep 2
done
if [ "$READY" -ne 1 ]; then
  echo "ERROR: keycloak did not become ready at $SERVER within 120s" >&2
  exit 1
fi

if ! $KC get realms/aof --server "$SERVER" >/dev/null 2>&1; then
  $KC create realms --server "$SERVER" -s realm=aof -s enabled=true -s displayName="AOF Governance"
  echo "realm aof created"
fi

# Keycloak 26 enables the declarative user profile by default: user attributes
# not declared in the realm profile are silently dropped, and declared fields
# without edit permissions reject admin writes. Provision the profile so the
# aof_tenant claim below actually persists, and allow unmanaged attributes
# for ad-hoc extensions. Without this the OIDC token has no aof_tenant claim
# and tenant resolution falls back to the realm name.
USER_PROFILE='{"attributes":[{"name":"username","displayName":"${username}","validations":{"length":{"min":3,"max":255},"username-prohibited-characters":{},"up-username-not-idn-homograph":{}},"permissions":{"view":["admin","user"],"edit":["admin","user"]},"multivalued":false},{"name":"email","displayName":"${email}","validations":{"email":{},"length":{"max":255}},"required":{"roles":["user"]},"permissions":{"view":["admin","user"],"edit":["admin","user"]},"multivalued":false},{"name":"firstName","displayName":"${firstName}","validations":{"length":{"max":255},"person-name-prohibited-characters":{}},"permissions":{"view":["admin","user"],"edit":["admin","user"]},"multivalued":false},{"name":"lastName","displayName":"${lastName}","validations":{"length":{"max":255},"person-name-prohibited-characters":{}},"permissions":{"view":["admin","user"],"edit":["admin","user"]},"multivalued":false},{"name":"aof_tenant","displayName":"AOF Tenant","permissions":{"view":["admin","user"],"edit":["admin"]},"multivalued":true}],"unmanagedAttributePolicy":"ENABLED"}'
printf '%s' "$USER_PROFILE" | $KC update users/profile -r aof --server "$SERVER" -f - >/dev/null
echo "user profile declares aof_tenant"

if ! $KC get clients -r aof --server "$SERVER" -q clientId=aof-api 2>/dev/null | grep -q aof-api; then
  $KC create clients -r aof --server "$SERVER" \
    -s clientId=aof-api -s enabled=true -s directAccessGrantsEnabled=true \
    -s serviceAccountsEnabled=true -s secret=aof-api-secret \
    -s 'redirectUris=["http://localhost:5173/*","http://localhost:5199/*"]' >/dev/null
  echo "client aof-api created"
fi

# Keycloak's container does not include Python. Parse the normal kcadm JSON
# formatting with POSIX tools and fail before constructing an empty admin URL.
CLIENT_UUID=$($KC get clients -r aof --server "$SERVER" -q clientId=aof-api --fields id |
  sed -n 's/.*"id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
if [ -z "$CLIENT_UUID" ]; then
  echo "ERROR: aof-api client exists but its UUID could not be resolved" >&2
  exit 1
fi
$KC update "clients/$CLIENT_UUID" -r aof --server "$SERVER" \
  -s enabled=true -s directAccessGrantsEnabled=true -s serviceAccountsEnabled=true \
  -s secret=aof-api-secret \
  -s 'redirectUris=["http://localhost:5173/*","http://localhost:5199/*"]' >/dev/null
API_CLIENT_UUID=$CLIENT_UUID

if ! $KC get clients -r aof --server "$SERVER" -q clientId=aof-web 2>/dev/null | grep -q aof-web; then
  $KC create clients -r aof --server "$SERVER" \
    -s clientId=aof-web -s enabled=true -s publicClient=true \
    -s standardFlowEnabled=true -s directAccessGrantsEnabled=false \
    -s 'redirectUris=["http://localhost:5173/*","http://localhost:5199/*"]' \
    -s 'webOrigins=["http://localhost:5173","http://localhost:5199"]' \
    -s 'attributes={"pkce.code.challenge.method":"S256"}' >/dev/null
  echo "public PKCE client aof-web created"
fi
WEB_CLIENT_UUID=$($KC get clients -r aof --server "$SERVER" -q clientId=aof-web --fields id |
  sed -n 's/.*"id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
if [ -z "$WEB_CLIENT_UUID" ]; then
  echo "ERROR: aof-web client exists but its UUID could not be resolved" >&2
  exit 1
fi
$KC update "clients/$WEB_CLIENT_UUID" -r aof --server "$SERVER" \
  -s enabled=true -s publicClient=true -s standardFlowEnabled=true \
  -s directAccessGrantsEnabled=false \
  -s 'redirectUris=["http://localhost:5173/*","http://localhost:5199/*"]' \
  -s 'webOrigins=["http://localhost:5173","http://localhost:5199"]' \
  -s 'attributes={"pkce.code.challenge.method":"S256"}' >/dev/null

for role in admin owner editor reviewer publisher operator viewer analyst worker ingestor; do
  $KC create roles -r aof --server "$SERVER" -s name="$role" >/dev/null 2>&1 || true
done
echo "realm roles ensured"

for spec in "alice:admin:acme" "bob:viewer:acme" "carol:reviewer:acme" "dave:publisher:acme"; do
  username=${spec%%:*}; rest=${spec#*:}; role=${rest%%:*}; tenant=${rest#*:}
  if ! $KC get users -r aof --server "$SERVER" -q username="$username" 2>/dev/null | grep -q username; then
    $KC create users -r aof --server "$SERVER" \
      -s username="$username" -s enabled=true \
      -s firstName="$username" -s lastName="AOF" \
      -s email="$username@example.invalid" -s emailVerified=true \
      -s 'requiredActions=[]' \
      -s 'attributes={"aof_tenant":["'"$tenant"'"]}'
    echo "user $username created ($role @ $tenant)"
  fi
  USER_UUID=$($KC get users -r aof --server "$SERVER" -q username="$username" --fields id |
    sed -n 's/.*"id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
  if [ -z "$USER_UUID" ]; then
    echo "ERROR: user $username exists but its UUID could not be resolved" >&2
    exit 1
  fi
  $KC update "users/$USER_UUID" -r aof --server "$SERVER" \
    -s enabled=true -s firstName="$username" -s lastName="AOF" \
    -s email="$username@example.invalid" -s emailVerified=true \
    -s 'requiredActions=[]' \
    -s 'attributes={"aof_tenant":["'"$tenant"'"]}' >/dev/null
  $KC set-password -r aof --username "$username" --new-password "$username-dev-pass"
  $KC add-roles -r aof --server "$SERVER" --uusername "$username" --rolename "$role" >/dev/null 2>&1 || true
  $KC add-roles -r aof --server "$SERVER" --uusername "$username" --rolename operator >/dev/null 2>&1 || true
done

for CLIENT_UUID in "$API_CLIENT_UUID" "$WEB_CLIENT_UUID"; do
  if ! $KC get "clients/$CLIENT_UUID/protocol-mappers/models" -r aof --server "$SERVER" 2>/dev/null | grep -q aof_tenant; then
    $KC create "clients/$CLIENT_UUID/protocol-mappers/models" -r aof --server "$SERVER" \
      -s name=aof-tenant -s protocol=openid-connect \
      -s protocolMapper=oidc-usermodel-attribute-mapper \
      -s 'config={"user.attribute":"aof_tenant","claim.name":"aof_tenant","jsonType.label":"String","access.token.claim":"true","id.token.claim":"true"}'
    echo "aof_tenant mapper created for client $CLIENT_UUID"
  fi
done
echo "keycloak provisioning complete"
INNER
