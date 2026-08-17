import client from './client'

export interface OntologyDraftManifest {
  draft_id: string
  ontology_id: string
  created_by: string
  created_at: string
  state: 'draft' | 'changes_requested' | 'validated' | 'conflict_review' | 'approved' | 'published'
  revision: number
  latest_review_id?: string
  approval_decision_id?: string
  published_version?: string
}

export interface GovernanceFinding {
  finding_id: string
  type: string
  severity: string
  focus_node?: string
  path?: string
  constraint_component?: string
  message: string
}

export interface DraftBundle {
  manifest: OntologyDraftManifest
  ontology_text: string
  shapes_text: string
  skos_text: string
  reviews: Array<{ review_id: string; conforms: boolean; findings: GovernanceFinding[]; recorded_at: string }>
  waivers: Array<{ waiver_id: string; finding_id: string; rationale: string; policy: string }>
}

export async function listDrafts() {
  const { data } = await client.get('/ontology/drafts')
  return data as { drafts: OntologyDraftManifest[]; count: number }
}

export async function createDraft(payload: Record<string, unknown>) {
  const { data } = await client.post('/ontology/drafts', payload)
  return data as OntologyDraftManifest
}

export async function getDraft(draftId: string) {
  const { data } = await client.get(`/ontology/drafts/${draftId}`)
  return data as DraftBundle
}

export async function updateDraft(draftId: string, payload: Record<string, unknown>) {
  const { data } = await client.put(`/ontology/drafts/${draftId}`, payload)
  return data as OntologyDraftManifest
}

export async function validateDraft(draftId: string, actor: string) {
  const { data } = await client.post(`/ontology/drafts/${draftId}/validate`, { actor })
  return data as { review_id: string; conforms: boolean; findings: GovernanceFinding[] }
}

export async function waiveFinding(draftId: string, payload: Record<string, unknown>) {
  const { data } = await client.post(`/ontology/drafts/${draftId}/waivers`, payload)
  return data
}

export async function approveDraft(draftId: string, payload: Record<string, unknown>) {
  const { data } = await client.post(`/ontology/drafts/${draftId}/approve`, payload)
  return data
}

export async function requestChanges(draftId: string, reviewer: string, rationale: string) {
  const { data } = await client.post(`/ontology/drafts/${draftId}/request-changes`, { reviewer, rationale })
  return data
}

export async function publishDraft(draftId: string, actor: string) {
  const { data } = await client.post(`/ontology/drafts/${draftId}/publish`, { actor })
  return data
}

export async function previewImpact(draftId: string) {
  const { data } = await client.get(`/ontology/drafts/${draftId}/impact`)
  return data as { base_version?: string; change_set: { added: string[][]; removed: string[][] }; potentially_affected_terms: string[] }
}
