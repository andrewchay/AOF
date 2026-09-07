/*
 * Copyright (C) 2026 Andrewchay
 * Use of this software is governed by the Business Source License
 * included in the LICENSE file of this repository.
 *
 * As of the Change Date specified in that file, in accordance with
 * the Business Source License, use of this software will be governed
 * by the Apache License, Version 2.0.
 */
import client from './client'

export interface DatasetInfo {
  id: string
  name: string
  description?: string
  data_count?: number
  status?: string
}

export async function listDatasets() {
  const { data } = await client.get('/datasets')
  return data as { datasets: DatasetInfo[] }
}

export async function getGraphStatistics() {
  const { data } = await client.get('/graph/statistics')
  return data as Record<string, unknown>
}

export async function getAnalyticsStatistics() {
  const { data } = await client.get('/analytics/statistics')
  return data as Record<string, unknown>
}

export async function healthCheck() {
  const { data } = await client.get('/healthz')
  return data as Record<string, unknown>
}
