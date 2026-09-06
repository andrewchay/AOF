import client from './client'

export interface OkfBundle {
  name: string
  path: string
  title: string
  concepts: number
}

export interface OkfConcept {
  path: string
  title?: string
  type?: string
  tags?: string[]
  description?: string
}

export async function listBundles() {
  const { data } = await client.get('/okf/bundles')
  return data as { count: number; bundles: OkfBundle[]; root: string }
}

export async function getBundleIndex(name: string) {
  const { data } = await client.get(`/okf/bundles/${name}/index`)
  return data as { exists: boolean; index_md?: string; error?: string }
}

export async function getConcept(name: string, path: string) {
  const { data } = await client.get(`/okf/bundles/${name}/concept`, {
    params: { path },
  })
  return data as { exists: boolean; concept?: string; error?: string }
}

export async function searchConcepts(
  name: string,
  params: { query?: string; type?: string; title?: string; tag?: string; limit?: number },
) {
  const { data } = await client.get(`/okf/bundles/${name}/search`, { params })
  return data as { count: number; results: object[] }
}

export async function lintBundle(name: string) {
  const { data } = await client.post(`/okf/bundles/${name}/lint`)
  return data as { error_count: number; warning_count: number; issues: object[]; concepts_scanned: number }
}
