/*
 * Copyright (C) 2026 Andrewchay
 * Use of this software is governed by the Business Source License
 * included in the LICENSE file of this repository.
 *
 * As of the Change Date specified in that file, in accordance with
 * the Business Source License, use of this software will be governed
 * by the Apache License, Version 2.0.
 */
import axios, { type AxiosInstance } from 'axios'

export const PRINCIPAL_STORAGE_KEY = 'aof.semantic-principal-headers'

export type PrincipalHeaders = Record<string, string>

export function getPrincipalHeaders(): PrincipalHeaders {
  try {
    const value = JSON.parse(localStorage.getItem(PRINCIPAL_STORAGE_KEY) || '{}')
    return value && typeof value === 'object' ? value : {}
  } catch {
    return {}
  }
}

export function setPrincipalHeaders(headers: PrincipalHeaders) {
  localStorage.setItem(PRINCIPAL_STORAGE_KEY, JSON.stringify(headers))
}

export function clearPrincipalHeaders() {
  localStorage.removeItem(PRINCIPAL_STORAGE_KEY)
}

// Axios 实例：dev 时走 vite proxy (/v1 -> localhost:8787)
const client: AxiosInstance = axios.create({
  baseURL: '/v1',
  timeout: 30000,
})

client.interceptors.request.use((config) => {
  // W01.02: prefer OIDC Bearer token if present
  const oidcToken = localStorage.getItem('aof.oidc-access-token')
  if (oidcToken) {
    config.headers.set('Authorization', `Bearer ${oidcToken}`)
    return config
  }
  // fallback: signed principal envelope (service-to-service compat mode)
  const signedHeaders = getPrincipalHeaders()
  Object.entries(signedHeaders).forEach(([name, value]) => {
    if (name.toLowerCase().startsWith('x-aof-principal-')) config.headers.set(name, value)
  })
  return config
})

// 统一错误处理
client.interceptors.response.use(
  (resp) => resp,
  (error) => {
    const message = error?.response?.data?.detail || error?.message || '请求失败'
    return Promise.reject(new Error(message))
  },
)

export default client
