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
