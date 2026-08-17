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
import logging
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

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Query
from fastapi.responses import PlainTextResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Logging
logger = logging.getLogger('aof_api')

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


class ParseDocReq(BaseModel):
    """文档解析请求（阶段 3，document_parser 对外能力）。"""

    path: str
    async_: bool = Field(default=False, alias="async")
    lang: str = "zh"


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


_parse_queue_singleton = None


def _get_parse_queue():
    """模块级 DocumentParseQueue 单例（异步解析端点复用，避免每次请求新建）。"""
    global _parse_queue_singleton
    if _parse_queue_singleton is None:
        from bridge.document_parser import DocumentParseQueue, ParserConfig

        _parse_queue_singleton = DocumentParseQueue(parser_config=ParserConfig())
    return _parse_queue_singleton


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


@app.post('/v1/documents/parse')
async def document_parse(req: ParseDocReq) -> dict[str, Any]:
    """解析单个文件为干净 Markdown + 元数据（document_parser 阶段 3 对外能力）。

    同步模式：返回 ParsedDoc（content + metadata）。
    异步模式（async=true）：提交到 DocumentParseQueue，返回 task_id 供轮询。
    """
    path = Path(req.path).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=400, detail=f'path not a file: {req.path}')

    # 路径限域（防任意文件读取）：只允许解析白名单根目录内的文件
    from bridge.document_parser.security import PathNotAllowedError, validate_parse_path

    try:
        path = validate_parse_path(path)
    except PathNotAllowedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    from bridge.document_parser import ParserConfig, parse_document

    if req.async_:
        queue = _get_parse_queue()
        task_id = await queue.submit_parse(str(path), name=f"api-parse:{path.name}")
        return {'status': 'accepted', 'task_id': task_id, 'path': str(path)}

    config = ParserConfig(lang=req.lang)
    result = parse_document(path, config=config)
    doc = result.doc
    return {
        'status': 'ok',
        'path': str(path),
        'engine': doc.engine,
        'use_raw_path': result.use_raw_path,
        'cached': result.cached,
        'format': 'markdown',
        'tables_count': doc.tables_count,
        'pages': doc.pages,
        'content': doc.content,
    }


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
    _load_latest_manifest(req.topic)
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
    """Reject the pre-Release SQL generator at the public API boundary."""
    raise HTTPException(
        status_code=410,
        detail={
            'code': 'legacy_semantic_compile_retired',
            'message': 'Ungoverned semantic compilation is retired',
            'replacement': '/v1/semantic/query',
            'capability': 'semantic_sql',
        },
    )


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
    style: str = "cognee"  # cognee | template


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
        
        if req.style == "template":
            result = visualizer.create_template_visualization(
                topic=req.topic,
                open_browser=req.open_browser,
            )
        else:
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
            'style': req.style,
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


@app.get('/v1/visualize/template')
def get_visualization_template() -> FileResponse:
    """
    获取 AOF 通用图谱展示模板页面。

    可直接通过 URL 参数传入数据源：
    - nodes=/path/to/nodes.json
    - edges=/path/to/edges.json
    - apiBase=http://localhost:8787
    - dataset=default
    - autoload=1
    """
    template_path = AOF_ROOT / 'visualization' / 'kg_vis_aof_template.html'
    if not template_path.exists():
        raise HTTPException(status_code=404, detail='Visualization template not found')
    return FileResponse(path=template_path, media_type='text/html')


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


# ==================== Training Data Generation Endpoints ====================

class TrainingDataGenerateReq(BaseModel):
    """训练数据生成请求。"""
    dataset_name: str
    generators: list[str] = Field(default=['sft', 'rag_eval'])
    max_samples: int = Field(default=1000, ge=10, le=10000)
    quality_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    enable_deduplication: bool = True
    enable_train_val_test_split: bool = False
    system_prompt_template: Optional[str] = None


class TrainingDataJobResp(BaseModel):
    """训练数据生成任务响应。"""
    job_id: str
    status: str
    progress: float
    estimated_samples: int
    output_files: list[str]
    metrics: dict[str, Any]


_TRAINING_DATA_JOBS: dict[str, dict[str, Any]] = {}


@app.post('/v1/training-data/generate')
async def generate_training_data(req: TrainingDataGenerateReq) -> dict[str, Any]:
    """
    生成 AI/Agent 训练数据集。

    基于知识图谱和文档生成结构化训练数据，支持 SFT、RAG 评估、Agent 工具调用三种类型。

    Args:
        dataset_name: 数据集名称
        generators: 生成器类型列表 ["sft", "rag_eval", "agent_tool"]
        max_samples: 每种生成器的最大样本数
        quality_threshold: 质量阈值（0-1）
        enable_deduplication: 是否启用去重
        enable_train_val_test_split: 是否拆分为训练/验证/测试集
        system_prompt_template: 自定义 system prompt 模板

    Returns:
        任务信息，包含 job_id 用于后续查询
    """
    import sys
    import uuid
    sys.path.insert(0, str(AOF_ROOT))

    from bridge.training_data import TrainingDataPipeline, QualityConfig, GeneratorConfig
    from bridge.training_data.generators import SFTGenerator, RAGEvalGenerator, AgentToolGenerator

    job_id = str(uuid.uuid4())

    # 解析生成器
    gen_mapping = {
        'sft': SFTGenerator,
        'rag_eval': RAGEvalGenerator,
        'agent_tool': AgentToolGenerator,
    }
    generators = []
    for name in req.generators:
        cls = gen_mapping.get(name)
        if cls:
            generators.append(cls())

    if not generators:
        raise HTTPException(status_code=400, detail='No valid generators specified')

    # 配置
    gen_config = GeneratorConfig(
        max_samples=req.max_samples,
        system_prompt_template=req.system_prompt_template or '',
    )
    quality_config = QualityConfig(
        enable_deduplication=req.enable_deduplication,
    )

    # 输出目录
    output_dir = API_DATA / 'training_data' / job_id
    output_dir.mkdir(parents=True, exist_ok=True)

    # 初始化任务状态
    _TRAINING_DATA_JOBS[job_id] = {
        'job_id': job_id,
        'status': 'running',
        'progress': 0.0,
        'dataset_name': req.dataset_name,
        'generators': req.generators,
        'output_dir': str(output_dir),
        'output_files': [],
        'metrics': {},
        'created_at': datetime.now().isoformat(),
    }

    # 在后台执行（同步执行但返回 job_id）
    try:
        pipeline = TrainingDataPipeline()
        result = await pipeline.run(
            dataset_name=req.dataset_name,
            generators=generators,
            output_path=output_dir,
            quality_config=quality_config,
            generator_config=gen_config,
            enable_split=req.enable_train_val_test_split,
        )

        _TRAINING_DATA_JOBS[job_id].update({
            'status': 'success',
            'progress': 100.0,
            'output_files': result.output_files,
            'metrics': {
                'total_samples': result.total_samples,
                'samples_by_type': result.samples_by_type,
                'duration_seconds': result.duration_seconds,
                'quality_metrics': result.quality_metrics,
            },
        })

        return {
            'status': 'success',
            'job_id': job_id,
            'message': 'Training data generation completed',
            'result': {
                'total_samples': result.total_samples,
                'samples_by_type': result.samples_by_type,
                'output_files': result.output_files,
                'duration_seconds': result.duration_seconds,
            },
        }

    except Exception as e:
        _TRAINING_DATA_JOBS[job_id]['status'] = 'failure'
        _TRAINING_DATA_JOBS[job_id]['error'] = str(e)
        logger.error(f'Training data generation failed: {e}', exc_info=True)
        raise HTTPException(status_code=500, detail=f'Training data generation failed: {e}')


@app.get('/v1/training-data/jobs/{job_id}')
def get_training_data_job(job_id: str) -> dict[str, Any]:
    """
    查询训练数据生成任务状态。

    Args:
        job_id: 任务 ID

    Returns:
        任务状态和进度信息
    """
    job = _TRAINING_DATA_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f'Job {job_id} not found')
    return {
        'status': 'success',
        'job': job,
    }


@app.get('/v1/training-data/jobs/{job_id}/download')
def download_training_data(job_id: str, file: str = 'training_data') -> Any:
    """
    下载生成的训练数据文件。

    Args:
        job_id: 任务 ID
        file: 文件名（默认 training_data.jsonl，或 train/validation/test）

    Returns:
        文件下载响应
    """
    job = _TRAINING_DATA_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f'Job {job_id} not found')

    output_dir = Path(job['output_dir'])

    # 查找文件
    candidates = [
        output_dir / f"{file}.jsonl",
        output_dir / file,
    ]
    for candidate in candidates:
        if candidate.exists():
            return FileResponse(
                path=str(candidate),
                filename=candidate.name,
                media_type='application/jsonl+json',
            )

    # 列出可用文件
    available = [f.name for f in output_dir.glob('*.jsonl')] if output_dir.exists() else []
    raise HTTPException(
        status_code=404,
        detail=f'File not found. Available: {available}',
    )


@app.get('/v1/training-data/templates')
def get_training_data_templates() -> dict[str, Any]:
    """
    获取各生成器的 prompt 模板。

    Returns:
        SFT、RAG Eval、Agent Tool 的默认模板
    """
    return {
        'status': 'success',
        'templates': {
            'sft': {
                'system_prompt': '你是一个企业知识助手，基于知识图谱回答用户问题。',
                'description': 'SFT 微调数据生成器，基于实体、关系、文档生成 instruction-response 对',
            },
            'rag_eval': {
                'system_prompt': '',
                'description': 'RAG 评估数据生成器，基于图谱生成 question-answer-context 三元组',
            },
            'agent_tool': {
                'system_prompt': '',
                'description': 'Agent 工具调用数据生成器，基于策略本体生成 function calling 样本',
            },
        },
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


# ==================== Graph API Endpoints (v1) ====================
# 专用知识图谱可视化API，支持高性能查询和实时交互

class GraphQueryReq(BaseModel):
    """图查询请求。"""
    query: str
    parameters: Optional[dict[str, Any]] = None
    dataset: str = 'default'


class GraphSearchReq(BaseModel):
    """图搜索请求。"""
    query: str
    limit: int = Field(default=20, ge=1, le=100)
    fuzzy: bool = True
    dataset: str = 'default'


class GraphNeighborReq(BaseModel):
    """邻居查询请求。"""
    depth: int = Field(default=1, ge=1, le=3)
    limit: int = Field(default=50, ge=1, le=200)
    direction: str = Field(default='both', pattern='^(in|out|both)$')


def _get_graph_backend(dataset_name: str = 'default'):
    """获取图存储后端实例。"""
    import sys
    sys.path.insert(0, str(AOF_ROOT))
    from bridge.storage import StorageFactory, StorageConfig
    
    config = StorageConfig(
        backend_type=os.environ.get('STORAGE_BACKEND', 'cognee'),
        default_dataset=dataset_name
    )
    backend = StorageFactory.create(config)
    # 尝试连接
    if hasattr(backend, 'connect'):
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(backend.connect())
            else:
                loop.run_until_complete(backend.connect())
        except Exception:
            pass
    return backend


@app.get('/v1/graph/health')
async def graph_health_check() -> dict[str, Any]:
    """
    Graph API 健康检查。
    
    Returns:
        Graph API 状态和配置信息
    """
    try:
        backend = _get_graph_backend()
        connected = await backend.health_check() if hasattr(backend, 'health_check') else True
        
        return {
            'status': 'healthy' if connected else 'degraded',
            'api_version': '1.0.0',
            'backend_type': backend.__class__.__name__,
            'features': [
                'nodes_query',
                'edges_query', 
                'neighbors_query',
                'search',
                'statistics',
                'cypher_query'
            ]
        }
    except Exception as e:
        return {
            'status': 'unavailable',
            'error': str(e)
        }


@app.get('/v1/graph/nodes')
async def get_graph_nodes(
    dataset: str = 'default',
    limit: int = Query(default=1000, ge=1, le=10000),
    offset: int = Query(default=0, ge=0),
    categories: Optional[str] = None,
    include_properties: bool = Query(default=True)
) -> dict[str, Any]:
    """
    获取知识图谱节点列表。
    
    Args:
        dataset: 数据集名称
        limit: 返回节点数量上限
        offset: 分页偏移量
        categories: 分类过滤（逗号分隔）
        include_properties: 是否包含节点属性
        
    Returns:
        节点列表和分页信息
    """
    try:
        backend = _get_graph_backend(dataset)
        
        # 获取所有节点（后端需要实现分页）
        # 这里使用 execute_cypher 作为通用查询方式
        category_filter = ''
        if categories:
            cat_list = categories.split(',')
            cat_conditions = ' OR '.join([f'n:`{c}`' for c in cat_list])
            category_filter = f'WHERE {cat_conditions}'
        
        cypher = f'''
            MATCH (n)
            {category_filter}
            RETURN n
            SKIP {offset}
            LIMIT {limit}
        '''
        
        nodes = await backend.execute_cypher(cypher)
        
        # 格式化节点数据
        formatted_nodes = []
        for record in nodes:
            node = record.get('n', record)
            formatted_nodes.append({
                'id': str(node.get('id', node.get('node_id', ''))),
                'labels': node.get('labels', node.get('type', ['Node'])),
                'properties': node if include_properties else {}
            })
        
        # 获取总数
        count_result = await backend.execute_cypher('MATCH (n) RETURN count(n) as total')
        total = count_result[0].get('total', 0) if count_result else 0
        
        return {
            'status': 'success',
            'dataset': dataset,
            'nodes': formatted_nodes,
            'pagination': {
                'total': total,
                'limit': limit,
                'offset': offset,
                'has_more': offset + len(formatted_nodes) < total
            }
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to fetch nodes: {e}')


@app.get('/v1/graph/edges')
async def get_graph_edges(
    dataset: str = 'default',
    limit: int = Query(default=2000, ge=1, le=50000),
    offset: int = Query(default=0, ge=0),
    source_id: Optional[str] = None,
    target_id: Optional[str] = None,
    relation_type: Optional[str] = None
) -> dict[str, Any]:
    """
    获取知识图谱边/关系列表。
    
    Args:
        dataset: 数据集名称
        limit: 返回边数量上限
        offset: 分页偏移量
        source_id: 源节点ID过滤
        target_id: 目标节点ID过滤
        relation_type: 关系类型过滤
        
    Returns:
        边列表和分页信息
    """
    try:
        backend = _get_graph_backend(dataset)
        
        # 构建查询条件
        conditions = []
        if source_id:
            conditions.append(f'startNode(r).id = "{source_id}"')
        if target_id:
            conditions.append(f'endNode(r).id = "{target_id}"')
        if relation_type:
            conditions.append(f'type(r) = "{relation_type}"')
        
        where_clause = f'WHERE {" AND ".join(conditions)}' if conditions else ''
        
        cypher = f'''
            MATCH (a)-[r]->(b)
            {where_clause}
            RETURN a.id as source_id, b.id as target_id, 
                   type(r) as relation_type, r as properties
            SKIP {offset}
            LIMIT {limit}
        '''
        
        edges = await backend.execute_cypher(cypher)
        
        formatted_edges = []
        for record in edges:
            formatted_edges.append({
                'source_id': str(record.get('source_id', '')),
                'target_id': str(record.get('target_id', '')),
                'relation_type': record.get('relation_type', 'RELATED'),
                'properties': record.get('properties', {})
            })
        
        return {
            'status': 'success',
            'dataset': dataset,
            'edges': formatted_edges,
            'pagination': {
                'limit': limit,
                'offset': offset,
                'count': len(formatted_edges)
            }
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to fetch edges: {e}')


@app.get('/v1/graph/nodes/{node_id}')
async def get_node_by_id(
    node_id: str,
    dataset: str = 'default',
    include_neighbors: bool = Query(default=False)
) -> dict[str, Any]:
    """
    根据ID获取单个节点详情。
    
    Args:
        node_id: 节点ID
        dataset: 数据集名称
        include_neighbors: 是否包含邻居信息
        
    Returns:
        节点详情
    """
    try:
        backend = _get_graph_backend(dataset)
        
        node = await backend.get_node(node_id)
        if not node:
            raise HTTPException(status_code=404, detail=f'Node {node_id} not found')
        
        result = {
            'id': str(node.id),
            'labels': node.labels,
            'properties': node.properties
        }
        
        if include_neighbors:
            neighbors = await backend.get_neighbors(node_id, limit=20)
            result['neighbors'] = [
                {'id': str(n.id), 'labels': n.labels, 'name': n.properties.get('name', '')}
                for n in neighbors
            ]
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to fetch node: {e}')


@app.get('/v1/graph/nodes/{node_id}/neighbors')
async def get_node_neighbors(
    node_id: str,
    dataset: str = 'default',
    depth: int = Query(default=1, ge=1, le=3),
    limit: int = Query(default=50, ge=1, le=200),
    direction: str = Query(default='both', pattern='^(in|out|both)$')
) -> dict[str, Any]:
    """
    获取节点的邻居子图。
    
    Args:
        node_id: 中心节点ID
        dataset: 数据集名称
        depth: 搜索深度（1-3）
        limit: 最大返回节点数
        direction: 方向 (in/out/both)
        
    Returns:
        邻居节点和连接边
    """
    try:
        backend = _get_graph_backend(dataset)
        
        # 检查节点是否存在
        node = await backend.get_node(node_id)
        if not node:
            raise HTTPException(status_code=404, detail=f'Node {node_id} not found')
        
        # 获取邻居
        neighbors = await backend.get_neighbors(
            node_id=node_id,
            direction=direction,
            limit=limit
        )
        
        # 获取相关边
        edges = await backend.get_edges(source_id=node_id)
        edges += await backend.get_edges(target_id=node_id)
        
        # 去重
        seen_edges = set()
        unique_edges = []
        for e in edges:
            edge_key = (e.source_id, e.target_id, e.relation_type)
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                unique_edges.append(e)
        
        return {
            'status': 'success',
            'center_node': {
                'id': str(node.id),
                'labels': node.labels,
                'properties': node.properties
            },
            'nodes': [
                {
                    'id': str(n.id),
                    'labels': n.labels,
                    'properties': n.properties
                }
                for n in neighbors
            ],
            'edges': [
                {
                    'source_id': str(e.source_id),
                    'target_id': str(e.target_id),
                    'relation_type': e.relation_type,
                    'properties': e.properties
                }
                for e in unique_edges[:limit]
            ]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to fetch neighbors: {e}')


@app.post('/v1/graph/search')
async def search_graph_nodes(req: GraphSearchReq) -> dict[str, Any]:
    """
    搜索知识图谱节点。
    
    Args:
        query: 搜索关键词
        limit: 返回结果数量上限
        fuzzy: 是否模糊匹配
        dataset: 数据集名称
        
    Returns:
        匹配的节点列表
    """
    try:
        backend = _get_graph_backend(req.dataset)
        
        # 使用属性搜索
        if req.fuzzy:
            cypher = f'''
                MATCH (n)
                WHERE n.name CONTAINS "{req.query}" 
                   OR n.title CONTAINS "{req.query}"
                   OR n.description CONTAINS "{req.query}"
                RETURN n
                LIMIT {req.limit}
            '''
        else:
            cypher = f'''
                MATCH (n)
                WHERE n.name = "{req.query}" 
                   OR n.title = "{req.query}"
                RETURN n
                LIMIT {req.limit}
            '''
        
        results = await backend.execute_cypher(cypher)
        
        nodes = []
        for record in results:
            node = record.get('n', record)
            nodes.append({
                'id': str(node.get('id', '')),
                'name': node.get('name', node.get('title', '')),
                'labels': node.get('labels', []),
                'properties': node
            })
        
        return {
            'status': 'success',
            'query': req.query,
            'count': len(nodes),
            'nodes': nodes
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Search failed: {e}')


@app.post('/v1/graph/query')
async def execute_graph_query(req: GraphQueryReq) -> dict[str, Any]:
    """
    执行自定义 Cypher/nGQL 查询。
    
    Args:
        query: Cypher 或 nGQL 查询语句
        parameters: 查询参数
        dataset: 数据集名称
        
    Returns:
        查询结果
    """
    try:
        backend = _get_graph_backend(req.dataset)
        
        results = await backend.execute_cypher(req.query, req.parameters or {})
        
        return {
            'status': 'success',
            'query': req.query,
            'count': len(results),
            'results': results
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Query execution failed: {e}')


@app.get('/v1/graph/statistics')
async def get_graph_viz_statistics(dataset: str = 'default') -> dict[str, Any]:
    """
    获取知识图谱统计信息（可视化专用）。
    
    Args:
        dataset: 数据集名称
        
    Returns:
        图谱统计信息
    """
    try:
        backend = _get_graph_backend(dataset)
        
        stats = await backend.get_statistics()
        
        # 获取分类统计
        cat_cypher = '''
            MATCH (n)
            UNWIND labels(n) as label
            RETURN label, count(*) as count
            ORDER BY count DESC
        '''
        cat_results = await backend.execute_cypher(cat_cypher)
        
        categories = {}
        for r in cat_results:
            label = r.get('label', 'Unknown')
            count = r.get('count', 0)
            categories[label] = count
        
        return {
            'status': 'success',
            'dataset': dataset,
            'node_count': stats.node_count,
            'edge_count': stats.edge_count,
            'categories': categories,
            'density': stats.node_count / (stats.edge_count + 1) if stats.edge_count > 0 else 0
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to get statistics: {e}')


@app.get('/v1/graph/categories')
async def get_graph_categories(dataset: str = 'default') -> dict[str, Any]:
    """
    获取所有节点分类/标签列表。
    
    Args:
        dataset: 数据集名称
        
    Returns:
        分类列表
    """
    try:
        backend = _get_graph_backend(dataset)
        
        cypher = '''
            MATCH (n)
            UNWIND labels(n) as label
            RETURN label, count(*) as count
            ORDER BY count DESC
        '''
        
        results = await backend.execute_cypher(cypher)
        
        categories = []
        for r in results:
            categories.append({
                'name': r.get('label', 'Unknown'),
                'count': r.get('count', 0)
            })
        
        return {
            'status': 'success',
            'dataset': dataset,
            'count': len(categories),
            'categories': categories
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to get categories: {e}')


# ==================== OKF / LLM Wiki Knowledge Bundle Endpoints ====================

def _okf_root() -> Path:
    """OKF 知识包根目录：优先 AOF_OKF_DIR，其次 spec.okf.dir，默认 <AOF_ROOT>/okf_bundle."""
    import os as _os
    env_dir = _os.environ.get('AOF_OKF_DIR')
    if env_dir:
        p = Path(env_dir)
        return p if p.is_absolute() else (AOF_ROOT / p)
    return AOF_ROOT / 'okf_bundle'


def _okf_service():
    """惰性导入共享 okf_service 模块."""
    import sys as _sys
    if str(AOF_ROOT) not in _sys.path:
        _sys.path.insert(0, str(AOF_ROOT))
    from exporters import okf_service
    return okf_service


def _resolve_bundle_dir(name: str | None, bundle_root: Path) -> tuple[Path, str, str | None]:
    """把 bundle 名解析为目录：name 缺省用根下第一个含 index.md 的包."""
    import sys as _sys
    if str(AOF_ROOT) not in _sys.path:
        _sys.path.insert(0, str(AOF_ROOT))
    from exporters.okf_service import list_bundles as _list
    listing = _list(bundle_root)
    error = None
    if name:
        candidate = bundle_root / name
        if not (candidate / 'index.md').is_file():
            error = f'知识包不存在: {name}'
            return candidate, name, error
        return candidate, name, None
    # 未指定 name：取第一个知识包
    if listing['bundles']:
        first = listing['bundles'][0]
        return Path(first['path']), first['name'], None
    return bundle_root, '', '知识包目录为空，无可用知识包'


@app.get('/v1/okf/bundles')
async def okf_list_bundles() -> dict[str, Any]:
    """列出 OKF 知识包根目录下的所有知识包."""
    svc = _okf_service()
    root = _okf_root()
    return svc.list_bundles(root)


@app.get('/v1/okf/bundles/{bundle_name}/index')
async def okf_bundle_index(bundle_name: str) -> dict[str, Any]:
    """读取指定知识包的 index.md 渐进式披露目录."""
    svc = _okf_service()
    bindir, _, error = _resolve_bundle_dir(bundle_name, _okf_root())
    if error:
        return {'bundle_dir': str(bindir), 'exists': False, 'error': error}
    return svc.read_index(bindir)


@app.get('/v1/okf/bundles/{bundle_name}/concept')
async def okf_get_concept(bundle_name: str, path: str) -> dict[str, Any]:
    """读取指定知识包内单个 Concept 完整内容."""
    svc = _okf_service()
    bindir, _, error = _resolve_bundle_dir(bundle_name, _okf_root())
    if error:
        return {'error': error, 'exists': False}
    return svc.get_concept(bindir, path)


@app.get('/v1/okf/bundles/{bundle_name}/search')
async def okf_search_concepts(
    bundle_name: str,
    query: str | None = None,
    type: str | None = None,  # noqa: A002 - FastAPI 参数名
    title: str | None = None,
    tag: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """按 type/title/tag/正文搜索知识包内 Concept."""
    svc = _okf_service()
    bindir, _, error = _resolve_bundle_dir(bundle_name, _okf_root())
    if error:
        return {'bundle_dir': str(bindir), 'error': error, 'count': 0, 'results': []}
    return svc.search_concepts(bindir, query=query, node_type=type, title=title, tag=tag, limit=limit)


@app.post('/v1/okf/bundles/{bundle_name}/lint')
async def okf_lint_bundle(bundle_name: str) -> dict[str, Any]:
    """对指定知识包运行结构体检（断链/重复/口径冲突）."""
    svc = _okf_service()
    bindir, _, error = _resolve_bundle_dir(bundle_name, _okf_root())
    if error:
        return {'error': error, 'concepts_scanned': 0, 'issues': []}
    return svc.lint_bundle(bindir)


@app.post('/v1/okf/export')
async def okf_export_dataset(
    dataset_name: str | None = None,
    dataset_id: str | None = None,
    output_dir: str | None = None,
    bundle_title: str | None = None,
) -> dict[str, Any]:
    """把数据集导出为 OKF 知识包（供 Web UI 创建动作调用）."""
    import sys as _sys
    if str(AOF_ROOT) not in _sys.path:
        _sys.path.insert(0, str(AOF_ROOT))
    from exporters.okf_exporter import OKFExporter

    exp = OKFExporter(cognee_root=None)
    out = Path(output_dir) if output_dir else None
    result = await exp.export(
        output_dir=str(out) if out else '/tmp/aof_okf_export',
        dataset_id=dataset_id,
        dataset_name=dataset_name,
        bundle_title=bundle_title,
    )
    return result.to_dict()


# ==================== RAG Retrieval Endpoints ====================

class RagRetrieveReq(BaseModel):
    """RAG 统一检索请求（多路召回 + 命中溯源）."""
    query: str = Field(..., min_length=1, description='检索查询')
    dataset_id: str | None = Field(default=None, description='数据集 ID（可选）')
    dataset_name: str | None = Field(default=None, description='数据集名称（可选，默认取 spec.dataset）')
    limit: int = Field(default=10, ge=1, le=50, description='返回结果数')
    expansion: bool = Field(default=False, description='是否开启查询扩展')
    include_graph: bool = Field(default=True, description='是否包含图谱路径召回（第三路）')


# ==================== Decision provenance (PROV-O-inspired) ====================

class DecisionEvidenceReq(BaseModel):
    id: str = Field(min_length=1, description='不可变证据实体 ID，例如 document:abc#chunk-3')
    type: str = 'evidence'
    uri: Optional[str] = None
    content_hash: Optional[str] = None
    description: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DecisionRecordReq(BaseModel):
    agent_id: str = Field(min_length=1)
    decision_type: str = Field(min_length=1)
    conclusion: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    evidence: list[DecisionEvidenceReq] = Field(default_factory=list)
    parent_decision_ids: list[str] = Field(default_factory=list)
    output_entities: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    policies: list[str] = Field(default_factory=list)
    status: str = 'completed'
    tenant_id: Optional[str] = None
    session_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    decision_id: Optional[str] = None


class DecisionPrecedentReq(BaseModel):
    decision_type: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    tenant_id: Optional[str] = None
    limit: int = Field(default=20, ge=1, le=100)


class DecisionImpactReq(BaseModel):
    decision_id: str = Field(min_length=1)
    max_depth: int = Field(default=8, ge=1, le=50)


def _decision_store():
    from bridge.decision_provenance import DecisionProvenanceStore
    return DecisionProvenanceStore(AOF_ROOT / 'data' / 'audit' / 'decision_provenance.jsonl')


def _decision_error(exc: Exception) -> HTTPException:
    message = str(exc)
    return HTTPException(status_code=404 if message.startswith('decision not found:') else 422, detail=message)


@app.post('/v1/decisions', status_code=201)
async def record_decision(req: DecisionRecordReq) -> dict[str, Any]:
    """Record an Agent Decision Activity and its evidence/causal predecessors."""
    try:
        return _decision_store().record(**req.model_dump())
    except Exception as exc:
        raise _decision_error(exc) from exc


@app.get('/v1/decisions/{decision_id}')
async def get_decision(decision_id: str) -> dict[str, Any]:
    entry = _decision_store().get(decision_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f'decision not found: {decision_id}')
    return entry


@app.get('/v1/decisions/{decision_id}/causal-chain')
async def decision_causal_chain(decision_id: str, direction: str = Query('ancestors'), max_depth: int = Query(8, ge=1, le=50)) -> dict[str, Any]:
    try:
        return _decision_store().causal_chain(decision_id, direction=direction, max_depth=max_depth)
    except Exception as exc:
        raise _decision_error(exc) from exc


@app.post('/v1/decisions/precedents/search')
async def search_decision_precedents(req: DecisionPrecedentReq) -> dict[str, Any]:
    return {'results': _decision_store().find_precedents(**req.model_dump())}


@app.post('/v1/decisions/impact')
async def decision_impact(req: DecisionImpactReq) -> dict[str, Any]:
    try:
        return _decision_store().impact(**req.model_dump())
    except Exception as exc:
        raise _decision_error(exc) from exc


@app.get('/v1/decisions/{decision_id}/audit-trail')
async def decision_audit_trail(decision_id: str) -> dict[str, Any]:
    try:
        return _decision_store().audit_trail(decision_id)
    except Exception as exc:
        raise _decision_error(exc) from exc


# ==================== Semantic IR proposal & Knowledge Release governance ====================

class SemanticProposalCreateReq(BaseModel):
    proposal_id: str = Field(min_length=1)
    release_id: str = Field(min_length=1)
    resources: list[dict[str, Any]] = Field(min_length=1)
    actor: Optional[str] = None  # deprecated; ignored in favor of signed principal
    rationale: str = Field(min_length=1)
    parent_release: Optional[str] = None
    scope: dict[str, Any] = Field(default_factory=dict)


class SemanticActorReq(BaseModel):
    actor: Optional[str] = None  # deprecated; ignored in favor of signed principal


class SemanticReviewReq(SemanticActorReq):
    rationale: str = Field(min_length=1)


class SemanticWaiverReq(SemanticReviewReq):
    finding_id: str = Field(min_length=1)
    policy: str = Field(min_length=1)


class SemanticCompileReq(SemanticActorReq):
    targets: list[str] = Field(min_length=1)


class SemanticCompilerContextReq(BaseModel):
    release: dict[str, Any]
    resources: list[dict[str, Any]] = Field(min_length=1)
    policy: dict[str, Any]
    targets: list[str] = Field(default_factory=list)
    waivers: list[dict[str, Any]] = Field(default_factory=list)


class SemanticCompilerRunReq(SemanticCompilerContextReq):
    run_id: str = Field(min_length=1)
    expected_plan_digest: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class SemanticCompilerReplayReq(SemanticCompilerContextReq):
    source_run_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class SemanticCompilerChannelReq(BaseModel):
    run_id: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    approval_decision_id: Optional[str] = None


class SemanticCompilerRollbackReq(BaseModel):
    channel: str = Field(min_length=1)
    to_run_id: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class SemanticQueryReq(BaseModel):
    channel: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    query: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    policy_resource_id: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    waivers: list[dict[str, Any]] = Field(default_factory=list)
    session_id: Optional[str] = None


def _semantic_governance():
    from bridge.semantic_core.compilers import default_compiler_registry
    from bridge.semantic_core.governance import SemanticGovernancePolicy, SemanticGovernanceService
    from bridge.semantic_core.releases import SqliteReleaseRepository
    from bridge.semantic_core.attestations import HmacReleaseAttestor
    from bridge.semantic_core.validators import (
        ontology_release_validator,
        semantic_query_regression_validator,
    )

    governance_root = AOF_ROOT / 'data' / 'semantic_governance'
    signing_secret = os.environ.get('AOF_RELEASE_SIGNING_SECRET', '').encode('utf-8')
    attestor = HmacReleaseAttestor(
        key_id=os.environ.get('AOF_RELEASE_SIGNING_KEY_ID', 'release-key-default'),
        secret=signing_secret,
    ) if signing_secret else None
    return SemanticGovernanceService(
        governance_root,
        decision_store=_decision_store(),
        compiler_registry=default_compiler_registry(),
        validators=[ontology_release_validator, semantic_query_regression_validator],
        release_repository=SqliteReleaseRepository(governance_root / 'releases.sqlite3'),
        access_policy=SemanticGovernancePolicy(),
        release_attestor=attestor,
    )


def _semantic_compiler_control():
    from bridge.semantic_core.compilers import CompilerControlPlane, default_compiler_registry
    from bridge.semantic_core.identity import SignedPrincipalVerifier

    secret = os.environ.get('AOF_SEMANTIC_IDENTITY_SECRET', '').encode('utf-8')
    if not secret:
        raise HTTPException(status_code=503, detail='semantic identity verifier is not configured')
    return CompilerControlPlane(
        AOF_ROOT / 'data' / 'semantic_compiler',
        verifier=SignedPrincipalVerifier(
            key_id=os.environ.get('AOF_SEMANTIC_IDENTITY_KEY_ID', 'identity-key-default'),
            secret=secret,
        ),
        registry=default_compiler_registry(),
        decision_store=_decision_store(),
    )


def _semantic_query_control():
    from bridge.semantic_core import QueryControlPlane, SignedPrincipalVerifier

    secret = os.environ.get('AOF_SEMANTIC_IDENTITY_SECRET', '').encode('utf-8')
    if not secret:
        raise HTTPException(status_code=503, detail='semantic identity verifier is not configured')
    state_root = Path(
        os.environ.get(
            'AOF_COMPILER_STATE_DIR', str(AOF_ROOT / 'data' / 'semantic_compiler')
        )
    )
    return QueryControlPlane(
        state_root,
        verifier=SignedPrincipalVerifier(
            key_id=os.environ.get('AOF_SEMANTIC_IDENTITY_KEY_ID', 'identity-key-default'),
            secret=secret,
        ),
        decision_store=_decision_store(),
    )


def _semantic_governance_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    message = str(exc)
    if 'not found:' in message:
        status_code = 404
    elif any(token in message for token in ('cannot ', 'already exists:', 'already waived:', 'blocked by', 'cannot be overwritten:', 'separation of duties')):
        status_code = 409
    else:
        status_code = 422
    return HTTPException(status_code=status_code, detail=message)


def _semantic_compiler_error(exc: Exception) -> HTTPException:
    from bridge.semantic_core.identity import PrincipalVerificationError

    if isinstance(exc, HTTPException):
        return exc
    message = str(exc)
    if isinstance(exc, PrincipalVerificationError):
        status_code = 401
    elif 'not found:' in message:
        status_code = 404
    elif any(token in message for token in ('already exists:', 'cannot be overwritten:', 'already points', 'already been used', 'separation')):
        status_code = 409
    else:
        status_code = 422
    return HTTPException(status_code=status_code, detail=message)


def _semantic_query_error(exc: Exception) -> HTTPException:
    from bridge.semantic_core import PrincipalVerificationError

    if isinstance(exc, HTTPException):
        return exc
    message = str(exc)
    if isinstance(exc, PrincipalVerificationError):
        status_code = 401
    elif 'not found:' in message or 'not in the trusted release:' in message:
        status_code = 404
    else:
        status_code = 422
    return HTTPException(status_code=status_code, detail=message)


def _semantic_principal(request: Request, action: str):
    from bridge.semantic_core.identity import PrincipalVerificationError, SignedPrincipalVerifier

    secret = os.environ.get('AOF_SEMANTIC_IDENTITY_SECRET', '').encode('utf-8')
    if not secret:
        raise HTTPException(status_code=503, detail='semantic identity verifier is not configured')
    verifier = SignedPrincipalVerifier(
        key_id=os.environ.get('AOF_SEMANTIC_IDENTITY_KEY_ID', 'identity-key-default'),
        secret=secret,
    )
    try:
        principal = verifier.verify(request.headers)
        return principal, principal.actor_for(action)
    except PrincipalVerificationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@app.post('/v1/semantic/proposals', status_code=201)
async def create_semantic_proposal(req: SemanticProposalCreateReq, request: Request) -> dict[str, Any]:
    from bridge.semantic_core import SemanticResource

    try:
        principal, actor = _semantic_principal(request, 'create')
        payload = req.model_dump()
        payload.pop('actor', None)
        payload['resources'] = [SemanticResource.from_dict(item) for item in payload['resources']]
        payload['actor'] = actor
        payload['scope'] = {**payload['scope'], 'tenant_id': principal.tenant_id}
        return _semantic_governance().create_proposal(**payload)
    except Exception as exc:
        raise _semantic_governance_error(exc) from exc


@app.get('/v1/semantic/proposals/{proposal_id}')
async def get_semantic_proposal(proposal_id: str, request: Request) -> dict[str, Any]:
    try:
        principal, _ = _semantic_principal(request, 'read')
        return _semantic_governance().get_proposal(proposal_id, tenant_id=principal.tenant_id)
    except Exception as exc:
        raise _semantic_governance_error(exc) from exc


@app.post('/v1/semantic/proposals/{proposal_id}/validate')
async def validate_semantic_proposal(proposal_id: str, req: SemanticActorReq, request: Request) -> dict[str, Any]:
    try:
        principal, actor = _semantic_principal(request, 'validate')
        service = _semantic_governance()
        service.get_proposal(proposal_id, tenant_id=principal.tenant_id)
        return service.validate(proposal_id, actor=actor)
    except Exception as exc:
        raise _semantic_governance_error(exc) from exc


@app.get('/v1/semantic/proposals/{proposal_id}/impact')
async def semantic_proposal_impact(proposal_id: str, request: Request) -> dict[str, Any]:
    try:
        principal, _ = _semantic_principal(request, 'read')
        return _semantic_governance().impact(proposal_id, tenant_id=principal.tenant_id)
    except Exception as exc:
        raise _semantic_governance_error(exc) from exc


@app.post('/v1/semantic/proposals/{proposal_id}/waivers', status_code=201)
async def waive_semantic_finding(proposal_id: str, req: SemanticWaiverReq, request: Request) -> dict[str, Any]:
    try:
        principal, actor = _semantic_principal(request, 'waive')
        service = _semantic_governance()
        service.get_proposal(proposal_id, tenant_id=principal.tenant_id)
        payload = req.model_dump()
        payload.pop('actor', None)
        payload['actor'] = actor
        return service.waive_finding(proposal_id, **payload)
    except Exception as exc:
        raise _semantic_governance_error(exc) from exc


@app.post('/v1/semantic/proposals/{proposal_id}/request-changes')
async def request_semantic_changes(proposal_id: str, req: SemanticReviewReq, request: Request) -> dict[str, Any]:
    try:
        principal, actor = _semantic_principal(request, 'request_changes')
        service = _semantic_governance()
        service.get_proposal(proposal_id, tenant_id=principal.tenant_id)
        return service.request_changes(proposal_id, actor=actor, rationale=req.rationale)
    except Exception as exc:
        raise _semantic_governance_error(exc) from exc


@app.post('/v1/semantic/proposals/{proposal_id}/approve')
async def approve_semantic_proposal(proposal_id: str, req: SemanticReviewReq, request: Request) -> dict[str, Any]:
    try:
        principal, actor = _semantic_principal(request, 'approve')
        service = _semantic_governance()
        service.get_proposal(proposal_id, tenant_id=principal.tenant_id)
        return service.approve(proposal_id, actor=actor, rationale=req.rationale)
    except Exception as exc:
        raise _semantic_governance_error(exc) from exc


@app.post('/v1/semantic/proposals/{proposal_id}/compile')
async def compile_semantic_proposal(proposal_id: str, req: SemanticCompileReq, request: Request) -> dict[str, Any]:
    try:
        principal, actor = _semantic_principal(request, 'compile')
        service = _semantic_governance()
        service.get_proposal(proposal_id, tenant_id=principal.tenant_id)
        return service.compile(proposal_id, actor=actor, targets=req.targets)
    except Exception as exc:
        raise _semantic_governance_error(exc) from exc


@app.post('/v1/semantic/proposals/{proposal_id}/publish')
async def publish_semantic_proposal(proposal_id: str, req: SemanticActorReq, request: Request) -> dict[str, Any]:
    try:
        principal, actor = _semantic_principal(request, 'publish')
        service = _semantic_governance()
        service.get_proposal(proposal_id, tenant_id=principal.tenant_id)
        return service.publish(proposal_id, actor=actor)
    except Exception as exc:
        raise _semantic_governance_error(exc) from exc


@app.get('/v1/semantic/releases/{release_id}')
async def get_semantic_release(
    release_id: str, request: Request
) -> dict[str, Any]:
    try:
        principal, _ = _semantic_principal(request, 'read')
        repository = _semantic_governance().release_repository
        release = repository.get(release_id, tenant_id=principal.tenant_id)
    except Exception as exc:
        raise _semantic_governance_error(exc) from exc
    if release is None:
        raise HTTPException(status_code=404, detail=f'release not found: {release_id}')
    return release.to_dict()


@app.post('/v1/semantic/compiler/plan')
async def plan_semantic_compilation(
    req: SemanticCompilerContextReq, request: Request
) -> dict[str, Any]:
    try:
        return _semantic_compiler_control().plan(req.model_dump(), headers=request.headers)
    except Exception as exc:
        raise _semantic_compiler_error(exc) from exc


@app.post('/v1/semantic/compiler/evaluate')
async def evaluate_semantic_compilation(
    req: SemanticCompilerContextReq, request: Request
) -> dict[str, Any]:
    try:
        return _semantic_compiler_control().evaluate(req.model_dump(), headers=request.headers)
    except Exception as exc:
        raise _semantic_compiler_error(exc) from exc


@app.post('/v1/semantic/compiler/runs', status_code=201)
async def execute_semantic_compilation(
    req: SemanticCompilerRunReq, request: Request
) -> dict[str, Any]:
    try:
        return _semantic_compiler_control().execute(req.model_dump(), headers=request.headers)
    except Exception as exc:
        raise _semantic_compiler_error(exc) from exc


@app.post('/v1/semantic/compiler/runs/replay', status_code=201)
async def replay_semantic_compilation(
    req: SemanticCompilerReplayReq, request: Request
) -> dict[str, Any]:
    try:
        return _semantic_compiler_control().replay(req.model_dump(), headers=request.headers)
    except Exception as exc:
        raise _semantic_compiler_error(exc) from exc


@app.get('/v1/semantic/compiler/runs/{run_id}')
async def get_semantic_compilation_run(run_id: str, request: Request) -> dict[str, Any]:
    try:
        return _semantic_compiler_control().get_run(run_id, headers=request.headers)
    except Exception as exc:
        raise _semantic_compiler_error(exc) from exc


@app.post('/v1/semantic/compiler/channels/approvals', status_code=201)
async def approve_semantic_compilation_promotion(
    req: SemanticCompilerChannelReq, request: Request
) -> dict[str, Any]:
    try:
        return _semantic_compiler_control().approve_promotion(
            req.model_dump(exclude_none=True), headers=request.headers
        )
    except Exception as exc:
        raise _semantic_compiler_error(exc) from exc


@app.post('/v1/semantic/compiler/channels/promote')
async def promote_semantic_compilation(
    req: SemanticCompilerChannelReq, request: Request
) -> dict[str, Any]:
    try:
        return _semantic_compiler_control().promote(
            req.model_dump(exclude_none=True), headers=request.headers
        )
    except Exception as exc:
        raise _semantic_compiler_error(exc) from exc


@app.post('/v1/semantic/compiler/channels/rollback')
async def rollback_semantic_compilation(
    req: SemanticCompilerRollbackReq, request: Request
) -> dict[str, Any]:
    try:
        return _semantic_compiler_control().rollback(req.model_dump(), headers=request.headers)
    except Exception as exc:
        raise _semantic_compiler_error(exc) from exc


@app.get('/v1/semantic/compiler/channels/{channel}')
async def get_semantic_compilation_channel(channel: str, request: Request) -> dict[str, Any]:
    try:
        return _semantic_compiler_control().get_channel(channel, headers=request.headers)
    except Exception as exc:
        raise _semantic_compiler_error(exc) from exc


@app.post('/v1/semantic/query')
async def execute_trusted_semantic_query(
    req: SemanticQueryReq, request: Request
) -> dict[str, Any]:
    try:
        return _semantic_query_control().execute(
            req.model_dump(exclude_none=True), headers=request.headers
        )
    except Exception as exc:
        raise _semantic_query_error(exc) from exc


# ==================== Ontology governance & deterministic reasoning ====================

class OntologyDraftCreateReq(BaseModel):
    ontology_id: str = Field(min_length=1)
    created_by: str = Field(min_length=1)
    ontology_text: str = Field(min_length=1)
    shapes_text: str = Field(min_length=1)
    skos_text: str = ''
    base_version: Optional[str] = None


class OntologyDraftUpdateReq(BaseModel):
    actor: str = Field(min_length=1)
    ontology_text: Optional[str] = None
    shapes_text: Optional[str] = None
    skos_text: Optional[str] = None


class OntologyActorReq(BaseModel):
    actor: str = Field(min_length=1)


class OntologyWaiverReq(BaseModel):
    finding_id: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    policy: str = Field(min_length=1)
    expires_at: Optional[str] = None


class OntologyApprovalReq(BaseModel):
    approver: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    policies: list[str] = Field(default_factory=list)


class OntologyChangesReq(BaseModel):
    reviewer: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class DatalogRunReq(BaseModel):
    program: str = Field(min_length=1)
    ruleset_id: str = 'ruleset:default'
    facts: list[dict[str, Any]] = Field(default_factory=list)
    agent_id: str = 'engine:datalog'
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class DatalogRuleSetPublishReq(BaseModel):
    ruleset_id: str = Field(min_length=1)
    program: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    description: str = ''


class DatalogRuleSetRunReq(BaseModel):
    facts: list[dict[str, Any]] = Field(default_factory=list)
    agent_id: str = 'engine:datalog'
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class SparqlQueryReq(BaseModel):
    query: str = Field(min_length=1)


def _ontology_governance():
    from bridge.ontology_governance import OntologyGovernanceService
    return OntologyGovernanceService(AOF_ROOT / 'data' / 'ontology_governance', _decision_store())


def _datalog_rulesets():
    from bridge.ontology_governance import RuleSetRepository
    return RuleSetRepository(AOF_ROOT / 'data' / 'ontology_governance' / 'rulesets', _decision_store())


def _ontology_error(exc: Exception) -> HTTPException:
    message = str(exc)
    return HTTPException(status_code=404 if 'not found:' in message else 409 if any(word in message for word in ('blocked', 'cannot be edited', 'only an approved')) else 422, detail=message)


@app.post('/v1/ontology/drafts', status_code=201)
async def create_ontology_draft(req: OntologyDraftCreateReq) -> dict[str, Any]:
    try:
        return _ontology_governance().create_draft(**req.model_dump())
    except Exception as exc:
        raise _ontology_error(exc) from exc


@app.get('/v1/ontology/drafts')
async def list_ontology_drafts() -> dict[str, Any]:
    drafts = _ontology_governance().list_drafts()
    return {'drafts': drafts, 'count': len(drafts)}


@app.get('/v1/ontology/drafts/{draft_id}')
async def get_ontology_draft(draft_id: str) -> dict[str, Any]:
    try:
        return _ontology_governance().get_draft_bundle(draft_id)
    except Exception as exc:
        raise _ontology_error(exc) from exc


@app.put('/v1/ontology/drafts/{draft_id}')
async def update_ontology_draft(draft_id: str, req: OntologyDraftUpdateReq) -> dict[str, Any]:
    try:
        return _ontology_governance().update_draft(draft_id, **req.model_dump())
    except Exception as exc:
        raise _ontology_error(exc) from exc


@app.post('/v1/ontology/drafts/{draft_id}/validate')
async def validate_ontology_draft(draft_id: str, req: OntologyActorReq) -> dict[str, Any]:
    try:
        return _ontology_governance().validate_draft(draft_id, actor=req.actor)
    except Exception as exc:
        raise _ontology_error(exc) from exc


@app.post('/v1/ontology/drafts/{draft_id}/waivers', status_code=201)
async def waive_ontology_finding(draft_id: str, req: OntologyWaiverReq) -> dict[str, Any]:
    try:
        return _ontology_governance().waive_finding(draft_id, **req.model_dump())
    except Exception as exc:
        raise _ontology_error(exc) from exc


@app.post('/v1/ontology/drafts/{draft_id}/approve')
async def approve_ontology_draft(draft_id: str, req: OntologyApprovalReq) -> dict[str, Any]:
    try:
        return _ontology_governance().approve(draft_id, **req.model_dump())
    except Exception as exc:
        raise _ontology_error(exc) from exc


@app.post('/v1/ontology/drafts/{draft_id}/request-changes')
async def request_ontology_changes(draft_id: str, req: OntologyChangesReq) -> dict[str, Any]:
    try:
        return _ontology_governance().request_changes(draft_id, **req.model_dump())
    except Exception as exc:
        raise _ontology_error(exc) from exc


@app.post('/v1/ontology/drafts/{draft_id}/publish')
async def publish_ontology_draft(draft_id: str, req: OntologyActorReq) -> dict[str, Any]:
    try:
        return _ontology_governance().publish(draft_id, actor=req.actor)
    except Exception as exc:
        raise _ontology_error(exc) from exc


@app.get('/v1/ontology/drafts/{draft_id}/impact')
async def preview_ontology_impact(draft_id: str) -> dict[str, Any]:
    try:
        return _ontology_governance().impact_preview(draft_id)
    except Exception as exc:
        raise _ontology_error(exc) from exc


@app.get('/v1/ontology/releases')
async def list_ontology_releases(ontology_id: Optional[str] = None) -> dict[str, Any]:
    releases = _ontology_governance().list_releases(ontology_id)
    return {'releases': releases, 'count': len(releases)}


@app.get('/v1/ontology/releases/{ontology_id}/{version}')
async def get_ontology_release(ontology_id: str, version: str) -> dict[str, Any]:
    try:
        return _ontology_governance().get_release(ontology_id, version)
    except Exception as exc:
        raise _ontology_error(exc) from exc


@app.post('/v1/reasoning/datalog/run')
async def run_datalog(req: DatalogRunReq) -> dict[str, Any]:
    from bridge.ontology_governance import DatalogEngine
    try:
        facts = [(item['predicate'], item.get('terms', [])) for item in req.facts]
        return DatalogEngine(req.program, ruleset_id=req.ruleset_id).run(
            facts, decision_store=_decision_store(), agent_id=req.agent_id, evidence=req.evidence,
        )
    except Exception as exc:
        raise _ontology_error(exc) from exc


@app.post('/v1/reasoning/rulesets', status_code=201)
async def publish_datalog_ruleset(req: DatalogRuleSetPublishReq) -> dict[str, Any]:
    try:
        return _datalog_rulesets().publish(**req.model_dump())
    except Exception as exc:
        raise _ontology_error(exc) from exc


@app.get('/v1/reasoning/rulesets')
async def list_datalog_rulesets(ruleset_id: Optional[str] = None) -> dict[str, Any]:
    rulesets = _datalog_rulesets().list(ruleset_id)
    return {'rulesets': rulesets, 'count': len(rulesets)}


@app.get('/v1/reasoning/rulesets/{ruleset_id}/{version}')
async def get_datalog_ruleset(ruleset_id: str, version: str) -> dict[str, Any]:
    try:
        return _datalog_rulesets().get(ruleset_id, version)
    except Exception as exc:
        raise _ontology_error(exc) from exc


@app.post('/v1/reasoning/rulesets/{ruleset_id}/{version}/run')
async def run_datalog_ruleset(ruleset_id: str, version: str, req: DatalogRuleSetRunReq) -> dict[str, Any]:
    try:
        facts = [(item['predicate'], item.get('terms', [])) for item in req.facts]
        return _datalog_rulesets().run(ruleset_id, version, facts, agent_id=req.agent_id, evidence=req.evidence)
    except Exception as exc:
        raise _ontology_error(exc) from exc


@app.post('/v1/ontology/releases/{ontology_id}/{version}/sparql')
async def query_ontology_release(ontology_id: str, version: str, req: SparqlQueryReq) -> dict[str, Any]:
    from bridge.ontology_governance import SparqlService
    try:
        release = _ontology_governance().get_release(ontology_id, version)
        rdf_text = release['ontology_text'] + '\n' + release['skos_text']
        return SparqlService(rdf_text, snapshot_id=f'ontology:{ontology_id}:{version}').query(req.query)
    except Exception as exc:
        raise _ontology_error(exc) from exc


@app.post('/v1/rag/retrieve')
async def rag_retrieve(req: RagRetrieveReq) -> dict[str, Any]:
    """RAG 统一检索：关键词 + 向量 + 图谱路径多路召回，RRF 融合，带命中溯源.

    返回结构中每条 result 携带 provenance（来源路 / 数据集 / 种子实体 / 图谱路径 / 关系），
    供 Agent 消费时追踪"为什么命中".
    """
    import sys as _sys
    if str(AOF_ROOT) not in _sys.path:
        _sys.path.insert(0, str(AOF_ROOT))
    from exporters.rag_service import rag_retrieve as _retrieve

    dataset_name = req.dataset_name
    if not dataset_name:
        try:
            spec_path = os.environ.get('AOF_SPEC_PATH', 'aof_spec.example.json')
            sp = Path(spec_path)
            if not sp.is_absolute():
                sp = AOF_ROOT / sp
            if sp.exists():
                dataset_name = (json.loads(sp.read_text(encoding='utf-8')).get('dataset') or '')
        except Exception:
            dataset_name = ''
    result = await _retrieve(
        query=req.query,
        dataset_id=req.dataset_id,
        dataset_name=dataset_name,
        limit=req.limit,
        expansion=req.expansion,
        include_graph=req.include_graph,
    )
    return result.to_dict()


# ==================== Frontend Static Hosting (lightweight deploy) ====================
# 放在所有 API 路由之后，catch-all 最后注册不抢占 /v1/* 等 API。
_app_dist = AOF_ROOT / 'web' / 'dist'
if os.environ.get('AOF_SERVE_WEB', '1') == '1' and _app_dist.is_dir():
    if (_app_dist / 'assets').is_dir():
        app.mount('/assets', StaticFiles(directory=_app_dist / 'assets'), name='aof_assets')

    @app.get('/{full_path:path}', include_in_schema=False)
    async def _spa_fallback(full_path: str):
        target = (_app_dist / full_path).resolve() if full_path else _app_dist
        try:
            target.relative_to(_app_dist.resolve())
        except ValueError:
            return HTMLResponse(content='Not Found', status_code=404)
        if target.is_file():
            return FileResponse(target)
        return FileResponse(_app_dist / 'index.html')

    logging.getLogger(__name__).info('[AOF] 前端静态托管已启用: %s', _app_dist)


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8787)
