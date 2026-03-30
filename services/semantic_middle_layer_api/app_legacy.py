#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


AOF_ROOT = Path(os.environ.get('AOF_ROOT', str(Path(__file__).resolve().parents[2]))).resolve()
API_DATA = AOF_ROOT / 'data' / 'api_runs'
STATE_FILE = API_DATA / 'topic_state.json'
RUN_INDEX = API_DATA / 'run_index.json'
MIDDLE_LAYER_ROOT = AOF_ROOT / 'data' / 'middle_layer'

app = FastAPI(title='AOF Semantic Middle Layer API', version='0.1.0')


class IngestDocsReq(BaseModel):
    topic: str
    docs_uri: str


class IngestMetadataReq(BaseModel):
    topic: str
    metadata: dict[str, Any]


class IngestFeedbackReq(BaseModel):
    topic: str
    patches: list[dict[str, Any]]


class BuildReq(BaseModel):
    topic: str
    max_iterations: int = 3
    skip_align: bool = False


class RetrieveReq(BaseModel):
    topic: str
    query: str


class CompileReq(BaseModel):
    topic: str
    intent: str
    target: str = Field(default='sql', pattern='^(sql|rule|metric_query|graph_query)$')


class EvaluateReq(BaseModel):
    topic: str
    candidate: str


class ExplainReq(BaseModel):
    topic: str
    object_id: str


def _ensure_store() -> None:
    API_DATA.mkdir(parents=True, exist_ok=True)
    if not STATE_FILE.exists():
        STATE_FILE.write_text('{}', encoding='utf-8')
    if not RUN_INDEX.exists():
        RUN_INDEX.write_text('{}', encoding='utf-8')


def _slugify(text: str) -> str:
    import re

    t = text.strip().lower()
    t = re.sub(r'[^a-z0-9_\-\u4e00-\u9fff]+', '_', t)
    t = re.sub(r'_+', '_', t).strip('_')
    return t or 'topic'


def _load_json(path: Path) -> dict[str, Any]:
    _ensure_store()
    return json.loads(path.read_text(encoding='utf-8'))


def _save_json(path: Path, obj: dict[str, Any]) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')


def _topic_state(topic: str) -> dict[str, Any]:
    st = _load_json(STATE_FILE)
    return st.get(_slugify(topic), {})


def _update_topic_state(topic: str, patch: dict[str, Any]) -> dict[str, Any]:
    st = _load_json(STATE_FILE)
    key = _slugify(topic)
    cur = st.get(key, {})
    cur.update(patch)
    st[key] = cur
    _save_json(STATE_FILE, st)
    return cur


def _latest_manifest_for_topic(topic: str) -> Path | None:
    t = _slugify(topic)
    art_dir = MIDDLE_LAYER_ROOT / t / 'artifacts'
    if not art_dir.exists():
        return None
    files = sorted(art_dir.glob(f'middle_layer_manifest_{t}_*.json'))
    return files[-1] if files else None


def _register_run(run_id: str, manifest: Path, topic: str) -> None:
    idx = _load_json(RUN_INDEX)
    idx[run_id] = {'manifest': str(manifest), 'topic': _slugify(topic), 'created_at': datetime.now().isoformat()}
    _save_json(RUN_INDEX, idx)


def _resolve_paths(topic: str) -> tuple[Path, Path | None, Path | None]:
    st = _topic_state(topic)
    docs = Path(st.get('docs_uri', '')).expanduser().resolve() if st.get('docs_uri') else None
    metadata = Path(st.get('metadata_json', '')).expanduser().resolve() if st.get('metadata_json') else None
    feedback = Path(st.get('feedback_jsonl', '')).expanduser().resolve() if st.get('feedback_jsonl') else None

    if not docs or not docs.exists():
        raise HTTPException(status_code=400, detail='docs_uri not set or not exists for topic')
    if metadata and not metadata.exists():
        raise HTTPException(status_code=400, detail='metadata file not exists for topic')
    if feedback and not feedback.exists():
        feedback = None
    return docs, metadata, feedback


def _run_middle_layer(topic: str, max_iterations: int, skip_align: bool) -> Path:
    docs, metadata, feedback = _resolve_paths(topic)
    cmd = [
        str(AOF_ROOT / 'tools' / 'middle_layer' / 'run_semantic_middle_layer.sh'),
        _slugify(topic),
        str(docs),
        str(metadata) if metadata else '',
        str(feedback) if feedback else '',
        str(max_iterations),
    ]

    env = None
    if skip_align:
        env = dict(**__import__('os').environ)
        env.pop('LLM_API_KEY', None)

    p = subprocess.run(cmd, text=True, capture_output=True, env=env)
    if p.returncode != 0:
        raise HTTPException(status_code=500, detail={'stdout': p.stdout, 'stderr': p.stderr})

    mf = _latest_manifest_for_topic(topic)
    if not mf:
        raise HTTPException(status_code=500, detail='manifest not found after build')
    return mf


@app.post('/v1/ingest/docs')
def ingest_docs(req: IngestDocsReq) -> dict[str, Any]:
    docs = Path(req.docs_uri).expanduser().resolve()
    if not docs.exists():
        raise HTTPException(status_code=400, detail='docs_uri not exists')
    st = _update_topic_state(req.topic, {'docs_uri': str(docs)})
    return {'status': 'accepted', 'topic': _slugify(req.topic), 'state': st}


@app.post('/v1/ingest/metadata')
def ingest_metadata(req: IngestMetadataReq) -> dict[str, Any]:
    topic = _slugify(req.topic)
    out = API_DATA / topic
    out.mkdir(parents=True, exist_ok=True)
    path = out / f'metadata_{topic}.json'
    path.write_text(json.dumps(req.metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    st = _update_topic_state(req.topic, {'metadata_json': str(path)})
    return {'status': 'accepted', 'topic': topic, 'metadata_json': str(path), 'state': st}


@app.post('/v1/ingest/feedback')
def ingest_feedback(req: IngestFeedbackReq) -> dict[str, Any]:
    topic = _slugify(req.topic)
    out = API_DATA / topic
    out.mkdir(parents=True, exist_ok=True)
    path = out / f'feedback_{topic}.jsonl'
    lines = [json.dumps(p, ensure_ascii=False) for p in req.patches]
    path.write_text('\n'.join(lines) + ('\n' if lines else ''), encoding='utf-8')
    st = _update_topic_state(req.topic, {'feedback_jsonl': str(path)})
    return {'status': 'accepted', 'topic': topic, 'feedback_jsonl': str(path), 'state': st}


@app.post('/v1/build/topic')
def build_topic(req: BuildReq) -> dict[str, Any]:
    manifest = _run_middle_layer(req.topic, req.max_iterations, req.skip_align)
    run_id = manifest.stem.replace('middle_layer_manifest_', '')
    _register_run(run_id, manifest, req.topic)
    return {'run_id': run_id, 'manifest': str(manifest), 'topic': _slugify(req.topic)}


@app.get('/v1/artifacts/{run_id}')
def get_artifacts(run_id: str) -> dict[str, Any]:
    idx = _load_json(RUN_INDEX)
    row = idx.get(run_id)
    if not row:
        raise HTTPException(status_code=404, detail='run_id not found')
    mf = Path(row['manifest'])
    if not mf.exists():
        raise HTTPException(status_code=404, detail='manifest missing')
    return json.loads(mf.read_text(encoding='utf-8'))


def _load_latest_manifest(topic: str) -> dict[str, Any]:
    mf = _latest_manifest_for_topic(topic)
    if not mf:
        raise HTTPException(status_code=404, detail='no manifest for topic')
    return json.loads(mf.read_text(encoding='utf-8'))


@app.post('/v1/semantic/retrieve')
def semantic_retrieve(req: RetrieveReq) -> dict[str, Any]:
    mf = _load_latest_manifest(req.topic)
    mapping_dir = Path(mf['artifacts']['mapping_dir'])
    term_file = mapping_dir / 'term_mapping.yaml'
    text = term_file.read_text(encoding='utf-8', errors='ignore') if term_file.exists() else ''

    hits = []
    q = req.query.lower().strip()
    for line in text.splitlines():
        if q and q in line.lower():
            hits.append(line.strip())
    return {'topic': _slugify(req.topic), 'query': req.query, 'hits': hits[:20]}


@app.post('/v1/semantic/compile')
def semantic_compile(req: CompileReq) -> dict[str, Any]:
    mf = _load_latest_manifest(req.topic)
    return {
        'topic': _slugify(req.topic),
        'target': req.target,
        'intent': req.intent,
        'plan': {
            'ontology_file': mf['artifacts'].get('ontology_file', ''),
            'mapping_dir': mf['artifacts'].get('mapping_dir', ''),
            'strategy': 'mapping+ontology+pattern_constrained',
        },
    }


@app.post('/v1/semantic/evaluate')
def semantic_evaluate(req: EvaluateReq) -> dict[str, Any]:
    txt = req.candidate.lower()
    risks = []
    if 'select *' in txt:
        risks.append('select_star')
    if 'where' not in txt:
        risks.append('missing_filter')
    score = max(0.0, 1.0 - 0.2 * len(risks))
    return {'topic': _slugify(req.topic), 'score': score, 'risks': risks}


@app.post('/v1/semantic/explain')
def semantic_explain(req: ExplainReq) -> dict[str, Any]:
    mf = _load_latest_manifest(req.topic)
    return {
        'topic': _slugify(req.topic),
        'object_id': req.object_id,
        'explain': {
            'ontology_file': mf['artifacts'].get('ontology_file', ''),
            'mapping_dir': mf['artifacts'].get('mapping_dir', ''),
            'regression_jsonl': mf['artifacts'].get('regression_jsonl', ''),
        },
    }


@app.get('/v1/export/owl')
def export_owl(topic: str) -> dict[str, Any]:
    mf = _load_latest_manifest(topic)
    return {'topic': _slugify(topic), 'owl': mf['artifacts'].get('ontology_file', '')}


@app.get('/v1/export/mapping')
def export_mapping(topic: str) -> dict[str, Any]:
    mf = _load_latest_manifest(topic)
    mdir = Path(mf['artifacts'].get('mapping_dir', ''))
    files = sorted(str(p) for p in mdir.glob('*.yaml')) if mdir.exists() else []
    return {'topic': _slugify(topic), 'mapping_files': files}


@app.get('/v1/export/regression')
def export_regression(topic: str) -> dict[str, Any]:
    mf = _load_latest_manifest(topic)
    return {
        'topic': _slugify(topic),
        'regression_jsonl': mf['artifacts'].get('regression_jsonl', ''),
        'regression_summary_md': mf['artifacts'].get('regression_summary_md', ''),
        'skills_update_md': mf['artifacts'].get('skills_update_md', ''),
    }


@app.get('/healthz')
def healthz() -> dict[str, str]:
    _ensure_store()
    return {'status': 'ok'}
