/** W01.02 — Browser OIDC authorization-code flow with PKCE. */

const OIDC_ISSUER = import.meta.env.VITE_OIDC_ISSUER || 'http://localhost:8081/realms/aof'
const OIDC_CLIENT_ID = import.meta.env.VITE_OIDC_CLIENT_ID || 'aof-web'

export const OIDC_TOKEN_KEY = 'aof.oidc-access-token'
export const OIDC_REFRESH_KEY = 'aof.oidc-refresh-token'
const OIDC_VERIFIER_KEY = 'aof.oidc-pkce-verifier'
const OIDC_STATE_KEY = 'aof.oidc-state'
const OIDC_RETURN_TO_KEY = 'aof.oidc-return-to'

export interface OidcSession {
  accessToken: string
  refreshToken: string
  expiresAt: number
  subject: string
  tenant: string
  roles: string[]
}

function base64Url(value: Uint8Array): string {
  let binary = ''
  value.forEach((byte) => { binary += String.fromCharCode(byte) })
  return btoa(binary).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '')
}

function randomUrlSafe(bytes = 32): string {
  const value = new Uint8Array(bytes)
  crypto.getRandomValues(value)
  return base64Url(value)
}

async function sha256(value: string): Promise<Uint8Array> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value))
  return new Uint8Array(digest)
}

function callbackUri(): string {
  return `${window.location.origin}${window.location.pathname}`
}

export async function beginLogin(returnTo = window.location.href): Promise<void> {
  const verifier = randomUrlSafe(64)
  const state = randomUrlSafe(32)
  sessionStorage.setItem(OIDC_VERIFIER_KEY, verifier)
  sessionStorage.setItem(OIDC_STATE_KEY, state)
  sessionStorage.setItem(OIDC_RETURN_TO_KEY, returnTo)
  const query = new URLSearchParams({
    client_id: OIDC_CLIENT_ID,
    redirect_uri: callbackUri(),
    response_type: 'code',
    scope: 'openid profile',
    state,
    code_challenge: base64Url(await sha256(verifier)),
    code_challenge_method: 'S256',
  })
  window.location.assign(`${OIDC_ISSUER}/protocol/openid-connect/auth?${query}`)
}

function decodeClaims(token: string): Record<string, any> {
  const encoded = token.split('.')[1]
  if (!encoded) throw new Error('OIDC access token has no claims payload')
  const normalized = encoded.replaceAll('-', '+').replaceAll('_', '/')
  const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=')
  return JSON.parse(atob(padded))
}

function storeSession(data: Record<string, any>): OidcSession {
  const payload = decodeClaims(String(data.access_token))
  const session: OidcSession = {
    accessToken: String(data.access_token),
    refreshToken: String(data.refresh_token || ''),
    expiresAt: Date.now() + Number(data.expires_in) * 1000,
    subject: String(payload.preferred_username || payload.sub),
    tenant: String(payload.aof_tenant || 'unknown'),
    roles: Array.isArray(payload.realm_access?.roles) ? payload.realm_access.roles : [],
  }
  sessionStorage.setItem(OIDC_TOKEN_KEY, session.accessToken)
  if (session.refreshToken) sessionStorage.setItem(OIDC_REFRESH_KEY, session.refreshToken)
  localStorage.removeItem(OIDC_TOKEN_KEY)
  localStorage.removeItem(OIDC_REFRESH_KEY)
  return session
}

export async function completeLogin(url = window.location.href): Promise<OidcSession> {
  const callback = new URL(url)
  const code = callback.searchParams.get('code')
  const state = callback.searchParams.get('state')
  const expectedState = sessionStorage.getItem(OIDC_STATE_KEY)
  const verifier = sessionStorage.getItem(OIDC_VERIFIER_KEY)
  if (!code || !state || !expectedState || state !== expectedState || !verifier) {
    throw new Error('OIDC callback state or PKCE verifier is invalid')
  }
  const body = new URLSearchParams({
    client_id: OIDC_CLIENT_ID,
    grant_type: 'authorization_code',
    redirect_uri: callbackUri(),
    code,
    code_verifier: verifier,
  })
  const response = await fetch(`${OIDC_ISSUER}/protocol/openid-connect/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  })
  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    throw new Error(error.error_description || `OIDC token exchange failed: ${response.status}`)
  }
  sessionStorage.removeItem(OIDC_VERIFIER_KEY)
  sessionStorage.removeItem(OIDC_STATE_KEY)
  return storeSession(await response.json())
}

export function consumeReturnTo(): string {
  const stored = sessionStorage.getItem(OIDC_RETURN_TO_KEY)
  sessionStorage.removeItem(OIDC_RETURN_TO_KEY)
  if (!stored) return window.location.origin
  const target = new URL(stored, window.location.origin)
  return target.origin === window.location.origin ? target.href : window.location.origin
}

export function getAccessToken(): string | null {
  return sessionStorage.getItem(OIDC_TOKEN_KEY)
}

export function logout(): void {
  for (const key of [OIDC_TOKEN_KEY, OIDC_REFRESH_KEY, OIDC_VERIFIER_KEY, OIDC_STATE_KEY]) {
    sessionStorage.removeItem(key)
    localStorage.removeItem(key)
  }
}

export function isLoggedIn(): boolean {
  return Boolean(getAccessToken())
}
