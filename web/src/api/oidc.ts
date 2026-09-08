/**
 * W01.02 — Keycloak OIDC login for the browser.
 *
 * Uses the password grant to obtain an access token from the configured
 * Keycloak IdP. In production the preferred flow is authorization_code + PKCE
 * (browser redirect); password grant is the dev/testing mode.
 */

const OIDC_ISSUER = import.meta.env.VITE_OIDC_ISSUER || 'http://localhost:8081/realms/aof'
const OIDC_CLIENT_ID = import.meta.env.VITE_OIDC_CLIENT_ID || 'aof-api'
const OIDC_CLIENT_SECRET = import.meta.env.VITE_OIDC_CLIENT_SECRET || 'aof-api-secret'

export const OIDC_TOKEN_KEY = 'aof.oidc-access-token'
export const OIDC_REFRESH_KEY = 'aof.oidc-refresh-token'

export interface OidcSession {
  accessToken: string
  refreshToken: string
  expiresAt: number
  subject: string
  tenant: string
  roles: string[]
}

export async function login(username: string, password: string): Promise<OidcSession> {
  const body = new URLSearchParams({
    client_id: OIDC_CLIENT_ID,
    client_secret: OIDC_CLIENT_SECRET,
    grant_type: 'password',
    username,
    password,
  })
  const resp = await fetch(`${OIDC_ISSUER}/protocol/openid-connect/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: body.toString(),
  })
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}))
    throw new Error(err.error_description || `login failed: ${resp.status}`)
  }
  const data = await resp.json()

  // decode claims (no verification in browser — server verifies)
  const payload = JSON.parse(atob(data.access_token.split('.')[1]))
  const session: OidcSession = {
    accessToken: data.access_token,
    refreshToken: data.refresh_token,
    expiresAt: Date.now() + data.expires_in * 1000,
    subject: payload.preferred_username || payload.sub,
    tenant: payload.aof_tenant || 'unknown',
    roles: payload.realm_access?.roles || [],
  }
  localStorage.setItem(OIDC_TOKEN_KEY, session.accessToken)
  localStorage.setItem(OIDC_REFRESH_KEY, session.refreshToken)
  return session
}

export function getAccessToken(): string | null {
  return localStorage.getItem(OIDC_TOKEN_KEY)
}

export function logout(): void {
  localStorage.removeItem(OIDC_TOKEN_KEY)
  localStorage.removeItem(OIDC_REFRESH_KEY)
}

export function isLoggedIn(): boolean {
  return !!getAccessToken()
}
