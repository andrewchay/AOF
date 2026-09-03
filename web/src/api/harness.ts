import client from './client'

export interface HarnessSession {
  id: string
  problem_statement: string
  pattern_type: string
  domain: string
  scenario: string
  status: string
  current_score: number
  iteration_count: number
  satisfactory_count: number
  iterations: Iteration[]
  attribution_report?: AttributionReport
}

export interface Iteration {
  number: number
  agent_response: string
  expert_score: ExpertScore
  expert_feedback: string
  is_satisfactory: boolean
  assets_used: AssetUsage[]
}

export interface ExpertScore {
  structure: number
  accuracy: number
  completeness: number
  style: number
  reasoning: number
  overall: number
}

export interface AssetUsage {
  asset_id: string
  asset_name: string
  asset_type: string
  relevance_score: number
}

export interface AttributionReport {
  key_insights: string[]
  overall_confidence: number
}

export interface CreateSessionReq {
  problem_statement: string
  pattern_type?: string
  domain?: string
  scenario?: string
  satisfaction_threshold?: number
}

export interface AddIterationReq {
  agent_response: string
  asset_version?: string
  expert_score?: Record<string, number>
  expert_feedback?: string
  tool_calls?: Record<string, unknown>[]
}

export async function listSessions(pattern_type?: string, status?: string) {
  const params = new URLSearchParams()
  if (pattern_type) params.append('pattern_type', pattern_type)
  if (status) params.append('status', status)
  const res = await client.get(`/v1/harness/sessions?${params}`)
  return res.data as { status: string; sessions: HarnessSession[] }
}

export async function createSession(req: CreateSessionReq) {
  const res = await client.post('/v1/harness/sessions', req)
  return res.data as { status: string; session: HarnessSession }
}

export async function getSession(id: string) {
  const res = await client.get(`/v1/harness/sessions/${id}`)
  return res.data as { status: string; session: HarnessSession }
}

export async function addIteration(id: string, req: AddIterationReq) {
  const res = await client.post(`/v1/harness/sessions/${id}/iterations`, req)
  return res.data as { status: string; iteration: { number: number; is_satisfactory: boolean; score: number }; session_status: string }
}

export async function getAttribution(id: string) {
  const res = await client.get(`/v1/harness/sessions/${id}/attribution`)
  return res.data as { status: string; report: AttributionReport }
}

export async function exportTrainingData(id: string) {
  const res = await client.post(`/v1/harness/sessions/${id}/export`)
  return res.data as { status: string; sample_count: number; samples: Record<string, unknown>[] }
}
