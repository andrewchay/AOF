import client from './client'

export interface GraphNode {
  id: string
  labels?: string[]
  properties?: Record<string, unknown>
}

export interface GraphEdge {
  id?: string
  source_id?: string
  target_id?: string
  source?: string
  target?: string
  relationship_type?: string
  relation?: string
}

export async function getNodes(dataset: string, limit = 200) {
  const { data } = await client.get('/graph/nodes', { params: { dataset, limit } })
  return data as { nodes: GraphNode[]; pagination?: { total: number } }
}

export async function getEdges(dataset: string, limit = 500) {
  const { data } = await client.get('/graph/edges', { params: { dataset, limit } })
  return data as { edges: GraphEdge[] }
}

export async function getStatistics(dataset: string) {
  const { data } = await client.get('/graph/statistics', { params: { dataset } })
  return data as Record<string, unknown>
}
