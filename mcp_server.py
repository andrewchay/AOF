#!/usr/bin/env python3
"""AOF MCP Server - stdio-based Model Context Protocol server.

Exposes AOF capabilities as MCP tools for Claude Code, Cursor, and other MCP clients.
Since this is a thin wrapper, it delegates all real work to the bridge modules.

Configuration:
  Set AOF_SPEC_PATH to point to your spec JSON (defaults to aof_spec.example.json)

Claude Code config example (~/.claude/server.json):
  {
    "mcpServers": {
      "aof": {
        "command": "/Users/chaihao/LLM/AOF/.venv/bin/python",
        "args": ["/Users/chaihao/LLM/AOF/mcp_server.py"]
      }
    }
  }
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Lightweight JSON-RPC 2.0 MCP implementation
# ---------------------------------------------------------------------------

@dataclass
class McpTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[Any]]


class McpServer:
    def __init__(self, name: str, version: str):
        self.name = name
        self.version = version
        self.tools: dict[str, McpTool] = {}
        self._initialized = False
        self._read_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()

    def register_tool(self, tool: McpTool) -> None:
        self.tools[tool.name] = tool

    async def _send(self, message: dict[str, Any]) -> None:
        async with self._write_lock:
            payload = json.dumps(message, ensure_ascii=False)
            sys.stdout.write(payload + "\n")
            sys.stdout.flush()

    async def _read(self) -> dict[str, Any] | None:
        line = await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)
        if not line:
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    async def run(self) -> None:
        while True:
            msg = await self._read()
            if msg is None:
                break

            msg_id = msg.get("id")
            method = msg.get("method")
            params = msg.get("params", {})

            if method == "initialize":
                self._initialized = True
                await self._send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": self.name, "version": self.version},
                    },
                })
            elif method == "notifications/initialized":
                pass
            elif method == "tools/list":
                await self._send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "tools": [
                            {
                                "name": t.name,
                                "description": t.description,
                                "inputSchema": t.input_schema,
                            }
                            for t in self.tools.values()
                        ]
                    },
                })
            elif method == "tools/call":
                name = params.get("name", "")
                arguments = params.get("arguments", {})
                result = await self._handle_tool_call(name, arguments)
                await self._send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": result,
                })
            else:
                await self._send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                })

    async def _handle_tool_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = self.tools.get(name)
        if not tool:
            return {
                "content": [{"type": "text", "text": f"Error: Unknown tool: {name}"}],
                "isError": True,
            }

        try:
            result = await tool.handler(arguments)
            text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
            return {"content": [{"type": "text", "text": text}], "isError": False}
        except Exception as e:
            traceback_str = traceback.format_exc()
            return {
                "content": [{"type": "text", "text": f"Error: {e}\n{traceback_str}"}],
                "isError": True,
            }


# ---------------------------------------------------------------------------
# AOF Tool implementations
# ---------------------------------------------------------------------------

def _load_spec() -> dict[str, Any]:
    spec_path = os.environ.get("AOF_SPEC_PATH", "aof_spec.example.json")
    p = Path(spec_path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


async def _tool_hybrid_search(args: dict[str, Any]) -> dict[str, Any]:
    from bridge.hybrid_search import AOFHybridSearch

    spec = _load_spec()
    cognee_root = (spec.get("cognee") or {}).get("root")
    engine = AOFHybridSearch(cognee_root=cognee_root)

    result = await engine.hybrid_search(
        query=args["query"],
        dataset_id=args.get("dataset_id"),
        dataset_name=args.get("dataset_name") or (spec.get("dataset")),
        limit=args.get("limit", 10),
        expansion=args.get("expansion", False),
    )
    return {
        "query": result.query,
        "expanded_queries": result.expanded_queries,
        "keyword_count": result.keyword_count,
        "vector_count": result.vector_count,
        "execution_time_ms": result.execution_time_ms,
        "results": [
            {
                "slug": r.slug,
                "type": r.type,
                "chunk_text": r.chunk_text,
                "source": r.source,
                "score": r.score,
            }
            for r in result.results
        ],
    }


async def _tool_enhanced_search(args: dict[str, Any]) -> dict[str, Any]:
    from bridge.enhanced_search import search_with_intent

    result = await search_with_intent(
        query=args["query"],
        user_intent=args.get("user_intent"),
        top_k=args.get("top_k", 10),
    )
    return {
        "query": result.query,
        "search_type_used": result.search_type_used.value,
        "execution_time_ms": result.execution_time_ms,
        "results": result.results,
    }


async def _tool_graph_analytics(args: dict[str, Any]) -> dict[str, Any]:
    from bridge.graph_analytics import GraphAnalytics

    analyzer = GraphAnalytics(dataset_name=args["dataset_name"])
    metrics = await analyzer.compute_all_metrics()
    return metrics.to_dict()


async def _tool_dataset_status(args: dict[str, Any]) -> dict[str, Any]:
    from bridge.dataset_manager import check_dataset_status

    status = await check_dataset_status(args["dataset_id"])
    return {
        "dataset_id": status.dataset_id,
        "pipeline_name": status.pipeline_name,
        "status": status.status,
        "progress": status.progress,
        "message": status.message,
        "last_updated": status.last_updated,
    }


async def _tool_health_check(args: dict[str, Any]) -> dict[str, Any]:
    from bridge.graph_doctor import GraphDoctor

    doctor = GraphDoctor(
        dataset_name=args["dataset_name"],
        dataset_id=args.get("dataset_id"),
        cognee_root=args.get("cognee_root"),
    )
    report = await doctor.full_check()
    return report.to_dict()


async def _tool_export_markdown(args: dict[str, Any]) -> dict[str, Any]:
    from exporters.markdown_exporter import MarkdownExporter

    spec = _load_spec()
    exporter = MarkdownExporter(
        cognee_root=args.get("cognee_root") or (spec.get("cognee") or {}).get("root")
    )
    result = await exporter.export(
        dataset_id=args.get("dataset_id"),
        dataset_name=args.get("dataset_name") or spec.get("dataset"),
        output_dir=args["output_dir"],
        dry_run=args.get("dry_run", False),
    )
    return {
        "output_dir": str(result.output_dir),
        "pages_exported": result.pages_exported,
        "pages_skipped": result.pages_skipped,
        "mode": result.mode,
        "files_created": [str(p) for p in result.files_created],
        "errors": result.errors,
    }


async def _tool_list_datasets(args: dict[str, Any]) -> dict[str, Any]:
    from bridge.dataset_manager import list_all_datasets

    datasets = await list_all_datasets()
    return {
        "datasets": [
            {
                "id": d.id,
                "name": d.name,
                "description": d.description,
                "data_count": d.data_count,
                "status": d.status,
            }
            for d in datasets
        ]
    }


# ---------------------------------------------------------------------------
# OKF / LLM Wiki knowledge consumption tools (P2)
# 复用 exporters.okf_service，与 FastAPI REST 共享同一套知识消费逻辑。
# ---------------------------------------------------------------------------

def _bundle_dir(args: dict[str, Any]) -> Path:
    """解析知识包目录（委托给共享 okf_service，参数 -> AOF_OKF_DIR -> spec -> 默认）."""
    from exporters.okf_service import bundle_dir_from_args
    return bundle_dir_from_args(args, PROJECT_ROOT, _load_spec())


async def _tool_okf_index(args: dict[str, Any]) -> dict[str, Any]:
    """读取知识包 index.md（渐进式披露目录）."""
    from exporters.okf_service import read_index
    bundle = _bundle_dir(args)
    return read_index(bundle)


async def _tool_okf_get_concept(args: dict[str, Any]) -> dict[str, Any]:
    """按 path 读取单个 Concept 完整内容."""
    from exporters.okf_service import get_concept
    bundle = _bundle_dir(args)
    return get_concept(bundle, args.get("path", ""))


async def _tool_okf_search_concepts(args: dict[str, Any]) -> dict[str, Any]:
    """按 type/title/tag/正文文本 搜索知识包内 Concept."""
    from exporters.okf_service import search_concepts
    bundle = _bundle_dir(args)
    return search_concepts(
        bundle,
        query=args.get("query"),
        node_type=args.get("type"),
        title=args.get("title"),
        tag=args.get("tag"),
        limit=int(args.get("limit", 20)),
    )


async def _tool_okf_lint(args: dict[str, Any]) -> dict[str, Any]:
    """对知识包运行结构体检（断链/重复/口径冲突）."""
    from exporters.okf_service import lint_bundle
    bundle = _bundle_dir(args)
    return lint_bundle(bundle)


async def _tool_rag_retrieve(args: dict[str, Any]) -> dict[str, Any]:
    """RAG 统一检索：多路召回（关键词+向量+图谱路径）+ 命中溯源."""
    from exporters.rag_service import rag_retrieve
    dataset_name = args.get("dataset_name") or (args.get("dataset_id") or "")
    result = await rag_retrieve(
        query=args["query"],
        dataset_id=args.get("dataset_id"),
        dataset_name=dataset_name or None,
        limit=int(args.get("limit", 10)),
        expansion=bool(args.get("expansion", False)),
        include_graph=bool(args.get("include_graph", True)),
    )
    return result.to_dict()


async def _tool_record_decision(args: dict[str, Any]) -> dict[str, Any]:
    """Persist an Agent decision with its PROV-style causal metadata."""
    from bridge.decision_provenance import DecisionProvenanceStore
    return DecisionProvenanceStore().record(**args)


async def _tool_decision_audit_trail(args: dict[str, Any]) -> dict[str, Any]:
    """Return a causally complete, integrity-checked compliance trail."""
    from bridge.decision_provenance import DecisionProvenanceStore
    return DecisionProvenanceStore().audit_trail(args["decision_id"])


async def _tool_find_decision_precedents(args: dict[str, Any]) -> dict[str, Any]:
    """Find earlier decisions of the same type, ranked by tag overlap."""
    from bridge.decision_provenance import DecisionProvenanceStore
    return {"results": DecisionProvenanceStore().find_precedents(
        decision_type=args["decision_type"], tags=args.get("tags", []),
        tenant_id=args.get("tenant_id"), limit=int(args.get("limit", 20)),
    )}


def _ontology_governance_service():
    from bridge.decision_provenance import DecisionProvenanceStore
    from bridge.ontology_governance import OntologyGovernanceService
    return OntologyGovernanceService(
        PROJECT_ROOT / "data" / "ontology_governance",
        DecisionProvenanceStore(PROJECT_ROOT / "data" / "audit" / "decision_provenance.jsonl"),
    )


async def _tool_ontology_create_draft(args: dict[str, Any]) -> dict[str, Any]:
    return _ontology_governance_service().create_draft(**args)


async def _tool_ontology_validate_draft(args: dict[str, Any]) -> dict[str, Any]:
    return _ontology_governance_service().validate_draft(args["draft_id"], actor=args["actor"])


async def _tool_ontology_waive_finding(args: dict[str, Any]) -> dict[str, Any]:
    payload = {key: value for key, value in args.items() if key != "draft_id"}
    return _ontology_governance_service().waive_finding(args["draft_id"], **payload)


async def _tool_ontology_approve_draft(args: dict[str, Any]) -> dict[str, Any]:
    payload = {key: value for key, value in args.items() if key != "draft_id"}
    return _ontology_governance_service().approve(args["draft_id"], **payload)


async def _tool_ontology_request_changes(args: dict[str, Any]) -> dict[str, Any]:
    return _ontology_governance_service().request_changes(args["draft_id"], reviewer=args["reviewer"], rationale=args["rationale"])


async def _tool_ontology_publish_draft(args: dict[str, Any]) -> dict[str, Any]:
    return _ontology_governance_service().publish(args["draft_id"], actor=args["actor"])


async def _tool_datalog_reason(args: dict[str, Any]) -> dict[str, Any]:
    from bridge.decision_provenance import DecisionProvenanceStore
    from bridge.ontology_governance import DatalogEngine
    facts = [(item["predicate"], item.get("terms", [])) for item in args.get("facts", [])]
    return DatalogEngine(args["program"], ruleset_id=args.get("ruleset_id", "ruleset:default")).run(
        facts,
        decision_store=DecisionProvenanceStore(PROJECT_ROOT / "data" / "audit" / "decision_provenance.jsonl"),
        agent_id=args.get("agent_id", "engine:datalog"), evidence=args.get("evidence", []),
    )


def _datalog_ruleset_repository():
    from bridge.decision_provenance import DecisionProvenanceStore
    from bridge.ontology_governance import RuleSetRepository
    return RuleSetRepository(
        PROJECT_ROOT / "data" / "ontology_governance" / "rulesets",
        DecisionProvenanceStore(PROJECT_ROOT / "data" / "audit" / "decision_provenance.jsonl"),
    )


async def _tool_publish_datalog_ruleset(args: dict[str, Any]) -> dict[str, Any]:
    return _datalog_ruleset_repository().publish(**args)


async def _tool_run_datalog_ruleset(args: dict[str, Any]) -> dict[str, Any]:
    facts = [(item["predicate"], item.get("terms", [])) for item in args.get("facts", [])]
    return _datalog_ruleset_repository().run(
        args["ruleset_id"], args["version"], facts,
        agent_id=args.get("agent_id", "engine:datalog"), evidence=args.get("evidence", []),
    )


async def _tool_document_parse(args: dict[str, Any]) -> dict[str, Any]:
    """把本地文件解析为干净 Markdown + 元数据（document_parser 阶段 3）。"""
    path = args.get("path")
    if not path:
        return {"error": "path 参数必填", "ok": False}

    # 路径限域（防任意文件读取）
    from bridge.document_parser.security import PathNotAllowedError, validate_parse_path

    try:
        safe_path = validate_parse_path(path)
    except PathNotAllowedError as e:
        return {"error": str(e), "ok": False, "path": path}

    from bridge.document_parser import ParserConfig, parse_document

    config = ParserConfig(lang=args.get("lang", "zh"))
    result = parse_document(safe_path, config=config)
    doc = result.doc
    return {"ok": True,
        "path": str(safe_path),
        "engine": doc.engine,
        "use_raw_path": result.use_raw_path,
        "cached": result.cached,
        "format": "markdown",
        "tables_count": doc.tables_count,
        "pages": doc.pages,
        "content": doc.content,
    }


def _semantic_compiler_control():
    from bridge.decision_provenance import DecisionProvenanceStore
    from bridge.semantic_core.compilers import CompilerControlPlane, default_compiler_registry
    from bridge.semantic_core.identity import SignedPrincipalVerifier

    secret = os.environ.get("AOF_SEMANTIC_IDENTITY_SECRET", "").encode("utf-8")
    if not secret:
        raise ValueError("semantic identity verifier is not configured")
    state_root = Path(
        os.environ.get("AOF_COMPILER_STATE_DIR", str(PROJECT_ROOT / "data" / "semantic_compiler"))
    )
    return CompilerControlPlane(
        state_root,
        verifier=SignedPrincipalVerifier(
            key_id=os.environ.get("AOF_SEMANTIC_IDENTITY_KEY_ID", "identity-key-default"),
            secret=secret,
        ),
        registry=default_compiler_registry(),
        decision_store=DecisionProvenanceStore(),
    )


def _compiler_request(args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    payload = dict(args)
    headers = payload.pop("principal_headers", None)
    if not isinstance(headers, dict):
        raise ValueError("principal_headers must contain signed principal headers")
    return payload, {str(key): str(value) for key, value in headers.items()}


async def _tool_semantic_compile_plan(args: dict[str, Any]) -> dict[str, Any]:
    payload, headers = _compiler_request(args)
    return _semantic_compiler_control().plan(payload, headers=headers)


async def _tool_semantic_compile_evaluate(args: dict[str, Any]) -> dict[str, Any]:
    payload, headers = _compiler_request(args)
    return _semantic_compiler_control().evaluate(payload, headers=headers)


async def _tool_semantic_compile_run(args: dict[str, Any]) -> dict[str, Any]:
    payload, headers = _compiler_request(args)
    return _semantic_compiler_control().execute(payload, headers=headers)


async def _tool_semantic_compile_replay(args: dict[str, Any]) -> dict[str, Any]:
    payload, headers = _compiler_request(args)
    return _semantic_compiler_control().replay(payload, headers=headers)


async def _tool_semantic_compile_approve_promotion(args: dict[str, Any]) -> dict[str, Any]:
    payload, headers = _compiler_request(args)
    return _semantic_compiler_control().approve_promotion(payload, headers=headers)


async def _tool_semantic_compile_promote(args: dict[str, Any]) -> dict[str, Any]:
    payload, headers = _compiler_request(args)
    return _semantic_compiler_control().promote(payload, headers=headers)


async def _tool_semantic_compile_rollback(args: dict[str, Any]) -> dict[str, Any]:
    payload, headers = _compiler_request(args)
    return _semantic_compiler_control().rollback(payload, headers=headers)


async def _tool_semantic_compile_get_run(args: dict[str, Any]) -> dict[str, Any]:
    payload, headers = _compiler_request(args)
    return _semantic_compiler_control().get_run(payload["run_id"], headers=headers)


async def _tool_semantic_compile_get_channel(args: dict[str, Any]) -> dict[str, Any]:
    payload, headers = _compiler_request(args)
    return _semantic_compiler_control().get_channel(payload["channel"], headers=headers)


def _register_semantic_compiler_tools(server: McpServer) -> None:
    principal = {
        "type": "object",
        "description": "Identity-gateway signed X-AOF principal headers.",
    }
    context = {
        "release": {"type": "object"},
        "resources": {"type": "array", "items": {"type": "object"}},
        "policy": {"type": "object"},
        "targets": {"type": "array", "items": {"type": "string"}},
        "waivers": {"type": "array", "items": {"type": "object"}},
        "principal_headers": principal,
    }
    definitions = [
        (
            "aof_semantic_compile_plan",
            "生成无副作用、依赖闭合且版本锁定的语义编译计划。",
            context,
            ["release", "resources", "policy", "targets", "principal_headers"],
            _tool_semantic_compile_plan,
        ),
        (
            "aof_semantic_compile_evaluate",
            "用 revision-addressed Policy 评估编译计划与精确 waiver。",
            context,
            ["release", "resources", "policy", "targets", "principal_headers"],
            _tool_semantic_compile_evaluate,
        ),
        (
            "aof_semantic_compile_run",
            "执行已确认 digest 的编译计划并写入不可变 CompilationRun。",
            {**context, "run_id": {"type": "string"}, "expected_plan_digest": {"type": "string"}, "rationale": {"type": "string"}},
            ["release", "resources", "policy", "targets", "run_id", "expected_plan_digest", "rationale", "principal_headers"],
            _tool_semantic_compile_run,
        ),
        (
            "aof_semantic_compile_replay",
            "独立回放不可变编译运行并核对全部产物摘要。",
            {**context, "source_run_id": {"type": "string"}, "run_id": {"type": "string"}, "rationale": {"type": "string"}},
            ["release", "resources", "policy", "source_run_id", "run_id", "rationale", "principal_headers"],
            _tool_semantic_compile_replay,
        ),
        (
            "aof_semantic_compile_approve_promotion",
            "以签名 reviewer Principal 审批可复现 Run 的环境提升。",
            {"run_id": {"type": "string"}, "channel": {"type": "string"}, "rationale": {"type": "string"}, "principal_headers": principal},
            ["run_id", "channel", "rationale", "principal_headers"],
            _tool_semantic_compile_approve_promotion,
        ),
        (
            "aof_semantic_compile_promote",
            "以签名 publisher Principal 和独立 approval 移动环境指针。",
            {"run_id": {"type": "string"}, "channel": {"type": "string"}, "approval_decision_id": {"type": "string"}, "rationale": {"type": "string"}, "principal_headers": principal},
            ["run_id", "channel", "approval_decision_id", "rationale", "principal_headers"],
            _tool_semantic_compile_promote,
        ),
        (
            "aof_semantic_compile_rollback",
            "把环境指针回滚到该 channel 历史上已提升的不可变 Run。",
            {"channel": {"type": "string"}, "to_run_id": {"type": "string"}, "rationale": {"type": "string"}, "principal_headers": principal},
            ["channel", "to_run_id", "rationale", "principal_headers"],
            _tool_semantic_compile_rollback,
        ),
        (
            "aof_semantic_compile_get_run",
            "读取当前签名 tenant 下的不可变 CompilationRun。",
            {"run_id": {"type": "string"}, "principal_headers": principal},
            ["run_id", "principal_headers"],
            _tool_semantic_compile_get_run,
        ),
        (
            "aof_semantic_compile_get_channel",
            "读取当前签名 tenant 下的环境指针及完整历史。",
            {"channel": {"type": "string"}, "principal_headers": principal},
            ["channel", "principal_headers"],
            _tool_semantic_compile_get_channel,
        ),
    ]
    for name, description, properties, required, handler in definitions:
        server.register_tool(
            McpTool(
                name=name,
                description=description,
                input_schema={"type": "object", "properties": properties, "required": required},
                handler=handler,
            )
        )


# ---------------------------------------------------------------------------
# Build and run server
# ---------------------------------------------------------------------------

def build_server() -> McpServer:
    server = McpServer(name="aof", version="2.1.0")

    server.register_tool(McpTool(
        name="aof_hybrid_search",
        description="执行 AOF 混合搜索（关键词 + 向量 + RRF 融合 + 4 层去重）",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索查询"},
                "dataset_id": {"type": "string", "description": "数据集 ID（可选）"},
                "dataset_name": {"type": "string", "description": "数据集名称（可选，默认从 AOF_SPEC 读取）"},
                "limit": {"type": "integer", "description": "返回结果数量", "default": 10},
                "expansion": {"type": "boolean", "description": "是否开启查询扩展", "default": False},
            },
            "required": ["query"],
        },
        handler=_tool_hybrid_search,
    ))

    server.register_tool(McpTool(
        name="aof_enhanced_search",
        description="使用 Cognee 增强语义搜索（基于意图自动选择搜索类型）",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索查询"},
                "user_intent": {"type": "string", "description": "用户意图，如 analytics/debugging/exploration/temporal"},
                "top_k": {"type": "integer", "description": "返回结果数量", "default": 10},
            },
            "required": ["query"],
        },
        handler=_tool_enhanced_search,
    ))

    server.register_tool(McpTool(
        name="aof_graph_analytics",
        description="运行图谱分析（PageRank、社区检测、中心性、统计指标）",
        input_schema={
            "type": "object",
            "properties": {
                "dataset_name": {"type": "string", "description": "数据集名称"},
            },
            "required": ["dataset_name"],
        },
        handler=_tool_graph_analytics,
    ))

    server.register_tool(McpTool(
        name="aof_dataset_status",
        description="检查数据集的 cognify 处理状态",
        input_schema={
            "type": "object",
            "properties": {
                "dataset_id": {"type": "string", "description": "数据集 ID 或名称"},
            },
            "required": ["dataset_id"],
        },
        handler=_tool_dataset_status,
    ))

    server.register_tool(McpTool(
        name="aof_health_check",
        description="运行 Graph Doctor 健康检查",
        input_schema={
            "type": "object",
            "properties": {
                "dataset_name": {"type": "string", "description": "数据集名称"},
                "dataset_id": {"type": "string", "description": "数据集 ID（可选）"},
            },
            "required": ["dataset_name"],
        },
        handler=_tool_health_check,
    ))

    server.register_tool(McpTool(
        name="aof_export_markdown",
        description="将知识图谱导出为 Markdown 文件（人类可读）",
        input_schema={
            "type": "object",
            "properties": {
                "output_dir": {"type": "string", "description": "输出目录"},
                "dataset_id": {"type": "string", "description": "数据集 ID（可选）"},
                "dataset_name": {"type": "string", "description": "数据集名称（可选）"},
                "dry_run": {"type": "boolean", "description": "是否只预览不写入", "default": False},
            },
            "required": ["output_dir"],
        },
        handler=_tool_export_markdown,
    ))

    server.register_tool(McpTool(
        name="aof_list_datasets",
        description="列出所有可访问的数据集",
        input_schema={
            "type": "object",
            "properties": {},
        },
        handler=_tool_list_datasets,
    ))

    # --- OKF / LLM Wiki knowledge consumption tools ---

    server.register_tool(McpTool(
        name="aof_okf_index",
        description="读取 OKF 知识包的 index.md 渐进式披露目录（Agent 应优先调用以获取全局知识索引）",
        input_schema={
            "type": "object",
            "properties": {
                "bundle_dir": {"type": "string", "description": "知识包目录（可选，默认由 AOF_OKF_DIR 或配置决定）"},
            },
        },
        handler=_tool_okf_index,
    ))

    server.register_tool(McpTool(
        name="aof_okf_get_concept",
        description="按 path 读取单个 OKF Concept 的完整内容（frontmatter + 正文），如 person/alice.md",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Concept 相对路径，如 person/alice.md"},
                "bundle_dir": {"type": "string", "description": "知识包目录（可选）"},
            },
            "required": ["path"],
        },
        handler=_tool_okf_get_concept,
    ))

    server.register_tool(McpTool(
        name="aof_okf_search_concepts",
        description="按 type/title/tag/正文文本搜索 OKF 知识包内的 Concept",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "正文文本关键词搜索"},
                "type": {"type": "string", "description": "按 Concept type 过滤，如 person/company/metric"},
                "title": {"type": "string", "description": "按标题过滤"},
                "tag": {"type": "string", "description": "按标签过滤"},
                "limit": {"type": "integer", "description": "返回最大条数", "default": 20},
            },
        },
        handler=_tool_okf_search_concepts,
    ))

    server.register_tool(McpTool(
        name="aof_okf_lint",
        description="对 OKF 知识包运行结构体检（断链/重复/口径冲突），返回体检报告",
        input_schema={
            "type": "object",
            "properties": {
                "bundle_dir": {"type": "string", "description": "知识包目录（可选）"},
            },
        },
        handler=_tool_okf_lint,
    ))

    server.register_tool(McpTool(
        name="aof_rag_retrieve",
        description="RAG 统一检索：关键词 + 向量 + 图谱路径 多路召回，RRF 融合，每条结果携带 provenance 命中溯源（来源路/数据集/种子实体/图谱路径/关系）。用于 Agent 做知识问答前的事实检索。",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索查询"},
                "dataset_id": {"type": "string", "description": "数据集 ID（可选）"},
                "dataset_name": {"type": "string", "description": "数据集名称（可选，默认取 spec.dataset）"},
                "limit": {"type": "integer", "description": "返回最大条数", "default": 10},
                "expansion": {"type": "boolean", "description": "是否开启查询扩展", "default": False},
                "include_graph": {"type": "boolean", "description": "是否包含图谱路径召回（第三路）", "default": True},
            },
            "required": ["query"],
        },
        handler=_tool_rag_retrieve,
    ))

    server.register_tool(McpTool(
        name="aof_record_decision",
        description="记录 Agent 决策及其证据、前序决策、输出实体和适用策略，生成带哈希链的 PROV-O 风格审计记录。",
        input_schema={
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "作出决策的 Agent ID"},
                "decision_type": {"type": "string", "description": "决策类型，如 deployment_approval"},
                "conclusion": {"type": "string", "description": "最终结论"},
                "rationale": {"type": "string", "description": "可审计的理由"},
                "evidence": {"type": "array", "description": "输入证据实体，每项至少含 id"},
                "parent_decision_ids": {"type": "array", "description": "因果前序 Decision ID"},
                "output_entities": {"type": "array", "description": "本决策生成或改变的实体"},
                "tags": {"type": "array", "description": "用于先例匹配的稳定标签"},
                "policies": {"type": "array", "description": "适用的治理/合规策略引用"},
                "tenant_id": {"type": "string"}, "session_id": {"type": "string"},
                "metadata": {"type": "object"}, "decision_id": {"type": "string"},
            },
            "required": ["agent_id", "decision_type", "conclusion", "rationale"],
        },
        handler=_tool_record_decision,
    ))

    server.register_tool(McpTool(
        name="aof_decision_audit_trail",
        description="追溯一个决策的因果链，返回 PROV-O 风格 JSON-LD、策略引用、证据完整性和账本哈希链校验结果。",
        input_schema={"type": "object", "properties": {"decision_id": {"type": "string"}}, "required": ["decision_id"]},
        handler=_tool_decision_audit_trail,
    ))

    server.register_tool(McpTool(
        name="aof_find_decision_precedents",
        description="按决策类型和标签检索已记录先例，供 Agent 在新决策前比对历史做法。",
        input_schema={
            "type": "object",
            "properties": {"decision_type": {"type": "string"}, "tags": {"type": "array"}, "tenant_id": {"type": "string"}, "limit": {"type": "integer", "default": 20}},
            "required": ["decision_type"],
        },
        handler=_tool_find_decision_precedents,
    ))

    server.register_tool(McpTool(
        name="aof_ontology_create_draft",
        description="创建版本化 OWL + SHACL + SKOS 本体草稿；草稿必须经过校验、审查、审批后才能发布。",
        input_schema={"type": "object", "properties": {
            "ontology_id": {"type": "string"}, "created_by": {"type": "string"},
            "ontology_text": {"type": "string"}, "shapes_text": {"type": "string"},
            "skos_text": {"type": "string"}, "base_version": {"type": "string"},
        }, "required": ["ontology_id", "created_by", "ontology_text", "shapes_text"]},
        handler=_tool_ontology_create_draft,
    ))

    server.register_tool(McpTool(
        name="aof_ontology_validate_draft",
        description="运行 SHACL/OWL/SKOS 发布门禁，生成不可变审查实体；不静默修正违规。",
        input_schema={"type": "object", "properties": {"draft_id": {"type": "string"}, "actor": {"type": "string"}}, "required": ["draft_id", "actor"]},
        handler=_tool_ontology_validate_draft,
    ))

    server.register_tool(McpTool(
        name="aof_ontology_waive_finding",
        description="按策略与理由记录不可变治理豁免；豁免保留在审计链中，不删除原违规。",
        input_schema={"type": "object", "properties": {
            "draft_id": {"type": "string"}, "finding_id": {"type": "string"}, "actor": {"type": "string"},
            "rationale": {"type": "string"}, "policy": {"type": "string"}, "expires_at": {"type": "string"},
        }, "required": ["draft_id", "finding_id", "actor", "rationale", "policy"]},
        handler=_tool_ontology_waive_finding,
    ))

    server.register_tool(McpTool(
        name="aof_ontology_approve_draft",
        description="审批已校验的本体草稿；存在未豁免 Violation 时强制阻断。",
        input_schema={"type": "object", "properties": {
            "draft_id": {"type": "string"}, "approver": {"type": "string"}, "rationale": {"type": "string"}, "policies": {"type": "array"},
        }, "required": ["draft_id", "approver", "rationale"]},
        handler=_tool_ontology_approve_draft,
    ))

    server.register_tool(McpTool(
        name="aof_ontology_request_changes",
        description="在校验或冲突审查后请求修改，记录审查决策并把草稿转入可编辑的新修订流程。",
        input_schema={"type": "object", "properties": {
            "draft_id": {"type": "string"}, "reviewer": {"type": "string"}, "rationale": {"type": "string"},
        }, "required": ["draft_id", "reviewer", "rationale"]},
        handler=_tool_ontology_request_changes,
    ))

    server.register_tool(McpTool(
        name="aof_ontology_publish_draft",
        description="发布已审批草稿，生成 ontologyVersion、shapeVersion、SKOS 版本、变更集与发布决策。",
        input_schema={"type": "object", "properties": {"draft_id": {"type": "string"}, "actor": {"type": "string"}}, "required": ["draft_id", "actor"]},
        handler=_tool_ontology_publish_draft,
    ))

    server.register_tool(McpTool(
        name="aof_datalog_reason",
        description="运行安全、分层、确定性的 Datalog 固定点推理；每个派生事实携带规则版本、绑定和输入证明，并记录决策溯源。",
        input_schema={"type": "object", "properties": {
            "program": {"type": "string"}, "ruleset_id": {"type": "string"}, "facts": {"type": "array"},
            "agent_id": {"type": "string"}, "evidence": {"type": "array"},
        }, "required": ["program", "facts"]},
        handler=_tool_datalog_reason,
    ))

    server.register_tool(McpTool(
        name="aof_publish_datalog_ruleset",
        description="校验并发布内容寻址、不可变的 Datalog 规则集版本，同时记录发布决策。",
        input_schema={"type": "object", "properties": {
            "ruleset_id": {"type": "string"}, "program": {"type": "string"}, "actor": {"type": "string"}, "description": {"type": "string"},
        }, "required": ["ruleset_id", "program", "actor"]},
        handler=_tool_publish_datalog_ruleset,
    ))

    server.register_tool(McpTool(
        name="aof_run_datalog_ruleset",
        description="按不可变规则集版本运行确定性推理，并输出逐事实证明链与决策溯源 ID。",
        input_schema={"type": "object", "properties": {
            "ruleset_id": {"type": "string"}, "version": {"type": "string"}, "facts": {"type": "array"},
            "agent_id": {"type": "string"}, "evidence": {"type": "array"},
        }, "required": ["ruleset_id", "version", "facts"]},
        handler=_tool_run_datalog_ruleset,
    ))

    server.register_tool(McpTool(
        name="aof_document_parse",
        description="把本地文件解析为干净 Markdown + 元数据（document_parser），供下游知识摄取",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "本地文件绝对路径（pdf/docx/pptx/xlsx 等）"},
                "lang": {"type": "string", "description": "解析语言（默认 zh）", "default": "zh"},
            },
            "required": ["path"],
        },
        handler=_tool_document_parse,
    ))

    _register_semantic_compiler_tools(server)

    return server


async def main() -> None:
    server = build_server()
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())
