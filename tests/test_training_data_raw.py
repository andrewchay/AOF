"""真实对话消息 + Agent trajectory 训练样本生成测试."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bridge.training_data.models import GeneratorConfig
from bridge.training_data.raw_trajectory import (
    RawTrajectoryLoader,
    RawTrajectoryGenerator,
)


CONVERSATION_JSONL = "\n".join([
    '{"session_id": "c1", "role": "customer", "message": "我在查库存"}',
    '{"session_id": "c1", "role": "agent", "message": "好的，稍候。"}',
    '{"session_id": "c1", "role": "customer", "message": "缺货了吗？"}',
])


TRAJECTORY_JSON = json.dumps({
    "run_id": "r1",
    "agent": "planner",
    "goal": "判断是否需要补货",
    "steps": [
        {"seq": 1, "action": "reason", "input": "取库存",
         "output": "决定查库存", "tool_call": {"tool": "query_inventory", "args": {"sku": "A"}}},
        {"seq": 2, "action": "decide", "input": "库存低于阈值",
         "output": "创建采购单", "tool_call": {"tool": "create_purchase_order", "args": {"qty": 100}}},
    ],
})


def _write(tmp_path: Path, name: str, content: str) -> str:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


async def _collect(gen: RawTrajectoryGenerator, cfg: GeneratorConfig):
    out = []
    async for sample in gen.generate(None, None, cfg):
        out.append(sample)
    return out


class TestRawTrajectoryLoader:
    def test_load_conversation_groups_by_session(self, tmp_path):
        path = _write(tmp_path, "conv.jsonl", CONVERSATION_JSONL)
        data = RawTrajectoryLoader([path]).load()
        assert len(data["conversations"]) == 1
        assert len(data["conversations"][0]["messages"]) == 3
        assert data["conversations"][0]["session_id"] == "c1"
        assert data["trajectories"] == []

    def test_load_trajectory(self, tmp_path):
        path = _write(tmp_path, "traj.json", TRAJECTORY_JSON)
        data = RawTrajectoryLoader([path]).load()
        assert len(data["trajectories"]) == 1
        assert data["trajectories"][0]["run_id"] == "r1"
        assert len(data["trajectories"][0]["steps"]) == 2

    def test_missing_source_is_ignored(self, tmp_path):
        loader = RawTrajectoryLoader([str(tmp_path / "nope.jsonl")])
        assert loader.load() == {"conversations": [], "trajectories": []}

    def test_invalid_content(self, tmp_path):
        path = _write(tmp_path, "bad.jsonl", "not valid json\n")
        data = RawTrajectoryLoader([path]).load()
        assert data == {"conversations": [], "trajectories": []}

    def test_supported_content_or_message_field(self, tmp_path):
        # 兼容 content 字段名
        path = _write(tmp_path, "c.jsonl", '{ "session_id":"s1","role":"user","content":"hi" }\n')
        data = RawTrajectoryLoader([path]).load()
        assert len(data["conversations"]) == 1


class TestRawTrajectoryGenerator:
    @pytest.mark.asyncio
    async def test_conversation_to_multi_turn_sft(self, tmp_path):
        path = _write(tmp_path, "conv.jsonl", CONVERSATION_JSONL)
        gen = RawTrajectoryGenerator().with_sources([path])
        samples = await _collect(gen, GeneratorConfig(max_samples=100))

        assert len(samples) == 1
        s = samples[0]
        assert s.sample_type.value == "sft"
        assert s.source.source_type == "conversation"
        assert s.source.source_id == "c1"
        roles = [m["role"] for m in s.messages]
        assert roles == ["user", "assistant", "user"]  # customer→user, agent→assistant
        # 忠实保留原文
        assert s.messages[0]["content"] == "我在查库存"

    @pytest.mark.asyncio
    async def test_trajectory_to_reasoning_sft(self, tmp_path):
        path = _write(tmp_path, "traj.json", TRAJECTORY_JSON)
        gen = RawTrajectoryGenerator().with_sources([path])
        samples = await _collect(gen, GeneratorConfig(max_samples=100))

        assert len(samples) == 1
        s = samples[0]
        assert s.source.source_type == "trajectory"
        assert s.source.source_id == "r1"
        assert s.messages[0]["role"] == "system"
        assert s.messages[1]["role"] == "user"
        assert s.messages[1]["content"].startswith("任务：")
        # 2 个 assistant steps + system + 任务 = 4 条
        assert len(s.messages) == 4
        # 推理链包含工具调用
        assert "query_inventory" in s.messages[2]["content"]
        assert "create_purchase_order" in s.messages[3]["content"]
        assert s.metadata["step_count"] == 2
        assert s.metadata["agent"] == "planner"

    @pytest.mark.asyncio
    async def test_no_sources_yields_nothing(self):
        gen = RawTrajectoryGenerator()
        assert await _collect(gen, GeneratorConfig()) == []

    @pytest.mark.asyncio
    async def test_max_samples_cap(self, tmp_path):
        # 3 条单独消息 → 3 个 conversation 样本
        path = _write(
            tmp_path, "multi.jsonl",
            "\n".join(
                f'{{"session_id":"s{i}","role":"user","content":"msg{i}"}}'
                for i in range(5)
            ),
        )
        gen = RawTrajectoryGenerator().with_sources([path])
        samples = await _collect(gen, GeneratorConfig(max_samples=2))
        assert len(samples) == 2

    @pytest.mark.asyncio
    async def test_reads_sources_from_config(self, tmp_path):
        path = _write(tmp_path, "conv.jsonl", CONVERSATION_JSONL)
        gen = RawTrajectoryGenerator()  # 不注入 sources，走 config
        cfg = GeneratorConfig(max_samples=100, raw_sources=[path])
        samples = await _collect(gen, cfg)
        assert len(samples) == 1

    @pytest.mark.asyncio
    async def test_unrecognized_record_is_dropped(self, tmp_path):
        path = _write(tmp_path, "junk.jsonl", '{ "foo": "bar" }\n')
        gen = RawTrajectoryGenerator().with_sources([path])
        assert await _collect(gen, GeneratorConfig()) == []

    def test_requires_data_loaders_is_false(self):
        gen = RawTrajectoryGenerator()
        assert gen.requires_data_loaders is False


class TestRawTrajectoryThroughPipeline:
    @pytest.mark.asyncio
    async def test_pipeline_runs_without_loaders(self, tmp_path):
        """raw 生成器无需图谱/文档后端，pipeline 应能直接运行并输出 jsonl."""
        from bridge.training_data.pipeline import TrainingDataPipeline

        conv = _write(tmp_path, "conv.jsonl", CONVERSATION_JSONL)
        traj = _write(tmp_path, "traj.json", TRAJECTORY_JSON)
        gen = RawTrajectoryGenerator().with_sources([conv, traj])

        out_dir = tmp_path / "out"
        result = await TrainingDataPipeline().run(
            dataset_name="demo",
            generators=[gen],
            output_path=out_dir,
        )
        assert result.total_samples == 2  # 1 对话 + 1 轨迹
        assert result.output_files
        out_file = Path(result.output_files[0])
        assert out_file.exists()
        # 校验 jsonl 内容
        with out_file.open(encoding="utf-8") as fh:
            lines = [json.loads(ln) for ln in fh]
        assert len(lines) == 2
        assert lines[0]["sample_type"] == "sft"
    