import client from './client'

export async function batchIngest(directory: string, datasetName?: string, recursive = true) {
  const { data } = await client.post('/ingest/batch', {
    directory,
    dataset_name: datasetName,
    recursive,
  })
  return data as { status: string; dataset_name?: string; summary?: { total_files: number; success_count: number; error_count: number } }
}

export async function ingestUrl(url: string, datasetName?: string) {
  const { data } = await client.post('/ingest/url', { url, dataset_name: datasetName })
  return data
}

export async function ingestUrlBatch(urls: string[], datasetName?: string) {
  const { data } = await client.post('/ingest/url/batch', { urls, dataset_name: datasetName })
  return data
}

export interface ParseResult {
  status: string
  path: string
  engine: string
  use_raw_path: boolean
  cached: boolean
  format?: string
  tables_count: number | null
  pages: number | null
  content: string
}

export async function documentParse(path: string, opts: { async?: boolean; lang?: string } = {}) {
  const { data } = await client.post('/documents/parse', {
    path,
    async: opts.async ?? false,
    lang: opts.lang ?? 'zh',
  })
  return data as ParseResult & { task_id?: string }
}

export interface KnowledgeSource { source_id: string; tenant_id: string; source_type: string; owner: string; revision_id: string; cursor?: string; config: Record<string, unknown> }
export interface IngestionRun { run_id: string; source_id: string; status: 'succeeded' | 'failed'; cursor_from?: string; cursor_to: string; record_count: number; source_snapshot_digest: string; change_set_digest: string; run_digest: string; error?: { type: string; message: string } }
export interface ContinuousStageResult { state: string; proposal_id?: string; candidate_digest?: string; ingestion_run_id: string; change_set_digest: string; schema_drift?: Record<string, string[]>; impact?: Record<string, unknown> }
export async function listKnowledgeSources() { const { data } = await client.get('/knowledge/sources'); return data as { sources: KnowledgeSource[]; count: number } }
export async function registerKnowledgeSource(payload: { source_id: string; source_type: string; config: Record<string, unknown> }) { const { data } = await client.post('/knowledge/sources', payload); return data as KnowledgeSource }
export async function runKnowledgeIngestion(sourceId: string, attemptId: string) { const { data } = await client.post(`/knowledge/sources/${sourceId}/ingest`, { attempt_id: attemptId }); return data as IngestionRun }
export async function listIngestionRuns(sourceId?: string) { const { data } = await client.get('/knowledge/ingestion-runs', { params: { source_id: sourceId } }); return data as { runs: IngestionRun[]; count: number } }
export async function getIngestionRun(runId: string) { const { data } = await client.get(`/knowledge/ingestion-runs/${runId}`); return data as IngestionRun & { change_set?: { summary: Record<string, number>; schema_drift: Record<string, string[]> } } }
export async function stageContinuousCompilation(runId: string, payload: { release_id: string; resources: Record<string, unknown>[]; parent_release?: string }) { const { data } = await client.post(`/knowledge/ingestion-runs/${runId}/stage`, payload); return data as ContinuousStageResult }
