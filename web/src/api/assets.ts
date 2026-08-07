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
