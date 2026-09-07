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

export interface WorkbenchSession {
  subject: string
  tenant_id: string
  roles: string[]
  permissions: Record<'read' | 'edit' | 'validate' | 'waive' | 'review' | 'publish', boolean>
}

export interface WorkbenchSummary {
  draft_count: number
  release_count: number
  state_counts: Record<string, number>
  findings: { total: number; unresolved: number; waived: number }
  evidence_integrity: { valid: boolean; decision_ledger: { valid: boolean; entries_checked: number } }
  recent_releases: Array<Record<string, any>>
  snapshot_digest: string
}

export async function getWorkbenchSession() {
  const { data } = await client.get('/ontology/workbench/session')
  return data as WorkbenchSession
}

export async function getWorkbenchSummary() {
  const { data } = await client.get('/ontology/workbench/summary')
  return data as WorkbenchSummary
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

export async function validateDraft(draftId: string) {
  const { data } = await client.post(`/ontology/drafts/${draftId}/validate`, {})
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

export async function requestChanges(draftId: string, rationale: string) {
  const { data } = await client.post(`/ontology/drafts/${draftId}/request-changes`, { rationale })
  return data
}

export async function publishDraft(draftId: string) {
  const { data } = await client.post(`/ontology/drafts/${draftId}/publish`, {})
  return data
}

export async function previewImpact(draftId: string) {
  const { data } = await client.get(`/ontology/drafts/${draftId}/impact`)
  return data as { base_version?: string; change_set: { added: string[][]; removed: string[][] }; potentially_affected_terms: string[] }
}

export async function listReleases(ontologyId?: string) {
  const { data } = await client.get('/ontology/releases', { params: { ontology_id: ontologyId } })
  return data as { releases: Array<Record<string, any>>; count: number }
}

export async function getAuditTrail(draftId: string) {
  const { data } = await client.get(`/ontology/drafts/${draftId}/audit-trail`)
  return data as {
    decision_id?: string
    integrity: { valid: boolean; entries_checked: number }
    compliance?: { policy_references: string[]; evidence_complete: boolean; rationale_complete: boolean }
    causal_chain: { nodes: Array<{ decision: Record<string, any> }>; edges: Array<Record<string, any>> }
  }
}
