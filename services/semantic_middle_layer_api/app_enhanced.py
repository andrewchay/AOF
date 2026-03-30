#!/usr/bin/env python3
"""Enhanced AOF Semantic Middle Layer API.

This version includes:
- Intelligent semantic retrieval with mapping library
- LLM-powered SQL generation
- Comprehensive query evaluation
- Native Python pipeline execution (optional)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

# Configuration
AOF_ROOT = Path(os.environ.get('AOF_ROOT', str(Path(__file__).resolve().parents[2]))).resolve()
API_DATA = AOF_ROOT / 'data' / 'api_runs'
STATE_FILE = API_DATA / 'topic_state.json'
RUN_INDEX = API_DATA / 'run_index.json'
MIDDLE_LAYER_ROOT = AOF_ROOT / 'data' / 'middle_layer'

# LLM Configuration
LLM_API_KEY = os.environ.get('LLM_API_KEY')
LLM_PROVIDER = os.environ.get('LLM_PROVIDER', 'custom')
LLM_MODEL = os.environ.get('LLM_MODEL', 'deepseek/deepseek-chat')
LLM_ENDPOINT = os.environ.get('LLM_ENDPOINT', 'https://api.deepseek.com/v1')

app = FastAPI(
    title='AOF Semantic Middle Layer API',
    version='0.2.0',
    description='Enhanced semantic layer with LLM-powered SQL generation and evaluation'
)


# ==================== Request Models ====================

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
    top_k: int = 10
    use_semantic: bool = True  # If False, use simple text matching


class CompileReq(BaseModel):
    topic: str
    intent: str
    target: str = Field(default='sql', pattern='^(sql|rule|metric_query|graph_query)$')
    context: dict[str, Any] = Field(default_factory=dict)


class EvaluateReq(BaseModel):
    topic: str
    candidate: str
    criteria: list[str] = Field(default_factory=lambda: ['syntax', 'performance', 'correctness'])


class ExplainReq(BaseModel):
    topic: str
    object_id: str


class SearchSimilarReq(BaseModel):
    topic: str
    query: str
    limit: int = 5


# ==================== Storage Utilities ====================

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
    idx[run_id] = {
        'manifest': str(manifest),
        'topic': _slugify(topic),
        'created_at': datetime.now().isoformat()
    }
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


# ==================== Mapping Library Loader ====================

def _load_mapping_library(topic: str) -> dict[str, Any]:
    """Load mapping library for a topic."""
    t = _slugify(topic)
    mapping_dir = MIDDLE_LAYER_ROOT / t / 'artifacts' / 'mapping_library'
    
    library = {
        'terms': {},
        'metrics': {},
        'dimensions': {},
        'sql_patterns': []
    }
    
    # Load term mappings
    term_file = mapping_dir / 'term_library.yaml'
    if term_file.exists():
        try:
            import yaml
            library['terms'] = yaml.safe_load(term_file.read_text(encoding='utf-8')) or {}
        except Exception:
            pass
    
    # Load metric mappings
    metric_file = mapping_dir / 'metric_library.yaml'
    if metric_file.exists():
        try:
            import yaml
            library['metrics'] = yaml.safe_load(metric_file.read_text(encoding='utf-8')) or {}
        except Exception:
            pass
    
    # Load SQL patterns
    pattern_file = mapping_dir / 'sql_patterns.yaml'
    if pattern_file.exists():
        try:
            import yaml
            library['sql_patterns'] = yaml.safe_load(pattern_file.read_text(encoding='utf-8')) or []
        except Exception:
            pass
    
    return library


# ==================== LLM Client ====================

def _call_llm(messages: list[dict], temperature: float = 0.1) -> str:
    """Call LLM with messages."""
    if not LLM_API_KEY:
        raise HTTPException(status_code=500, detail='LLM_API_KEY not configured')
    
    try:
        import openai
        client = openai.OpenAI(
            api_key=LLM_API_KEY,
            base_url=LLM_ENDPOINT if LLM_PROVIDER == 'custom' else None
        )
        
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            temperature=temperature
        )
        return response.choices[0].message.content or ''
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'LLM call failed: {str(e)}')


# ==================== Enhanced Endpoints ====================

@app.post('/v1/ingest/docs')
def ingest_docs(req: IngestDocsReq) -> dict[str, Any]:
    """Ingest documents for a topic."""
    docs = Path(req.docs_uri).expanduser().resolve()
    if not docs.exists():
        raise HTTPException(status_code=400, detail='docs_uri not exists')
    st = _update_topic_state(req.topic, {'docs_uri': str(docs)})
    return {'status': 'accepted', 'topic': _slugify(req.topic), 'state': st}


@app.post('/v1/ingest/metadata')
def ingest_metadata(req: IngestMetadataReq) -> dict[str, Any]:
    """Ingest metadata for a topic."""
    topic = _slugify(req.topic)
    out = API_DATA / topic
    out.mkdir(parents=True, exist_ok=True)
    path = out / f'metadata_{topic}.json'
    path.write_text(json.dumps(req.metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    st = _update_topic_state(req.topic, {'metadata_json': str(path)})
    return {'status': 'accepted', 'topic': topic, 'metadata_json': str(path), 'state': st}


@app.post('/v1/ingest/feedback')
def ingest_feedback(req: IngestFeedbackReq) -> dict[str, Any]:
    """Ingest feedback patches for a topic."""
    topic = _slugify(req.topic)
    out = API_DATA / topic
    out.mkdir(parents=True, exist_ok=True)
    path = out / f'feedback_{topic}.jsonl'
    lines = [json.dumps(p, ensure_ascii=False) for p in req.patches]
    path.write_text('\n'.join(lines) + ('\n' if lines else ''), encoding='utf-8')
    st = _update_topic_state(req.topic, {'feedback_jsonl': str(path)})
    return {'status': 'accepted', 'topic': topic, 'feedback_jsonl': str(path), 'state': st}


@app.post('/v1/build/topic')
def build_topic(req: BuildReq, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Build topic ontology and mappings."""
    manifest = _run_middle_layer(req.topic, req.max_iterations, req.skip_align)
    run_id = manifest.stem.replace('middle_layer_manifest_', '')
    _register_run(run_id, manifest, req.topic)
    return {'run_id': run_id, 'manifest': str(manifest), 'topic': _slugify(req.topic)}


def _run_middle_layer(topic: str, max_iterations: int, skip_align: bool) -> Path:
    """Run middle layer build process."""
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
        env = dict(**os.environ)
        env.pop('LLM_API_KEY', None)

    p = subprocess.run(cmd, text=True, capture_output=True, env=env)
    if p.returncode != 0:
        raise HTTPException(status_code=500, detail={'stdout': p.stdout, 'stderr': p.stderr})

    mf = _latest_manifest_for_topic(topic)
    if not mf:
        raise HTTPException(status_code=500, detail='manifest not found after build')
    return mf


@app.get('/v1/artifacts/{run_id}')
def get_artifacts(run_id: str) -> dict[str, Any]:
    """Get artifacts by run ID."""
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


# ==================== Enhanced Semantic Endpoints ====================

@app.post('/v1/semantic/retrieve')
def semantic_retrieve(req: RetrieveReq) -> dict[str, Any]:
    """
    Enhanced semantic retrieval with intelligent matching.
    
    - Loads mapping library for the topic
    - Performs semantic matching on terms, metrics, and dimensions
    - Returns ranked results with relevance scores
    """
    mf = _load_latest_manifest(req.topic)
    library = _load_mapping_library(req.topic)
    
    query_lower = req.query.lower().strip()
    results = []
    
    # Search in term mappings
    for term_key, term_data in library.get('terms', {}).items():
        score = _calculate_relevance(query_lower, term_key, term_data)
        if score > 0:
            results.append({
                'type': 'term',
                'key': term_key,
                'data': term_data,
                'score': score
            })
    
    # Search in metric mappings
    for metric_key, metric_data in library.get('metrics', {}).items():
        score = _calculate_relevance(query_lower, metric_key, metric_data)
        if score > 0:
            results.append({
                'type': 'metric',
                'key': metric_key,
                'data': metric_data,
                'score': score
            })
    
    # Search in SQL patterns
    for pattern in library.get('sql_patterns', []):
        if isinstance(pattern, dict):
            pattern_desc = pattern.get('description', '')
            if query_lower in pattern_desc.lower():
                results.append({
                    'type': 'pattern',
                    'data': pattern,
                    'score': 0.8
                })
    
    # Sort by score and limit results
    results.sort(key=lambda x: x['score'], reverse=True)
    top_results = results[:req.top_k]
    
    return {
        'topic': _slugify(req.topic),
        'query': req.query,
        'hits': top_results,
        'total_matches': len(results)
    }


def _calculate_relevance(query: str, key: str, data: Any) -> float:
    """Calculate relevance score for a match."""
    score = 0.0
    
    # Direct key match
    if query in key.lower():
        score += 1.0
    
    # Check in data if it's a dict
    if isinstance(data, dict):
        name = str(data.get('name', '')).lower()
        description = str(data.get('description', '')).lower()
        
        if query in name:
            score += 0.9
        if query in description:
            score += 0.5
        
        # Check aliases
        aliases = data.get('aliases', [])
        for alias in aliases:
            if query in str(alias).lower():
                score += 0.7
                break
    
    return min(score, 1.0)


@app.post('/v1/semantic/compile')
def semantic_compile(req: CompileReq) -> dict[str, Any]:
    """
    Enhanced SQL compilation with LLM assistance.
    
    - Uses mapping library for context
    - Generates SQL based on intent and available mappings
    - Returns generated SQL with explanation
    """
    mf = _load_latest_manifest(req.topic)
    library = _load_mapping_library(req.topic)
    
    # Build context from mapping library
    context = _build_sql_context(library, req.intent)
    
    # Generate SQL using LLM
    sql = _generate_sql(req.intent, context, req.target)
    
    return {
        'topic': _slugify(req.topic),
        'target': req.target,
        'intent': req.intent,
        'generated_sql': sql,
        'context': context,
        'artifacts': {
            'ontology_file': mf['artifacts'].get('ontology_file', ''),
            'mapping_dir': mf['artifacts'].get('mapping_dir', ''),
        }
    }


def _build_sql_context(library: dict, intent: str) -> dict[str, Any]:
    """Build SQL generation context from mapping library."""
    # Extract relevant tables and fields from intent
    intent_lower = intent.lower()
    
    relevant_tables = []
    relevant_metrics = []
    
    # Find relevant tables
    for term_key, term_data in library.get('terms', {}).items():
        if isinstance(term_data, dict):
            if any(word in intent_lower for word in term_key.lower().split('_')):
                relevant_tables.append({
                    'name': term_data.get('name', term_key),
                    'fields': term_data.get('fields', [])
                })
    
    # Find relevant metrics
    for metric_key, metric_data in library.get('metrics', {}).items():
        if isinstance(metric_data, dict):
            if any(word in intent_lower for word in metric_key.lower().split('_')):
                relevant_metrics.append({
                    'name': metric_data.get('name', metric_key),
                    'sql': metric_data.get('sql_formula', '')
                })
    
    return {
        'tables': relevant_tables[:5],  # Limit context size
        'metrics': relevant_metrics[:5],
        'patterns': library.get('sql_patterns', [])[:3]
    }


def _generate_sql(intent: str, context: dict, target: str) -> str:
    """Generate SQL using LLM with context."""
    # Build prompt
    tables_desc = json.dumps(context.get('tables', []), indent=2, ensure_ascii=False)
    metrics_desc = json.dumps(context.get('metrics', []), indent=2, ensure_ascii=False)
    
    messages = [
        {
            'role': 'system',
            'content': '''You are an expert SQL generator for data analytics.
Generate SQL queries based on user intent and available schema information.
- Use only the tables and fields mentioned in the context
- For metrics, use the provided SQL formulas
- Add appropriate WHERE clauses for filtering
- Prefer explicit column names over SELECT *
- Add comments to explain complex logic'''
        },
        {
            'role': 'user',
            'content': f'''Generate a SQL query for the following intent:

Intent: {intent}

Available Tables:
{tables_desc}

Available Metrics:
{metrics_desc}

Target type: {target}

Please provide only the SQL query without explanation.'''
        }
    ]
    
    try:
        return _call_llm(messages, temperature=0.1)
    except HTTPException:
        # Fallback to template-based generation
        return _generate_sql_template(intent, context)


def _generate_sql_template(intent: str, context: dict) -> str:
    """Fallback template-based SQL generation."""
    tables = context.get('tables', [])
    metrics = context.get('metrics', [])
    
    if not tables:
        return f"-- Unable to generate SQL: no tables found for intent: {intent}"
    
    main_table = tables[0]
    table_name = main_table.get('name', 'unknown')
    fields = main_table.get('fields', [])
    
    # Select clause
    select_fields = ['id'] if 'id' in fields else (fields[:3] if fields else ['*'])
    select_clause = ', '.join(select_fields)
    
    # Metric if available
    if metrics:
        metric = metrics[0]
        select_clause += f", {metric.get('sql', 'COUNT(*)')} as {metric.get('name', 'metric')}"
    
    sql = f"SELECT {select_clause}\nFROM {table_name}"
    
    # Add simple WHERE if intent mentions filtering
    intent_lower = intent.lower()
    if any(word in intent_lower for word in ['where', 'filter', 'specific']):
        if fields:
            sql += f"\nWHERE {fields[0]} IS NOT NULL"
    
    sql += "\nLIMIT 100;"
    
    return sql


@app.post('/v1/semantic/evaluate')
def semantic_evaluate(req: EvaluateReq) -> dict[str, Any]:
    """
    Enhanced query evaluation with LLM assessment.
    
    - Evaluates SQL for syntax, performance, and correctness
    - Identifies potential risks and anti-patterns
    - Provides improvement suggestions
    """
    # Rule-based checks (fast) - works without manifest
    txt = req.candidate.lower()
    rule_risks = []
    suggestions = []
    
    if 'select *' in txt:
        rule_risks.append({'type': 'select_star', 'severity': 'medium', 'message': 'Avoid SELECT *, specify columns explicitly'})
        suggestions.append('Specify required columns instead of SELECT *')
    
    if 'where' not in txt and 'limit' not in txt:
        rule_risks.append({'type': 'missing_filter', 'severity': 'high', 'message': 'Query lacks WHERE clause, may scan entire table'})
        suggestions.append('Add appropriate WHERE clause to limit data scan')
    
    if txt.count('join') > 3:
        rule_risks.append({'type': 'many_joins', 'severity': 'medium', 'message': 'Query has many JOINs, may impact performance'})
    
    # LLM-based evaluation (if available)
    llm_evaluation = None
    if LLM_API_KEY:
        try:
            llm_evaluation = _llm_evaluate_sql(req.candidate, req.criteria)
        except Exception:
            pass
    
    # Calculate overall score
    base_score = max(0.0, 1.0 - 0.15 * len(rule_risks))
    if llm_evaluation:
        base_score = (base_score + llm_evaluation.get('score', base_score)) / 2
    
    return {
        'topic': _slugify(req.topic),
        'candidate': req.candidate,
        'score': round(base_score, 2),
        'risks': rule_risks,
        'suggestions': suggestions,
        'llm_evaluation': llm_evaluation
    }


def _llm_evaluate_sql(sql: str, criteria: list[str]) -> dict[str, Any]:
    """Use LLM to evaluate SQL query."""
    messages = [
        {
            'role': 'system',
            'content': '''You are a SQL code reviewer. Evaluate the given SQL query and provide:
1. An overall score (0.0 to 1.0)
2. List of issues found
3. Improvement suggestions

Respond in JSON format:
{
  "score": 0.85,
  "issues": [{"type": "...", "severity": "...", "message": "..."}],
  "suggestions": ["..."]
}'''
        },
        {
            'role': 'user',
            'content': f'Evaluate this SQL query:\n\n```sql\n{sql}\n```\n\nCriteria: {", ".join(criteria)}'
        }
    ]
    
    try:
        response = _call_llm(messages, temperature=0.1)
        # Extract JSON from response
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except Exception:
        pass
    
    return {'score': 0.5, 'issues': [], 'suggestions': ['Unable to perform LLM evaluation']}


@app.post('/v1/semantic/explain')
def semantic_explain(req: ExplainReq) -> dict[str, Any]:
    """Explain a query or object in the ontology."""
    mf = _load_latest_manifest(req.topic)
    
    # Try to find the object in various places
    explanation = {
        'object_id': req.object_id,
        'found': False,
        'type': None,
        'details': {}
    }
    
    # Check in ontology file
    ontology_file = mf['artifacts'].get('ontology_file')
    if ontology_file and Path(ontology_file).exists():
        try:
            content = Path(ontology_file).read_text(encoding='utf-8')
            if req.object_id in content:
                explanation['found'] = True
                explanation['type'] = 'ontology_entity'
                explanation['source'] = ontology_file
        except Exception:
            pass
    
    return {
        'topic': _slugify(req.topic),
        'explain': explanation,
        'artifacts': {
            'ontology_file': mf['artifacts'].get('ontology_file', ''),
            'mapping_dir': mf['artifacts'].get('mapping_dir', ''),
            'regression_jsonl': mf['artifacts'].get('regression_jsonl', ''),
        }
    }


# ==================== Export Endpoints ====================

@app.get('/v1/export/owl')
def export_owl(topic: str) -> dict[str, Any]:
    """Export OWL ontology file path."""
    mf = _load_latest_manifest(topic)
    return {'topic': _slugify(topic), 'owl': mf['artifacts'].get('ontology_file', '')}


@app.get('/v1/export/mapping')
def export_mapping(topic: str) -> dict[str, Any]:
    """Export mapping library files."""
    mf = _load_latest_manifest(topic)
    mdir = Path(mf['artifacts'].get('mapping_dir', ''))
    files = sorted(str(p) for p in mdir.glob('*.yaml')) if mdir.exists() else []
    return {'topic': _slugify(topic), 'mapping_files': files}


@app.get('/v1/export/regression')
def export_regression(topic: str) -> dict[str, Any]:
    """Export regression test artifacts."""
    mf = _load_latest_manifest(topic)
    return {
        'topic': _slugify(topic),
        'regression_jsonl': mf['artifacts'].get('regression_jsonl', ''),
        'regression_summary_md': mf['artifacts'].get('regression_summary_md', ''),
        'skills_update_md': mf['artifacts'].get('skills_update_md', ''),
    }


@app.get('/healthz')
def healthz() -> dict[str, Any]:
    """Health check endpoint."""
    _ensure_store()
    return {
        'status': 'ok',
        'version': '0.2.0',
        'llm_configured': 'yes' if LLM_API_KEY else 'no'
    }


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8787)
