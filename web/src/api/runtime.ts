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

export interface RuntimeRun { run_id: string; tenant_id: string; status?: string; state?: string; run_digest: string; audit_decision_id?: string; [key: string]: unknown }
export async function listReasoningRuns() { const { data } = await client.get('/semantic/reasoning-runs'); return data as { runs: RuntimeRun[]; count: number } }
export async function applyReasoning(payload: Record<string, unknown>) { const { data } = await client.post('/semantic/reasoning-runs', payload); return data as RuntimeRun }
export async function replayReasoning(runId: string) { const { data } = await client.post(`/semantic/reasoning-runs/${runId}/replay`); return data as { reproduced: boolean } }
export async function listWorkflowRuns() { const { data } = await client.get('/semantic/workflow-runs'); return data as { runs: RuntimeRun[]; count: number } }
export async function startWorkflow(plan: Record<string, unknown>, rationale: string) { const { data } = await client.post('/semantic/workflow-runs', { plan, rationale }); return data as RuntimeRun }
export async function advanceWorkflow(runId: string) { const { data } = await client.post(`/semantic/workflow-runs/${runId}/advance`); return data as RuntimeRun }
export async function listSimulations() { const { data } = await client.get('/semantic/simulations'); return data as { runs: RuntimeRun[]; count: number } }
export async function runSimulation(request: Record<string, unknown>) { const { data } = await client.post('/semantic/simulations', { request }); return data as RuntimeRun }
export async function replaySimulation(runId: string) { const { data } = await client.post(`/semantic/simulations/${runId}/replay`); return data as { reproduced: boolean } }
