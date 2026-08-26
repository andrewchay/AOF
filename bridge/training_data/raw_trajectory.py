"""真实对话消息 + Agent trajectory 训练样本生成器.

将真实的企业对话消息和 Agent 运行轨迹（含工具调用）直接转换为 SFT 训练样本，
**不需要**先经过「图谱→模板合成」链路，而是忠实保留真实数据中的对话轮次与推理步骤。

核心动机（对照 AOF 既有三生成器）:
- 既有 sft/rag_eval/agent_tool 都是「知识图谱/文档 → 模板化合成样本」，
  无法把真实的历史对话、真实 Agent 运行轨迹作为训练样本。
- 本模块从**原始轨迹文件**读取数据，产出忠实于真实输入的 SFTSample：
    * 企业对话（role/content 序列）       → 每个 session 一个多轮 SFTSample
    * Agent trajectory（含 tool_call 序列） → 每个 run 一个带推理链的 SFTSample

输入格式（支持 jsonl / json 数组 / json 对象）:
1. 对话消息:
   ```jsonl
   {"session_id": "c1", "role": "user",    "content": "我的笔记本黑屏了"}
   {"session_id": "c1", "role": "assistant", "content": "请问电源指示灯亮吗？"}
   ```
2. Agent 轨迹:
   ```json
   {"run_id": "r1", "agent": "planner", "goal": "...",
    "steps": [
      {"seq":1, "action":"reason", "input":"取库存", "output":"决定查工具", "tool_call": {"tool":"query_inventory","args":{...}}},
      {"seq":2, "action":"decide", "input":"...", "output":"批准PO", "tool_call": null}
    ]}
   ```

用法:
    from bridge.training_data.generators.raw_trajectory import RawTrajectoryGenerator
    gen = RawTrajectoryGenerator().with_sources(["/path/sample_conversation.jsonl"])
    async for sample in gen.generate(graph_loader, doc_loader, config):
        ...
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional

from .models import (
    GeneratorConfig,
    SFTSample,
    SampleSource,
    TrainingSample,
)
from .loaders import GraphLoader, DocumentLoader
from .generators.base import GeneratorBase

logger = logging.getLogger(__name__)

# 轨迹动作 → 推理链的提示词（用于把真实步骤转成带推理的教学样本）
TRAJECTORY_SYSTEM_PROMPT = (
    "你是一个企业智能助手，在完成用户任务时会先推理、再决定、必要时调用工具。"
    "以下展示了你完成一次任务时的真实思考与行动过程。"
)


def _get_text(rec: dict[str, Any]) -> str:
    """从记录中提取正文（兼容 content / message 两种字段名）."""
    for key in ("content", "message", "text", "body"):
        val = rec.get(key)
        if isinstance(val, str):
            return val
        if val is not None:
            return str(val)
    return ""


def _safe_load_jsonl(path: Path) -> list[dict[str, Any]]:
    """加载 jsonl 文件."""
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    records.append(obj)
                elif isinstance(obj, list):
                    records.extend(obj)
            except json.JSONDecodeError:
                logger.warning(f"[RawTrajectory] skip malformed jsonl line in {path}")
    return records


def _safe_load_json(path: Path) -> Any:
    """加载 json 文件（对象或数组）. 失败时尝试按 jsonl 兜底."""
    try:
        with path.open(encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        logger.warning(f"[RawTrajectory] cannot read {path}: {e}")
        return None
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # 可能实际是 jsonl 或混合格式 → 逐行解析
        try:
            return _safe_load_jsonl(path)
        except Exception:
            logger.warning(f"[RawTrajectory] invalid json in {path}")
            return None


class RawTrajectoryLoader:
    """从原始轨迹/对话文件加载结构化数据.

    纯本地解析，不依赖图谱后端或数据集管理器。
    解析结果做两种正交归类：
        - conversation: 按 session_id 聚合的〔role/content 消息序列〕，供多轮 SFT
        - trajectory:    含 steps 的完整 Agent 运行记录，供带推理链的 SFT
    """

    SUPPORTED_EXTS = {".jsonl", ".json"}

    def __init__(self, sources: Optional[list[str]] = None):
        self.sources = [s for s in (sources or []) if s]

    def add_source(self, path: str) -> None:
        self.sources.append(path)

    @property
    def is_configured(self) -> bool:
        return bool(self.sources)

    def load(self) -> dict[str, list[dict[str, Any]]]:
        """解析所有源文件，归一化为〔conversations / trajectories〕两组记录.

        Returns:
            {
                "conversations": [{"session_id":..., "messages":[{role,content}, ...]}, ...],
                "trajectories":  [{"run_id":..., "agent":..., "goal":..., "steps":[...]}, ...],
            }
        """
        conv_by_session: dict[str, dict[str, Any]] = {}
        trajectories: list[dict[str, Any]] = []
        messages_without_session: list[dict[str, Any]] = []

        for src in self.sources:
            path = Path(src)
            if not path.exists():
                logger.warning(f"[RawTrajectory] source not found: {src}")
                continue
            records: Any = None
            if path.suffix.lower() == ".jsonl":
                records = _safe_load_jsonl(path)
            else:
                data = _safe_load_json(path)
                records = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
            if not isinstance(records, list):
                continue

            for rec in records:
                if not isinstance(rec, dict):
                    continue
                if "steps" in rec and isinstance(rec.get("steps"), list):
                    trajectories.append(rec)
                    continue
                # 对话消息形态：必须有内容字段（content 或 message）
                if ("content" in rec or "message" in rec) and "role" in rec:
                    session_id = rec.get("session_id") or rec.get("conversation") or rec.get("id")
                    if session_id:
                        holder = conv_by_session.setdefault(
                            str(session_id),
                            {"session_id": str(session_id), "messages": []},
                        )
                        holder["messages"].append(rec)
                    else:
                        messages_without_session.append(rec)

        # 无 session 的单条消息：每条独立成一段（单轮）
        if messages_without_session:
            for i, msg in enumerate(messages_without_session):
                conv_by_session.setdefault(
                    f"bare_{i}", {"session_id": None, "messages": [msg]}
                )

        return {
            "conversations": list(conv_by_session.values()),
            "trajectories": trajectories,
        }


class RawTrajectoryGenerator(GeneratorBase):
    """从真实对话 / Agent 轨迹生成 SFT 训练样本."""

    def __init__(self, name: Optional[str] = None, sources: Optional[list[str]] = None):
        super().__init__(name)
        self._sources: list[str] = list(sources or [])

    @property
    def sample_type(self) -> str:
        return "sft"  # 产出 SFTSample

    @property
    def requires_data_loaders(self) -> bool:
        # 直接从原始文件读取，不依赖图谱/文档后端
        return False

    @property
    def bypass_quality_filter(self) -> bool:
        # 忠实保留真实对话/轨迹，不为合成样本设计的长度阈值而误杀真实短消息
        return True

    def with_sources(self, sources: list[str]) -> "RawTrajectoryGenerator":
        """注入源文件路径（链式）."""
        self._sources.extend(sources)
        return self

    async def estimate_yield(
        self,
        node_count: int,
        edge_count: int,
        document_count: int,
    ) -> int:
        return len(self._sources)  # 粗略：一个文件约一个样本（会话/轨迹）

    async def generate(
        self,
        graph_loader: Optional[GraphLoader],
        doc_loader: Optional[DocumentLoader],
        config: GeneratorConfig,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> AsyncIterator[TrainingSample]:
        """生成 SFT 样本.

        数据来源优先级：生成器自身注入的 _sources > config.raw_sources。
        不依赖 graph_loader / doc_loader，二者可为 None。
        """
        sources = self._sources or list(getattr(config, "raw_sources", None) or [])
        loader = RawTrajectoryLoader(sources)
        if not loader.is_configured:
            logger.warning("[RawTrajectory] no raw sources configured, yielding nothing")
            return

        data = loader.load()
        conversations: list[dict[str, Any]] = data["conversations"]
        trajectories: list[dict[str, Any]] = data["trajectories"]
        total_estimate = len(conversations) + len(trajectories)
        count = 0

        # 1) 对话 → 多轮 SFTSample
        for conv in conversations:
            if count >= config.max_samples:
                break
            sample = self._conversation_to_sample(conv)
            if sample is not None:
                count += 1
                self._report_progress(count, total_estimate, "conversation→sft", progress_callback)
                yield sample

        # 2) 轨迹 → 带推理链 SFTSample
        for traj in trajectories:
            if count >= config.max_samples:
                break
            sample = self._trajectory_to_sample(traj)
            if sample is not None:
                count += 1
                self._report_progress(count, total_estimate, "trajectory→sft", progress_callback)
                yield sample

    # ------------------------------------------------------------------
    # 内部：记录 → SFTSample
    # ------------------------------------------------------------------
    def _conversation_to_sample(self, conv: dict[str, Any]) -> Optional[SFTSample]:
        """一组对话消息（同一 session）→ 一个多轮 SFTSample."""
        messages: list[dict[str, Any]] = []
        role_map = {
            "customer": "user", "user": "user", "human": "user", "patient": "user",
            "agent": "assistant", "assistant": "assistant", "support": "assistant", "assistant_agent": "assistant",
        }
        for msg in conv.get("messages", []):
            content = _get_text(msg).strip()
            if not content:
                continue
            role_raw = str(msg.get("role", "user")).lower()
            messages.append({"role": role_map.get(role_raw, "user"), "content": content})

        if not messages:
            return None
        session_id = conv.get("session_id") or conv.get("id") or "conversation"
        return SFTSample(
            messages=messages,
            system_prompt="",
            source=SampleSource(
                dataset_name="",
                source_type="conversation",
                source_id=str(session_id),
            ),
            metadata={"session_id": str(session_id), "origin": "raw_conversation",
                      "message_count": len(messages)},
        )

    def _trajectory_to_sample(self, record: dict[str, Any]) -> Optional[SFTSample]:
        """一段 Agent 轨迹 → 一个带推理链的 SFTSample."""
        steps = record.get("steps")
        if not isinstance(steps, list) or not steps:
            return None
        run_id = record.get("run_id") or record.get("id") or "agent_run"
        agent = record.get("agent") or record.get("agent_id") or "agent"
        goal = record.get("goal") or record.get("task") or ""

        messages: list[dict[str, Any]] = [{"role": "system", "content": TRAJECTORY_SYSTEM_PROMPT}]
        if goal:
            messages.append({"role": "user", "content": f"任务：{goal}"})

        for step in steps:
            task_input = (step.get("input") or "").strip()
            output = (step.get("output") or "").strip()
            action = (step.get("action") or "step").strip()
            tool = step.get("tool_call")

            reasoning_lines: list[str] = []
            if task_input:
                reasoning_lines.append(f"输入：{task_input}")
            reasoning_lines.append(f"动作（{action}）")
            if isinstance(tool, dict) and tool:
                tname = tool.get("tool")
                targs = json.dumps(tool.get("args", {}), ensure_ascii=False)
                reasoning_lines.append(f"调用工具 {tname}，参数 {targs}")
            else:
                reasoning_lines.append("（未调用工具）")
            if output:
                reasoning_lines.append(f"结果：{output}")
            messages.append({"role": "assistant", "content": "\n".join(reasoning_lines)})

        return SFTSample(
            messages=messages,
            system_prompt=TRAJECTORY_SYSTEM_PROMPT,
            source=SampleSource(
                dataset_name="",
                source_type="trajectory",
                source_id=str(run_id),
            ),
            metadata={"run_id": str(run_id), "agent": str(agent), "step_count": len(steps)},
        )
