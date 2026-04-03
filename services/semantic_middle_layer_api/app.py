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
import time
import uuid
from contextlib import nullcontext
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import PlainTextResponse
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


# ==================== Observability & Runtime State ====================

OBS_STARTED_AT = datetime.now()
OBS_TOTAL_REQUESTS = 0
OBS_TOTAL_ERRORS = 0
OBS_PATH_STATS: dict[str, dict[str, float]] = defaultdict(lambda: {
    'requests': 0.0,
    'errors': 0.0,
    'latency_ms_sum': 0.0,
    'latency_ms_max': 0.0,
})
OBS_LATENCY_SAMPLES_MS: deque[float] = deque(maxlen=5000)

MAPPING_CACHE_TTL_SECONDS = int(os.environ.get('AOF_MAPPING_CACHE_TTL_SECONDS', '120'))
MAPPING_CACHE: dict[str, dict[str, Any]] = {}
SLO_TARGETS_FILE = Path(os.environ.get('AOF_SLO_TARGETS_FILE', str(AOF_ROOT / 'config' / 'observability' / 'slo_targets.yaml')))
SLO_TARGETS_CACHE: dict[str, Any] | None = None

# OpenTelemetry tracing (optional)
OTEL_ENABLED = False
OTEL_TRACER = None
try:
    from opentelemetry import trace  # type: ignore
    from opentelemetry.sdk.resources import Resource  # type: ignore
    from opentelemetry.sdk.trace import TracerProvider  # type: ignore
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter  # type: ignore

    resource = Resource.create({
        'service.name': os.environ.get('AOF_OTEL_SERVICE_NAME', 'aof-semantic-api'),
        'service.version': '0.2.0',
    })
    provider = TracerProvider(resource=resource)
    if os.environ.get('AOF_OTEL_CONSOLE_EXPORTER', '0') == '1':
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    otlp_endpoint = os.environ.get('AOF_OTEL_EXPORTER_OTLP_ENDPOINT', '').strip()
    if otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter  # type: ignore
            provider.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(endpoint=otlp_endpoint)
                )
            )
        except Exception:
            # Keep service running even when optional OTLP exporter isn't available.
            pass
    trace.set_tracer_provider(provider)
    OTEL_TRACER = trace.get_tracer('aof.semantic.api')
    OTEL_ENABLED = True
except Exception:
    OTEL_ENABLED = False
    OTEL_TRACER = None


def _record_request_metric(path: str, status_code: int, latency_ms: float) -> None:
    global OBS_TOTAL_REQUESTS, OBS_TOTAL_ERRORS
    OBS_TOTAL_REQUESTS += 1
    if status_code >= 500:
        OBS_TOTAL_ERRORS += 1

    stats = OBS_PATH_STATS[path]
    stats['requests'] += 1
    if status_code >= 500:
        stats['errors'] += 1
    stats['latency_ms_sum'] += latency_ms
    stats['latency_ms_max'] = max(stats['latency_ms_max'], latency_ms)
    OBS_LATENCY_SAMPLES_MS.append(latency_ms)


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    if p <= 0:
        return values[0]
    if p >= 1:
        return values[-1]
    k = int(round((len(values) - 1) * p))
    return values[max(0, min(k, len(values) - 1))]


@app.middleware('http')
async def metrics_middleware(request: Request, call_next):
    started = time.perf_counter()
    request_id = request.headers.get('X-Request-ID') or str(uuid.uuid4())
    path = request.url.path

    span_name = f'{request.method} {path}'
    span_ctx = OTEL_TRACER.start_as_current_span(span_name) if OTEL_ENABLED and OTEL_TRACER else nullcontext()

    with span_ctx as span:
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            elapsed_ms = (time.perf_counter() - started) * 1000
            _record_request_metric(path, 500, elapsed_ms)
            if span is not None and hasattr(span, 'set_attribute'):
                span.set_attribute('http.status_code', 500)
                span.set_attribute('aof.request_id', request_id)
            raise

        elapsed_ms = (time.perf_counter() - started) * 1000
        _record_request_metric(path, status_code, elapsed_ms)
        if span is not None and hasattr(span, 'set_attribute'):
            span.set_attribute('http.method', request.method)
            span.set_attribute('http.route', path)
            span.set_attribute('http.status_code', status_code)
            span.set_attribute('aof.request_id', request_id)
            span.set_attribute('aof.latency_ms', elapsed_ms)
        response.headers['X-Request-ID'] = request_id
        if OTEL_ENABLED and span is not None and hasattr(span, 'get_span_context'):
            try:
                response.headers['X-Trace-ID'] = format(span.get_span_context().trace_id, '032x')
            except Exception:
                pass
        return response


def _load_slo_targets() -> dict[str, Any]:
    global SLO_TARGETS_CACHE
    if SLO_TARGETS_CACHE is not None:
        return SLO_TARGETS_CACHE
    if not SLO_TARGETS_FILE.exists():
        SLO_TARGETS_CACHE = {}
        return SLO_TARGETS_CACHE
    try:
        import yaml
        parsed = yaml.safe_load(SLO_TARGETS_FILE.read_text(encoding='utf-8')) or {}
        if not isinstance(parsed, dict):
            parsed = {}
        SLO_TARGETS_CACHE = parsed
    except Exception:
        SLO_TARGETS_CACHE = {}
    return SLO_TARGETS_CACHE


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

def _load_mapping_library(topic: str, force_refresh: bool = False) -> dict[str, Any]:
    """Load mapping library for a topic."""
    t = _slugify(topic)
    cache_key = t
    now_ts = time.time()
    cached = MAPPING_CACHE.get(cache_key)
    if cached and not force_refresh and cached.get('expires_at', 0) > now_ts:
        cached['hits'] = int(cached.get('hits', 0)) + 1
        return cached['library']

    # Priority: new mapping directory, fallback to legacy artifacts/mapping_library.
    mapping_dir = MIDDLE_LAYER_ROOT / t / 'mapping'
    if not mapping_dir.exists():
        mapping_dir = MIDDLE_LAYER_ROOT / t / 'artifacts' / 'mapping_library'
    
    library = {
        'terms': {},
        'metrics': {},
        'dimensions': {},
        'sql_patterns': []
    }
    
    # Load term mappings
    term_file = mapping_dir / 'term_mapping.yaml'
    if not term_file.exists():
        term_file = mapping_dir / 'term_library.yaml'
    if term_file.exists():
        try:
            import yaml
            library['terms'] = yaml.safe_load(term_file.read_text(encoding='utf-8')) or {}
        except Exception:
            pass
    
    # Load metric mappings
    metric_file = mapping_dir / 'metric_catalog.yaml'
    if not metric_file.exists():
        metric_file = mapping_dir / 'metric_library.yaml'
    if metric_file.exists():
        try:
            import yaml
            library['metrics'] = yaml.safe_load(metric_file.read_text(encoding='utf-8')) or {}
        except Exception:
            pass
    
    # Load dimension mappings
    dimension_file = mapping_dir / 'dimension_mapping.yaml'
    if dimension_file.exists():
        try:
            import yaml
            library['dimensions'] = yaml.safe_load(dimension_file.read_text(encoding='utf-8')) or {}
        except Exception:
            pass

    # Load SQL patterns
    pattern_file = mapping_dir / 'sql_pattern_library.yaml'
    if not pattern_file.exists():
        pattern_file = mapping_dir / 'sql_patterns.yaml'
    if pattern_file.exists():
        try:
            import yaml
            library['sql_patterns'] = yaml.safe_load(pattern_file.read_text(encoding='utf-8')) or []
        except Exception:
            pass
    
    MAPPING_CACHE[cache_key] = {
        'library': library,
        'loaded_at': now_ts,
        'expires_at': now_ts + MAPPING_CACHE_TTL_SECONDS,
        'hits': 0,
        'source': str(mapping_dir),
    }
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


# ==================== Enhanced Search Endpoints ====================

class EnhancedSearchReq(BaseModel):
    """Enhanced semantic search request."""
    topic: str
    query: str
    search_type: str = Field(
        default='auto',
        description='Search type: auto, graph_completion, rag_completion, chunks, summaries, temporal, code, cypher, feeling_lucky'
    )
    user_intent: Optional[str] = Field(
        default=None,
        description='User intent: analytics, debugging, exploration, comparison, temporal, code_related, reasoning'
    )
    top_k: int = Field(default=10, ge=1, le=100)
    use_ontology_guidance: bool = True


# 完整的搜索类型映射（全局定义）
SEARCH_TYPE_MAP = {
    # 基础搜索
    'graph_completion': 'GRAPH_COMPLETION',
    'rag_completion': 'RAG_COMPLETION',
    'chunks': 'CHUNKS',
    'summaries': 'SUMMARIES',
    # 高级搜索
    'graph_completion_cot': 'GRAPH_COMPLETION_COT',
    'graph_completion_context_ext': 'GRAPH_COMPLETION_CONTEXT_EXTENSION',
    'graph_summary_completion': 'GRAPH_SUMMARY_COMPLETION',
    'triplet_completion': 'TRIPLET_COMPLETION',
    'temporal': 'TEMPORAL',
    'code': 'CODING_RULES',
    'cypher': 'CYPHER',
    'natural_language': 'NATURAL_LANGUAGE',
    'chunks_lexical': 'CHUNKS_LEXICAL',
    'feeling_lucky': 'FEELING_LUCKY',
    'auto': None,
}


@app.post('/v1/semantic/search/enhanced')
def semantic_search_enhanced(req: EnhancedSearchReq) -> dict[str, Any]:
    """
    Enhanced semantic search with multiple search types and intent-based selection.
    
    This endpoint leverages Cognee's full search capabilities:
    
    **Base Search Types:**
    - graph_completion: Natural language Q&A with graph context
    - rag_completion: Traditional RAG with document chunks
    - chunks: Raw text segment retrieval
    - summaries: Hierarchical content summaries
    
    **Advanced Search Types:**
    - graph_completion_cot: Chain-of-Thought reasoning search
    - graph_completion_context_ext: Context-extended graph search
    - graph_summary_completion: Search based on graph summaries
    - triplet_completion: Triplet-based completion
    - temporal: Time-aware search for temporal data
    - code: Code-specific search with syntax understanding
    - cypher: Direct graph database queries (Cypher syntax)
    - natural_language: Natural language processing search
    - chunks_lexical: Lexical (token-based) chunk search
    - feeling_lucky: Auto-select best search type based on query
    
    When search_type='auto', the system will select the best type based on:
    - Query content analysis
    - User intent (if provided)
    - Available ontology context
    """
    mf = _load_latest_manifest(req.topic)
    
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        
        # Get selected search type
        selected_type = SEARCH_TYPE_MAP.get(req.search_type.lower())
        
        # Load ontology guidance if requested
        ontology_guidance = None
        if req.use_ontology_guidance:
            ontology_file = mf['artifacts'].get('ontology_file')
            if ontology_file and Path(ontology_file).exists():
                ontology_guidance = {'ontology_file': ontology_file}
        
        return {
            'topic': _slugify(req.topic),
            'query': req.query,
            'search_config': {
                'search_type': req.search_type,
                'selected_type': selected_type if selected_type else 'auto',
                'user_intent': req.user_intent,
                'top_k': req.top_k,
                'ontology_guidance': bool(ontology_guidance),
            },
            'available_search_types': {
                'base': ['graph_completion', 'rag_completion', 'chunks', 'summaries'],
                'advanced': [
                    'graph_completion_cot',
                    'graph_completion_context_ext', 
                    'graph_summary_completion',
                    'triplet_completion',
                    'temporal',
                    'code',
                    'cypher',
                    'natural_language',
                    'chunks_lexical',
                    'feeling_lucky'
                ],
                'auto': ['auto']
            },
            'search_type_descriptions': {
                'graph_completion': '自然语言问答，使用完整图谱上下文',
                'rag_completion': '传统RAG，使用文档块',
                'chunks': '原始文本段检索',
                'summaries': '分层内容摘要',
                'graph_completion_cot': 'Chain-of-Thought推理搜索',
                'graph_completion_context_ext': '上下文扩展的图谱搜索',
                'temporal': '时序感知搜索（适合时间序列数据）',
                'code': '代码特定搜索（语法+语义）',
                'cypher': '原生Cypher图查询',
                'feeling_lucky': '自动选择最佳搜索类型',
            },
            'artifacts': {
                'ontology_file': mf['artifacts'].get('ontology_file', ''),
            }
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Enhanced search configuration failed: {e}')


@app.post('/v1/semantic/search/execute')
async def execute_search(req: EnhancedSearchReq) -> dict[str, Any]:
    """
    执行增强搜索（异步版本）。
    
    真正调用 Cognee 执行搜索并返回结果。
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.enhanced_search import search_with_intent
        
        # 执行搜索
        result = await search_with_intent(
            query=req.query,
            user_intent=req.user_intent,
            top_k=req.top_k,
        )
        
        return {
            'status': 'success',
            'topic': _slugify(req.topic),
            'query': req.query,
            'search_type_used': result.search_type_used.value,
            'result_count': len(result.results),
            'results': result.results[:10],  # 最多返回10个
            'execution_time_ms': result.execution_time_ms,
            'metadata': result.metadata,
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Search execution failed: {e}')


class FeedbackReq(BaseModel):
    """Search feedback request."""
    topic: str
    query: str
    search_results: list[dict[str, Any]]
    feedback_score: int = Field(ge=1, le=5, description='User rating 1-5')
    feedback_text: Optional[str] = None
    used_graph_elements: Optional[dict] = None  # node_ids, edge_ids


@app.post('/v1/feedback/search')
def search_feedback(req: FeedbackReq) -> dict[str, Any]:
    """
    Collect user feedback on search results for ontology improvement.
    
    This feedback is used by the Memify system to:
    - Identify missing ontology elements
    - Improve graph weights based on user ratings
    - Generate feedback candidates for ontology refinement
    """
    topic = _slugify(req.topic)
    
    # Store feedback
    feedback_dir = API_DATA / topic / 'feedback'
    feedback_dir.mkdir(parents=True, exist_ok=True)
    
    feedback_record = {
        'topic': topic,
        'query': req.query,
        'feedback_score': req.feedback_score,
        'feedback_text': req.feedback_text,
        'used_graph_elements': req.used_graph_elements or {},
        'timestamp': __import__('datetime').datetime.now().isoformat(),
    }
    
    # Append to feedback log
    feedback_file = feedback_dir / 'search_feedback.jsonl'
    with open(feedback_file, 'a', encoding='utf-8') as f:
        f.write(json.dumps(feedback_record, ensure_ascii=False) + '\n')
    
    return {
        'status': 'recorded',
        'topic': topic,
        'feedback_file': str(feedback_file),
        'message': 'Feedback recorded for ontology improvement analysis',
    }


# ==================== Dataset Management Endpoints ====================

class DatasetListReq(BaseModel):
    """数据集列表请求。"""
    topic: Optional[str] = None


class DatasetStatusReq(BaseModel):
    """数据集状态请求。"""
    dataset_id: str
    pipeline_name: str = "cognify_pipeline"


class DatasetDeleteReq(BaseModel):
    """数据集删除请求。"""
    dataset_id: str
    mode: str = Field(default='empty', pattern='^(empty|data_item)$')
    data_id: Optional[str] = None  # 当 mode='data_item' 时必需


@app.get('/v1/datasets')
def list_datasets(topic: Optional[str] = None) -> dict[str, Any]:
    """
    列出所有可访问的数据集。
    
    Returns:
        数据集列表，包含名称、ID、数据量、状态等信息
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.dataset_manager import DatasetManager
        
        import asyncio
        manager = DatasetManager()
        
        # 异步执行
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            datasets = loop.run_until_complete(manager.list_datasets())
        finally:
            loop.close()
        
        return {
            'status': 'success',
            'topic': _slugify(topic) if topic else None,
            'count': len(datasets),
            'datasets': [
                {
                    'id': d.id,
                    'name': d.name,
                    'description': d.description,
                    'data_count': d.data_count,
                    'status': d.status,
                    'created_at': d.created_at.isoformat() if d.created_at else None,
                }
                for d in datasets
            ]
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to list datasets: {e}')


@app.get('/v1/datasets/{dataset_id}/status')
def get_dataset_status(dataset_id: str, pipeline_name: str = "cognify_pipeline") -> dict[str, Any]:
    """
    获取数据集的处理状态。
    
    Args:
        dataset_id: 数据集 ID
        pipeline_name: 管道名称（默认 cognify_pipeline）
        
    Returns:
        状态信息，包括进度、消息等
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.dataset_manager import DatasetManager
        
        import asyncio
        manager = DatasetManager()
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            status = loop.run_until_complete(manager.get_dataset_status(dataset_id, pipeline_name))
        finally:
            loop.close()
        
        return {
            'status': 'success',
            'dataset_id': status.dataset_id,
            'pipeline_name': status.pipeline_name,
            'state': status.status,
            'progress': status.progress,
            'message': status.message,
            'last_updated': status.last_updated.isoformat() if status.last_updated else None,
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to get dataset status: {e}')


@app.get('/v1/datasets/{dataset_id}/data')
def list_dataset_data(dataset_id: str, limit: int = 100) -> dict[str, Any]:
    """
    列出数据集中的数据项。
    
    Args:
        dataset_id: 数据集 ID
        limit: 最大返回数量
        
    Returns:
        数据项列表
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.dataset_manager import DatasetManager
        
        import asyncio
        manager = DatasetManager()
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            data_items = loop.run_until_complete(manager.list_dataset_data(dataset_id))
        finally:
            loop.close()
        
        # 限制数量
        data_items = data_items[:limit]
        
        return {
            'status': 'success',
            'dataset_id': dataset_id,
            'count': len(data_items),
            'data_items': [
                {
                    'id': d.id,
                    'name': d.name,
                    'mime_type': d.mime_type,
                    'created_at': d.created_at.isoformat() if d.created_at else None,
                }
                for d in data_items
            ]
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to list data items: {e}')


@app.delete('/v1/datasets/{dataset_id}')
def delete_dataset(dataset_id: str, mode: str = 'empty', data_id: Optional[str] = None) -> dict[str, Any]:
    """
    删除数据集或数据项。
    
    Args:
        dataset_id: 数据集 ID
        mode: 删除模式 - 'empty' 清空数据集, 'data_item' 删除特定数据项
        data_id: 当 mode='data_item' 时，指定数据项 ID
        
    Returns:
        删除结果
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.dataset_manager import DatasetManager
        
        import asyncio
        manager = DatasetManager()
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            if mode == 'empty':
                success = loop.run_until_complete(manager.empty_dataset(dataset_id))
                message = f'Dataset {dataset_id} emptied successfully'
            else:  # data_item
                if not data_id:
                    raise HTTPException(status_code=400, detail='data_id required when mode=data_item')
                success = loop.run_until_complete(manager.delete_data(dataset_id, data_id))
                message = f'Data item {data_id} deleted from dataset {dataset_id}'
        finally:
            loop.close()
        
        return {
            'status': 'success' if success else 'failed',
            'dataset_id': dataset_id,
            'mode': mode,
            'message': message,
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to delete: {e}')


# ==================== Visualization Endpoints ====================

class VisualizeReq(BaseModel):
    """可视化请求。"""
    topic: str
    open_browser: bool = False


@app.post('/v1/visualize/generate')
def generate_visualization(req: VisualizeReq) -> dict[str, Any]:
    """
    生成知识图谱的 HTML 可视化。
    
    Args:
        topic: 主题名称
        open_browser: 是否在生成后自动打开浏览器
        
    Returns:
        可视化文件路径和信息
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.graph_visualizer import GraphVisualizer
        
        import asyncio
        visualizer = GraphVisualizer()
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(visualizer.visualize_for_topic(
                topic=req.topic,
                open_browser=req.open_browser,
            ))
        finally:
            loop.close()
        
        return {
            'status': 'success',
            'topic': _slugify(req.topic),
            'html_path': str(result.html_path),
            'file_size_bytes': result.file_size_bytes,
            'file_size_human': _format_bytes(result.file_size_bytes),
            'preview_url': result.preview_url,
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to generate visualization: {e}')


@app.get('/v1/visualize/list')
def list_visualizations(topic: Optional[str] = None) -> dict[str, Any]:
    """
    列出所有可视化文件。
    
    Args:
        topic: 可选，筛选特定主题
        
    Returns:
        可视化文件列表
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.graph_visualizer import GraphVisualizer
        
        visualizer = GraphVisualizer()
        files = visualizer.list_visualizations(topic)
        
        return {
            'status': 'success',
            'topic': _slugify(topic) if topic else None,
            'count': len(files),
            'files': [
                {
                    'path': str(f),
                    'name': f.name,
                    'modified': datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                    'size_bytes': f.stat().st_size,
                    'size_human': _format_bytes(f.stat().st_size),
                }
                for f in files[:20]  # 最多返回 20 个
            ]
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to list visualizations: {e}')


@app.get('/v1/visualize/open/{filename}')
def open_visualization(filename: str) -> dict[str, Any]:
    """
    在浏览器中打开可视化文件。
    
    Args:
        filename: 可视化文件名
        
    Returns:
        操作结果
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.graph_visualizer import GraphVisualizer
        
        visualizer = GraphVisualizer()
        
        # 查找文件
        viz_dir = Path('visualizations')
        file_path = viz_dir / filename
        
        if not file_path.exists():
            raise HTTPException(status_code=404, detail=f'Visualization file not found: {filename}')
        
        success = visualizer.open_visualization(file_path)
        
        return {
            'status': 'success' if success else 'failed',
            'file': filename,
            'opened': success,
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to open visualization: {e}')


# ==================== Batch Ingestion Endpoints ====================

class BatchIngestReq(BaseModel):
    """批量摄取请求。"""
    directory: str
    dataset_name: Optional[str] = None
    recursive: bool = True
    file_pattern: Optional[str] = None
    dry_run: bool = False  # 预览模式，不实际摄取


class DiscoverDatasetsReq(BaseModel):
    """发现数据集请求。"""
    directory: str
    recursive: bool = True
    min_files: int = 1


@app.post('/v1/ingest/batch/preview')
def preview_batch_ingestion(req: BatchIngestReq) -> dict[str, Any]:
    """
    预览批量摄取（不实际执行）。
    
    Args:
        directory: 目录路径
        recursive: 是否递归子目录
        file_pattern: 文件匹配模式（如 "*.txt"）
        
    Returns:
        预览信息，包含文件统计
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.batch_ingestion import BatchIngestor
        
        ingestor = BatchIngestor()
        preview = ingestor.preview_ingestion(
            directory_path=req.directory,
            recursive=req.recursive,
            file_pattern=req.file_pattern,
        )
        
        return {
            'status': 'success',
            'preview': preview,
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to preview: {e}')


@app.post('/v1/ingest/batch')
def batch_ingest(req: BatchIngestReq) -> dict[str, Any]:
    """
    批量摄取目录中的文件。
    
    Args:
        directory: 目录路径
        dataset_name: 数据集名称（默认使用目录名）
        recursive: 是否递归子目录
        file_pattern: 文件匹配模式
        dry_run: 如果为 True，只返回预览不实际摄取
        
    Returns:
        摄取结果
    """
    if req.dry_run:
        return preview_batch_ingestion(req)
    
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.batch_ingestion import BatchIngestor
        
        import asyncio
        ingestor = BatchIngestor()
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(ingestor.ingest_directory(
                directory_path=req.directory,
                dataset_name=req.dataset_name,
                recursive=req.recursive,
                file_pattern=req.file_pattern,
            ))
        finally:
            loop.close()
        
        return {
            'status': 'success',
            'dataset_name': result.dataset_name,
            'summary': {
                'total_files': result.total_files,
                'success_count': result.success_count,
                'error_count': result.error_count,
            },
            'errors': result.errors[:10] if result.errors else [],  # 最多返回 10 个错误
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Batch ingestion failed: {e}')


@app.post('/v1/ingest/discover')
def discover_datasets(req: DiscoverDatasetsReq) -> dict[str, Any]:
    """
    自动发现目录中的数据集。
    
    Args:
        directory: 根目录路径
        recursive: 是否递归子目录
        min_files: 最小文件数量阈值
        
    Returns:
        发现的数据集列表
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.batch_ingestion import BatchIngestor
        
        ingestor = BatchIngestor()
        datasets = ingestor.discover_datasets(
            root_path=req.directory,
            recursive=req.recursive,
            min_files=req.min_files,
        )
        
        return {
            'status': 'success',
            'directory': req.directory,
            'dataset_count': len(datasets),
            'datasets': [
                {
                    'name': d.name,
                    'path': str(d.path),
                    'file_count': d.file_count,
                    'total_size_bytes': d.total_size_bytes,
                    'total_size_human': _format_bytes(d.total_size_bytes),
                    'sample_files': [str(f.name) for f in d.files[:5]],
                }
                for d in datasets
            ]
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to discover datasets: {e}')


def _format_bytes(size_bytes: int) -> str:
    """格式化字节大小为人类可读。"""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} TB"


# ==================== Graph Analytics Endpoints ====================

class GraphAnalyticsReq(BaseModel):
    """图谱分析请求。"""
    dataset_name: str
    compute_communities: bool = True


class PageRankReq(BaseModel):
    """PageRank 请求。"""
    dataset_name: str
    top_k: int = Field(default=20, ge=1, le=100)
    alpha: float = Field(default=0.85, ge=0.0, le=1.0)


class CommunityDetectReq(BaseModel):
    """社区检测请求。"""
    dataset_name: str
    algorithm: str = Field(default="louvain", pattern="^(louvain|label_propagation|greedy_modularity)$")
    resolution: float = Field(default=1.0, ge=0.1, le=10.0)


class CentralityReq(BaseModel):
    """中心性分析请求。"""
    dataset_name: str
    centrality_type: str = Field(
        default="pagerank",
        pattern="^(degree|betweenness|closeness|eigenvector|pagerank)$"
    )
    top_k: int = Field(default=20, ge=1, le=100)


class ShortestPathReq(BaseModel):
    """最短路径请求。"""
    dataset_name: str
    source: str
    target: str


@app.post('/v1/analytics/metrics')
async def compute_graph_metrics(req: GraphAnalyticsReq) -> dict[str, Any]:
    """
    计算完整图谱指标。
    
    Args:
        dataset_name: 数据集名称
        compute_communities: 是否计算社区
        
    Returns:
        完整图谱指标（统计、PageRank、社区等）
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.graph_analytics import GraphAnalytics
        
        analyzer = GraphAnalytics(req.dataset_name)
        metrics = await analyzer.compute_all_metrics()
        
        return {
            'status': 'success',
            'metrics': metrics.to_dict(),
        }
        
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f'Graph analytics not available: {e}. Install with: pip install networkx')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Graph analytics failed: {e}')


@app.post('/v1/analytics/pagerank')
async def compute_pagerank(req: PageRankReq) -> dict[str, Any]:
    """
    计算 PageRank。
    
    Args:
        dataset_name: 数据集名称
        top_k: 返回前 k 个节点
        alpha: 阻尼系数
        
    Returns:
        PageRank 结果
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.graph_analytics import GraphAnalytics
        
        analyzer = GraphAnalytics(req.dataset_name)
        nodes = await analyzer.pagerank(alpha=req.alpha, top_k=req.top_k)
        
        return {
            'status': 'success',
            'dataset_name': req.dataset_name,
            'top_k': req.top_k,
            'nodes': [n.to_dict() for n in nodes],
        }
        
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f'Graph analytics not available: {e}')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'PageRank computation failed: {e}')


@app.post('/v1/analytics/communities')
async def detect_communities_endpoint(req: CommunityDetectReq) -> dict[str, Any]:
    """
    检测图谱社区。
    
    Args:
        dataset_name: 数据集名称
        algorithm: 检测算法 (louvain/label_propagation/greedy_modularity)
        resolution: 分辨率参数
        
    Returns:
        社区检测结果
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.graph_analytics import GraphAnalytics, CommunityAlgorithm
        
        analyzer = GraphAnalytics(req.dataset_name)
        algorithm = CommunityAlgorithm(req.algorithm)
        communities = await analyzer.detect_communities(algorithm, resolution=req.resolution)
        
        return {
            'status': 'success',
            'dataset_name': req.dataset_name,
            'algorithm': req.algorithm,
            'community_count': len(communities),
            'communities': [c.to_dict() for c in communities[:20]],  # 最多返回 20 个
        }
        
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f'Graph analytics not available: {e}')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Community detection failed: {e}')


@app.post('/v1/analytics/centrality')
async def compute_centrality_endpoint(req: CentralityReq) -> dict[str, Any]:
    """
    计算中心性指标。
    
    Args:
        dataset_name: 数据集名称
        centrality_type: 中心性类型 (degree/betweenness/closeness/eigenvector/pagerank)
        top_k: 返回前 k 个节点
        
    Returns:
        中心性结果
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.graph_analytics import GraphAnalytics, CentralityType
        
        analyzer = GraphAnalytics(req.dataset_name)
        centrality = CentralityType(req.centrality_type)
        nodes = await analyzer.compute_centrality(centrality, top_k=req.top_k)
        
        return {
            'status': 'success',
            'dataset_name': req.dataset_name,
            'centrality_type': req.centrality_type,
            'nodes': [n.to_dict() for n in nodes],
        }
        
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f'Graph analytics not available: {e}')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Centrality computation failed: {e}')


@app.post('/v1/analytics/shortest_path')
async def find_shortest_path_endpoint(req: ShortestPathReq) -> dict[str, Any]:
    """
    查找最短路径。
    
    Args:
        dataset_name: 数据集名称
        source: 起点节点 ID
        target: 终点节点 ID
        
    Returns:
        路径结果
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.graph_analytics import GraphAnalytics
        
        analyzer = GraphAnalytics(req.dataset_name)
        result = await analyzer.find_shortest_path(req.source, req.target)
        
        return {
            'status': 'success',
            'dataset_name': req.dataset_name,
            'source': result.source,
            'target': result.target,
            'exists': result.exists,
            'path': result.path,
            'length': result.length,
        }
        
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f'Graph analytics not available: {e}')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Path finding failed: {e}')


@app.get('/v1/analytics/statistics')
async def get_graph_statistics(dataset_name: str) -> dict[str, Any]:
    """
    获取图谱基础统计。
    
    Args:
        dataset_name: 数据集名称
        
    Returns:
        统计信息
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.graph_analytics import GraphAnalytics
        
        analyzer = GraphAnalytics(dataset_name)
        stats = await analyzer.compute_statistics()
        
        return {
            'status': 'success',
            'dataset_name': dataset_name,
            'statistics': stats.to_dict(),
        }
        
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f'Graph analytics not available: {e}')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to compute statistics: {e}')


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


# ==================== Incremental Ingestion Endpoints ====================

class IncrementalIngestReq(BaseModel):
    """增量摄取请求。"""
    directory: str
    dataset_name: str
    recursive: bool = True
    file_pattern: Optional[str] = None
    dry_run: bool = False  # 预览模式


class IncrementalDetectReq(BaseModel):
    """增量检测请求。"""
    directory: str
    dataset_name: str
    recursive: bool = True
    file_pattern: Optional[str] = None


@app.post('/v1/ingest/incremental')
async def ingest_incremental_endpoint(req: IncrementalIngestReq) -> dict[str, Any]:
    """
    执行增量摄取，只处理变化的文件。
    
    Args:
        directory: 目录路径
        dataset_name: 数据集名称
        recursive: 是否递归子目录
        file_pattern: 文件匹配模式（如 "*.txt"）
        dry_run: 预览模式，不实际执行
        
    Returns:
        摄取结果，包含变更统计
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.incremental_loader import IncrementalLoader
        
        loader = IncrementalLoader(req.dataset_name)
        
        result = await loader.ingest_incremental(
            directory=req.directory,
            recursive=req.recursive,
            file_pattern=req.file_pattern,
            dry_run=req.dry_run,
        )
        
        return {
            'status': 'success' if result.success else 'failed',
            'dataset_name': result.dataset_name,
            'dry_run': req.dry_run,
            'changes': result.change_set.to_dict(),
            'ingested_count': result.ingested_count,
            'error_count': result.error_count,
            'errors': result.errors[:10] if result.errors else [],
            'execution_time_ms': result.execution_time_ms,
            'message': result.message,
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Incremental ingestion failed: {e}')


@app.post('/v1/ingest/incremental/detect')
async def detect_changes_endpoint(req: IncrementalDetectReq) -> dict[str, Any]:
    """
    检测目录变化，不执行摄取。
    
    Args:
        directory: 目录路径
        dataset_name: 数据集名称
        recursive: 是否递归子目录
        file_pattern: 文件匹配模式
        
    Returns:
        变更检测结果
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.incremental_loader import IncrementalLoader
        
        loader = IncrementalLoader(req.dataset_name)
        
        change_set = await loader.detect_changes(
            directory=req.directory,
            recursive=req.recursive,
            file_pattern=req.file_pattern,
        )
        
        return {
            'status': 'success',
            'dataset_name': change_set.dataset_name,
            'scan_time': change_set.scan_time.isoformat(),
            'has_changes': change_set.has_changes,
            'summary': {
                'added': len(change_set.added),
                'modified': len(change_set.modified),
                'deleted': len(change_set.deleted),
                'renamed': len(change_set.renamed),
                'unchanged': len(change_set.unchanged),
                'total': change_set.total_files,
            },
            'changes': {
                'added': [{'path': c.path} for c in change_set.added],
                'modified': [{'path': c.path} for c in change_set.modified],
                'deleted': [{'path': c.path} for c in change_set.deleted],
                'renamed': [{'path': c.path, 'from': c.renamed_from} for c in change_set.renamed],
            },
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Change detection failed: {e}')


@app.get('/v1/ingest/incremental/stats')
def get_incremental_stats(dataset_name: str) -> dict[str, Any]:
    """
    获取增量加载统计信息。
    
    Args:
        dataset_name: 数据集名称
        
    Returns:
        统计信息，包括跟踪的文件数、最后更新时间等
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.incremental_loader import IncrementalLoader
        
        loader = IncrementalLoader(dataset_name)
        stats = loader.get_stats()
        
        return {
            'status': 'success',
            'stats': stats,
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to get stats: {e}')


@app.delete('/v1/ingest/incremental/state')
def reset_incremental_state(dataset_name: str) -> dict[str, Any]:
    """
    重置增量加载状态（强制下次全量摄取）。
    
    Args:
        dataset_name: 数据集名称
        
    Returns:
        操作结果
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.incremental_loader import IncrementalLoader
        
        loader = IncrementalLoader(dataset_name)
        success = loader.reset_state()
        
        return {
            'status': 'success',
            'dataset_name': dataset_name,
            'state_reset': success,
            'message': 'State reset successfully, next ingestion will be full' if success else 'No state to reset',
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to reset state: {e}')


# ==================== S3 Ingestion Endpoints ====================

class S3IngestReq(BaseModel):
    """S3 文件摄取请求。"""
    bucket: str
    key: str
    dataset_name: Optional[str] = None
    endpoint_url: Optional[str] = None  # 用于 S3-compatible 服务
    region: str = "us-east-1"


class S3PrefixIngestReq(BaseModel):
    """S3 前缀批量摄取请求。"""
    bucket: str
    prefix: str = ""
    pattern: Optional[str] = None  # 如 "*.json"
    dataset_name: Optional[str] = None
    max_concurrent: int = Field(default=5, ge=1, le=20)
    max_files: int = Field(default=100, ge=1, le=1000)
    endpoint_url: Optional[str] = None


class S3SyncReq(BaseModel):
    """S3 同步请求（增量）。"""
    bucket: str
    prefix: str = ""
    pattern: Optional[str] = None
    dataset_name: Optional[str] = None
    incremental: bool = True
    endpoint_url: Optional[str] = None


@app.post('/v1/ingest/s3/file')
async def ingest_s3_file_endpoint(req: S3IngestReq) -> dict[str, Any]:
    """
    从 S3 摄取单个文件。
    
    Args:
        bucket: S3 桶名称
        key: 文件路径（如 "documents/report.pdf"）
        dataset_name: 数据集名称
        endpoint_url: 自定义 S3 端点（用于 MinIO 等）
        region: AWS 区域
        
    Returns:
        摄取结果
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.s3_ingestion import S3Ingester, S3Config
        
        config = S3Config(
            endpoint_url=req.endpoint_url,
            region=req.region,
        )
        ingester = S3Ingester(config)
        
        result = await ingester.ingest_file(
            bucket=req.bucket,
            key=req.key,
            dataset_name=req.dataset_name,
        )
        
        return {
            'status': 'success' if result.success else 'failed',
            'bucket': result.bucket,
            'key': result.key,
            'success': result.success,
            'size': result.size,
            'content_type': result.content_type,
            'dataset_name': result.dataset_name,
            'local_path': str(result.local_path) if result.local_path else None,
            'download_time_ms': result.download_time_ms,
            'error': result.error,
        }
        
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f'S3 support not available: {e}. Install with: pip install boto3')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'S3 ingestion failed: {e}')


@app.post('/v1/ingest/s3/prefix')
async def ingest_s3_prefix_endpoint(req: S3PrefixIngestReq) -> dict[str, Any]:
    """
    批量摄取 S3 前缀下的文件。
    
    Args:
        bucket: S3 桶名称
        prefix: 前缀路径（如 "data/2024/"）
        pattern: 文件名匹配模式（如 "*.json"）
        dataset_name: 数据集名称
        max_concurrent: 最大并发数
        max_files: 最大文件数
        
    Returns:
        批量摄取结果
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.s3_ingestion import S3Ingester, S3Config
        
        config = S3Config(
            endpoint_url=req.endpoint_url,
        )
        ingester = S3Ingester(config)
        
        result = await ingester.ingest_prefix(
            bucket=req.bucket,
            prefix=req.prefix,
            pattern=req.pattern,
            dataset_name=req.dataset_name,
            max_concurrent=req.max_concurrent,
            max_files=req.max_files,
        )
        
        return {
            'status': 'success',
            'bucket': result.bucket,
            'prefix': result.prefix,
            'total_objects': result.total_objects,
            'success_count': result.success_count,
            'failed_count': result.failed_count,
            'total_bytes': result.total_bytes,
            'total_bytes_human': _format_bytes(result.total_bytes),
            'successful': [
                {
                    'key': r.key,
                    'size': r.size,
                    'dataset_name': r.dataset_name,
                }
                for r in result.successful[:20]  # 最多返回 20 个
            ],
            'failed': [
                {
                    'key': r.key,
                    'error': r.error,
                }
                for r in result.failed[:10]  # 最多返回 10 个错误
            ],
        }
        
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f'S3 support not available: {e}')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'S3 prefix ingestion failed: {e}')


@app.post('/v1/ingest/s3/sync')
async def sync_s3_prefix_endpoint(req: S3SyncReq) -> dict[str, Any]:
    """
    同步 S3 前缀（支持增量同步）。
    
    Args:
        bucket: S3 桶名称
        prefix: 前缀路径
        pattern: 文件名匹配模式
        dataset_name: 数据集名称
        incremental: 是否只同步变化的文件
        
    Returns:
        同步结果
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.s3_ingestion import S3Ingester, S3Config
        
        config = S3Config(
            endpoint_url=req.endpoint_url,
        )
        ingester = S3Ingester(config)
        
        result = await ingester.sync_prefix(
            bucket=req.bucket,
            prefix=req.prefix,
            pattern=req.pattern,
            dataset_name=req.dataset_name,
            incremental=req.incremental,
        )
        
        return {
            'status': 'success',
            'bucket': result.bucket,
            'prefix': result.prefix,
            'incremental': req.incremental,
            'total_objects': result.total_objects,
            'success_count': result.success_count,
            'failed_count': result.failed_count,
            'total_bytes': result.total_bytes,
            'total_bytes_human': _format_bytes(result.total_bytes),
        }
        
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f'S3 support not available: {e}')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'S3 sync failed: {e}')


@app.get('/v1/ingest/s3/buckets')
async def list_s3_buckets(endpoint_url: Optional[str] = None, region: str = "us-east-1") -> dict[str, Any]:
    """
    列出所有可访问的 S3 桶。
    
    Args:
        endpoint_url: 自定义 S3 端点
        region: AWS 区域
        
    Returns:
        桶列表
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.s3_ingestion import S3Ingester, S3Config
        
        config = S3Config(
            endpoint_url=endpoint_url,
            region=region,
        )
        ingester = S3Ingester(config)
        
        buckets = await ingester.list_buckets()
        
        return {
            'status': 'success',
            'count': len(buckets),
            'buckets': buckets,
        }
        
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f'S3 support not available: {e}')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to list buckets: {e}')


@app.get('/v1/ingest/s3/objects')
async def list_s3_objects(
    bucket: str,
    prefix: str = "",
    pattern: Optional[str] = None,
    max_keys: int = 1000,
    endpoint_url: Optional[str] = None,
) -> dict[str, Any]:
    """
    列出 S3 对象。
    
    Args:
        bucket: S3 桶名称
        prefix: 前缀过滤
        pattern: 文件名匹配模式
        max_keys: 最大返回数量
        endpoint_url: 自定义 S3 端点
        
    Returns:
        对象列表
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.s3_ingestion import S3Ingester, S3Config
        
        config = S3Config(endpoint_url=endpoint_url)
        ingester = S3Ingester(config)
        
        objects = await ingester.list_objects(bucket, prefix, pattern, max_keys)
        
        return {
            'status': 'success',
            'bucket': bucket,
            'prefix': prefix,
            'count': len(objects),
            'objects': [
                {
                    'key': obj.key,
                    'size': obj.size,
                    'size_human': _format_bytes(obj.size),
                    'last_modified': obj.last_modified.isoformat(),
                    'etag': obj.etag,
                    'content_type': obj.content_type,
                }
                for obj in objects
            ],
        }
        
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f'S3 support not available: {e}')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to list objects: {e}')


# ==================== Data Sync Endpoints ====================

class DataSyncReq(BaseModel):
    """数据同步请求。"""
    local_path: str
    dataset_name: str
    direction: str = Field(default="to_cognee", pattern="^(to_cognee|from_cognee|bidirectional)$")
    strategy: str = Field(default="merge", pattern="^(local_first|cognee_first|merge|manual)$")
    incremental: bool = True


class ScheduledSyncReq(BaseModel):
    """定时同步任务请求。"""
    local_path: str
    dataset_name: str
    interval_minutes: int = Field(ge=5, le=1440)  # 5分钟到24小时
    direction: str = Field(default="to_cognee", pattern="^(to_cognee|from_cognee|bidirectional)$")
    strategy: str = Field(default="merge", pattern="^(local_first|cognee_first|merge|manual)$")


@app.post('/v1/sync')
async def sync_data_endpoint(req: DataSyncReq) -> dict[str, Any]:
    """
    执行数据同步。
    
    Args:
        local_path: 本地目录路径
        dataset_name: 数据集名称
        direction: 同步方向 (to_cognee/from_cognee/bidirectional)
        strategy: 冲突策略 (local_first/cognee_first/merge/manual)
        incremental: 是否增量同步
        
    Returns:
        同步结果
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.data_sync import DataSync, SyncDirection, SyncStrategy
        
        sync = DataSync(
            dataset_name=req.dataset_name,
            strategy=SyncStrategy(req.strategy),
        )
        
        direction = SyncDirection(req.direction)
        
        if direction == SyncDirection.TO_COGNEE:
            result = await sync.sync_to_cognee(req.local_path, req.incremental)
        elif direction == SyncDirection.FROM_COGNEE:
            result = await sync.export_from_cognee(req.local_path)
        else:
            result = await sync.sync_bidirectional(req.local_path)
        
        return result.to_dict()
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Data sync failed: {e}')


@app.post('/v1/sync/scheduled')
def add_scheduled_sync(req: ScheduledSyncReq) -> dict[str, Any]:
    """
    添加定时同步任务。
    
    Args:
        local_path: 本地目录路径
        dataset_name: 数据集名称
        interval_minutes: 同步间隔（分钟）
        direction: 同步方向
        strategy: 冲突策略
        
    Returns:
        任务信息
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.data_sync import SyncScheduler, SyncDirection, SyncStrategy
        
        scheduler = SyncScheduler()
        
        task_id = scheduler.add_task(
            local_path=req.local_path,
            dataset_name=req.dataset_name,
            direction=SyncDirection(req.direction),
            strategy=SyncStrategy(req.strategy),
            interval_minutes=req.interval_minutes,
        )
        
        return {
            'status': 'success',
            'task_id': task_id,
            'message': f'Scheduled sync task created, runs every {req.interval_minutes} minutes',
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to add scheduled sync: {e}')


@app.get('/v1/sync/scheduled')
def list_scheduled_syncs() -> dict[str, Any]:
    """
    列出所有定时同步任务。
    
    Returns:
        任务列表
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.data_sync import SyncScheduler
        
        scheduler = SyncScheduler()
        tasks = scheduler.list_tasks()
        
        return {
            'status': 'success',
            'count': len(tasks),
            'tasks': tasks,
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to list tasks: {e}')


@app.delete('/v1/sync/scheduled/{task_id}')
def remove_scheduled_sync(task_id: str) -> dict[str, Any]:
    """
    移除定时同步任务。
    
    Args:
        task_id: 任务 ID
        
    Returns:
        操作结果
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.data_sync import SyncScheduler
        
        scheduler = SyncScheduler()
        success = scheduler.remove_task(task_id)
        
        return {
            'status': 'success' if success else 'failed',
            'task_id': task_id,
            'removed': success,
            'message': 'Task removed' if success else 'Task not found',
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to remove task: {e}')


@app.get('/v1/sync/status')
def get_sync_status(dataset_name: str) -> dict[str, Any]:
    """
    获取同步状态。
    
    Args:
        dataset_name: 数据集名称
        
    Returns:
        状态信息
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.data_sync import DataSync
        
        sync = DataSync(dataset_name)
        status = sync.get_sync_status()
        
        return {
            'status': 'success',
            'sync_status': status,
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to get status: {e}')


# ==================== URL Ingestion Endpoints ====================

class URLIngestReq(BaseModel):
    """URL 摄取请求。"""
    url: str
    dataset_name: Optional[str] = None
    timeout: int = Field(default=30, ge=5, le=300)


class URLBatchIngestReq(BaseModel):
    """批量 URL 摄取请求。"""
    urls: list[str]
    dataset_name: Optional[str] = None
    max_concurrent: int = Field(default=5, ge=1, le=20)
    timeout: int = Field(default=30, ge=5, le=300)


@app.post('/v1/ingest/url')
async def ingest_url_endpoint(req: URLIngestReq) -> dict[str, Any]:
    """
    从 URL 摄取网络数据。
    
    Args:
        url: 目标 URL（支持 http/https）
        dataset_name: 数据集名称（默认从 URL 生成）
        timeout: 下载超时（秒）
        
    Returns:
        摄取结果，包括下载状态、内容类型、数据集名称等
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.url_ingestion import URLIngester
        
        ingester = URLIngester()
        
        result = await ingester.ingest(
            url=req.url,
            dataset_name=req.dataset_name,
            timeout=req.timeout,
        )
        
        return {
            'status': 'success' if result.success else 'failed',
            'url': result.url,
            'success': result.success,
            'content_type': result.content_type,
            'content_length': result.content_length,
            'title': result.title,
            'dataset_name': result.dataset_name,
            'local_path': str(result.local_path) if result.local_path else None,
            'download_time_ms': result.download_time_ms,
            'processing_time_ms': result.processing_time_ms,
            'error': result.error,
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'URL ingestion failed: {e}')


@app.post('/v1/ingest/url/batch')
async def ingest_url_batch_endpoint(req: URLBatchIngestReq) -> dict[str, Any]:
    """
    批量摄取多个 URL。
    
    Args:
        urls: URL 列表
        dataset_name: 数据集名称（所有 URL 共享）
        max_concurrent: 最大并发数
        timeout: 每个 URL 的超时时间
        
    Returns:
        批量摄取结果
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.url_ingestion import URLIngester
        
        ingester = URLIngester()
        
        results = await ingester.ingest_batch(
            urls=req.urls,
            dataset_name=req.dataset_name,
            max_concurrent=req.max_concurrent,
            timeout=req.timeout,
        )
        
        successful = [r for r in results if r.success]
        failed = [r for r in results if not r.success]
        
        return {
            'status': 'success',
            'total': len(results),
            'successful': len(successful),
            'failed': len(failed),
            'results': [
                {
                    'url': r.url,
                    'success': r.success,
                    'content_type': r.content_type,
                    'content_length': r.content_length,
                    'dataset_name': r.dataset_name,
                    'error': r.error,
                }
                for r in results
            ],
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Batch URL ingestion failed: {e}')


@app.get('/v1/ingest/url/history')
def get_url_ingest_history(limit: int = 50, success_only: bool = False) -> dict[str, Any]:
    """
    获取 URL 摄取历史。
    
    Args:
        limit: 返回记录数量
        success_only: 只返回成功的记录
        
    Returns:
        历史记录列表
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.url_ingestion import URLIngester
        
        ingester = URLIngester()
        history = ingester.get_history(limit=limit, success_only=success_only)
        stats = ingester.get_statistics()
        
        return {
            'status': 'success',
            'count': len(history),
            'statistics': stats,
            'history': history,
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to get history: {e}')


@app.get('/v1/ingest/url/stats')
def get_url_ingest_stats() -> dict[str, Any]:
    """
    获取 URL 摄取统计信息。
    
    Returns:
        统计信息，包括总摄取数、成功率、按内容类型分布等
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.url_ingestion import URLIngester
        
        ingester = URLIngester()
        stats = ingester.get_statistics()
        
        return {
            'status': 'success',
            'statistics': stats,
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to get stats: {e}')


@app.delete('/v1/ingest/url/cleanup')
def cleanup_url_temp_files(max_age_hours: int = 24) -> dict[str, Any]:
    """
    清理 URL 摄取的临时文件。
    
    Args:
        max_age_hours: 最大保留时间（小时）
        
    Returns:
        清理结果
    """
    try:
        import sys
        sys.path.insert(0, str(AOF_ROOT))
        from bridge.url_ingestion import URLIngester
        
        ingester = URLIngester()
        deleted = ingester.cleanup_temp_files(max_age_hours=max_age_hours)
        
        return {
            'status': 'success',
            'deleted_files': deleted,
            'max_age_hours': max_age_hours,
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Cleanup failed: {e}')


class CacheRefreshReq(BaseModel):
    """Refresh mapping cache for one or more topics."""
    topics: list[str] = Field(default_factory=list)
    force: bool = True


@app.get('/v1/ops/slo')
def get_slo_snapshot() -> dict[str, Any]:
    """
    Return lightweight SLO snapshot derived from request metrics.
    """
    latencies = sorted(OBS_LATENCY_SAMPLES_MS)
    p50_ms = _percentile(latencies, 0.50)
    p95_ms = _percentile(latencies, 0.95)
    p99_ms = _percentile(latencies, 0.99)
    error_rate = (OBS_TOTAL_ERRORS / OBS_TOTAL_REQUESTS) if OBS_TOTAL_REQUESTS else 0.0
    current = {
        'availability_error_rate': round(error_rate, 6),
        'latency_ms_p50': round(p50_ms, 2),
        'latency_ms_p95': round(p95_ms, 2),
        'latency_ms_p99': round(p99_ms, 2),
    }
    targets = _load_slo_targets()
    slo_targets = targets.get('slo', {}) if isinstance(targets, dict) else {}
    checks = []
    if slo_targets:
        max_err = float(slo_targets.get('availability_error_rate_max', 1.0))
        max_p95 = float(slo_targets.get('latency_ms_p95_max', 1e12))
        max_p99 = float(slo_targets.get('latency_ms_p99_max', 1e12))
        checks = [
            {
                'name': 'availability_error_rate',
                'current': current['availability_error_rate'],
                'target': max_err,
                'operator': '<=',
                'passed': current['availability_error_rate'] <= max_err,
            },
            {
                'name': 'latency_ms_p95',
                'current': current['latency_ms_p95'],
                'target': max_p95,
                'operator': '<=',
                'passed': current['latency_ms_p95'] <= max_p95,
            },
            {
                'name': 'latency_ms_p99',
                'current': current['latency_ms_p99'],
                'target': max_p99,
                'operator': '<=',
                'passed': current['latency_ms_p99'] <= max_p99,
            },
        ]

    return {
        'status': 'ok',
        'window': {
            'sample_size': len(latencies),
            'since': OBS_STARTED_AT.isoformat(),
        },
        'slo': current,
        'targets': slo_targets,
        'checks': checks,
        'overall_passed': all(c.get('passed') for c in checks) if checks else None,
        'counters': {
            'requests_total': OBS_TOTAL_REQUESTS,
            'errors_total': OBS_TOTAL_ERRORS,
        },
    }


@app.get('/v1/ops/slo/targets')
def get_slo_targets() -> dict[str, Any]:
    """Return loaded SLO target configuration."""
    return {
        'status': 'ok',
        'path': str(SLO_TARGETS_FILE),
        'targets': _load_slo_targets(),
    }


@app.post('/v1/ops/slo/reload')
def reload_slo_targets() -> dict[str, Any]:
    """Reload SLO targets from config file."""
    global SLO_TARGETS_CACHE
    SLO_TARGETS_CACHE = None
    return {
        'status': 'ok',
        'path': str(SLO_TARGETS_FILE),
        'targets': _load_slo_targets(),
    }


@app.get('/v1/ops/cache/stats')
def get_cache_stats() -> dict[str, Any]:
    """Get mapping cache status."""
    now_ts = time.time()
    items = []
    for topic, entry in sorted(MAPPING_CACHE.items()):
        expires_at = float(entry.get('expires_at', 0))
        items.append({
            'topic': topic,
            'hits': int(entry.get('hits', 0)),
            'source': entry.get('source', ''),
            'expired': expires_at <= now_ts,
            'ttl_seconds_remaining': max(0, int(expires_at - now_ts)),
        })
    return {
        'status': 'ok',
        'ttl_seconds': MAPPING_CACHE_TTL_SECONDS,
        'size': len(items),
        'items': items,
    }


@app.post('/v1/ops/cache/refresh')
def refresh_cache(req: CacheRefreshReq) -> dict[str, Any]:
    """Refresh mapping cache for topics."""
    topics = req.topics
    if not topics:
        topics = [p.name for p in MIDDLE_LAYER_ROOT.iterdir() if p.is_dir()] if MIDDLE_LAYER_ROOT.exists() else []

    refreshed = []
    failed = []
    for topic in topics:
        try:
            _load_mapping_library(topic, force_refresh=req.force)
            refreshed.append(_slugify(topic))
        except Exception as e:
            failed.append({'topic': _slugify(topic), 'error': str(e)})

    return {
        'status': 'ok',
        'refreshed_count': len(refreshed),
        'failed_count': len(failed),
        'refreshed_topics': refreshed,
        'failed_topics': failed,
    }


@app.get('/metrics')
def metrics() -> PlainTextResponse:
    """
    Prometheus-compatible metrics endpoint.
    """
    lines = [
        '# HELP aof_requests_total Total HTTP requests served.',
        '# TYPE aof_requests_total counter',
        f'aof_requests_total {OBS_TOTAL_REQUESTS}',
        '# HELP aof_request_errors_total Total HTTP 5xx responses.',
        '# TYPE aof_request_errors_total counter',
        f'aof_request_errors_total {OBS_TOTAL_ERRORS}',
        '# HELP aof_request_error_rate_ratio HTTP 5xx ratio.',
        '# TYPE aof_request_error_rate_ratio gauge',
        f'aof_request_error_rate_ratio {((OBS_TOTAL_ERRORS / OBS_TOTAL_REQUESTS) if OBS_TOTAL_REQUESTS else 0.0):.6f}',
    ]

    for path, stats in sorted(OBS_PATH_STATS.items()):
        path_safe = path.replace('\\', '_').replace('"', '\\"')
        reqs = int(stats.get('requests', 0))
        errs = int(stats.get('errors', 0))
        lat_sum = float(stats.get('latency_ms_sum', 0.0))
        lat_max = float(stats.get('latency_ms_max', 0.0))
        lat_avg = (lat_sum / reqs) if reqs else 0.0
        lines.append(f'aof_path_requests_total{{path="{path_safe}"}} {reqs}')
        lines.append(f'aof_path_errors_total{{path="{path_safe}"}} {errs}')
        lines.append(f'aof_path_latency_ms_avg{{path="{path_safe}"}} {lat_avg:.3f}')
        lines.append(f'aof_path_latency_ms_max{{path="{path_safe}"}} {lat_max:.3f}')

    return PlainTextResponse('\n'.join(lines) + '\n')


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
