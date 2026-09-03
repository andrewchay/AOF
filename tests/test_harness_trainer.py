"""Agent Harness Trainer 测试."""

from __future__ import annotations


from bridge.harness_trainer import (
    AssetChange,
    AssetType,
    AssetUsage,
    AttributionEngine,
    ChangeType,
    ExpertScore,
    ExplicitAssetTracker,
    HarnessSession,
    HarnessSessionManager,
    Iteration,
    IterationEngine,
    SessionStatus,
    TrainingDataExtractor,
    UsageContext,
)


class TestHarnessSessionManager:
    def test_create_session(self, tmp_path):
        manager = HarnessSessionManager(storage_path=str(tmp_path))
        session = manager.create_session(
            problem_statement="分析 Q3 库存",
            pattern_type="区域库存分析",
            scenario="business_analysis",
        )
        assert session.problem_statement == "分析 Q3 库存"
        assert session.pattern_type == "区域库存分析"
        assert session.status == SessionStatus.ACTIVE
        assert session.satisfaction_threshold == 4.0
        assert session.current_score == 0.0

    def test_add_iteration_and_satisfaction(self, tmp_path):
        manager = HarnessSessionManager(storage_path=str(tmp_path))
        session = manager.create_session(
            problem_statement="分析库存",
            satisfaction_threshold=4.0,
        )
        
        # 不满意迭代
        iter1 = Iteration(
            number=1,
            agent_response="库存还可以",
            expert_score=ExpertScore(structure=3, accuracy=3, completeness=3, style=3, reasoning=3),
        )
        session = manager.add_iteration(session.id, iter1)
        assert session.status == SessionStatus.ACTIVE
        assert not session.iterations[0].is_satisfactory
        
        # 满意迭代
        iter2 = Iteration(
            number=2,
            agent_response="库存分析完成，建议补货 800 台",
            expert_score=ExpertScore(structure=4, accuracy=5, completeness=4, style=4, reasoning=5),
        )
        session = manager.add_iteration(session.id, iter2)
        assert session.status == SessionStatus.SATISFIED
        assert session.iterations[1].is_satisfactory
        assert session.best_iteration.number == 2

    def test_scenario_weights(self, tmp_path):
        manager = HarnessSessionManager(storage_path=str(tmp_path))
        _ = manager.create_session(
            problem_statement="客服问题",
            scenario="customer_service",
        )
        score = ExpertScore.for_scenario("customer_service")
        score.style = 5
        score.completeness = 5
        score.accuracy = 5
        assert score.overall >= 4.0  # style + completeness + accuracy 权重高

    def test_mark_abandoned(self, tmp_path):
        manager = HarnessSessionManager(storage_path=str(tmp_path))
        session = manager.create_session(problem_statement="测试")
        session = manager.mark_abandoned(session.id)
        assert session.status == SessionStatus.ABANDONED


class TestIterationEngine:
    def test_implicit_asset_tracking(self):
        engine = IterationEngine()
        engine.register_asset("库存周转率", "inv_turnover", AssetType.ONTOLOGY_CLASS)
        engine.register_asset("安全库存", "safety_stock", AssetType.KNOWLEDGE_CHUNK)
        
        iteration = engine.run_iteration(
            problem="分析库存",
            agent_response="根据库存周转率和安全库存分析，建议补货 800 台",
        )
        
        assert len(iteration.assets_used) == 2
        names = [u.asset_name for u in iteration.assets_used]
        assert "库存周转率" in names
        assert "安全库存" in names

    def test_no_asset_registry(self):
        engine = IterationEngine()  # 空注册表
        iteration = engine.run_iteration(
            problem="分析库存",
            agent_response="库存情况良好",
        )
        assert iteration.assets_used == []

    def test_explicit_asset_tracking(self):
        engine = IterationEngine(enable_explicit_tracking=True)
        engine.register_asset("库存周转率", "inv_turnover", AssetType.ONTOLOGY_CLASS)
        
        # 模拟 Agent 的 function calling
        tool_calls = [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "query_asset",
                    "arguments": '{"asset_type": "ontology_class", "asset_id": "inv_turnover", "query": "查询库存周转率"}',
                },
            }
        ]
        
        iteration = engine.run_iteration(
            problem="分析库存",
            agent_response="根据库存周转率分析，建议补货",
            tool_calls=tool_calls,
        )
        
        assert len(iteration.assets_used) == 1
        assert iteration.assets_used[0].asset_id == "inv_turnover"
        assert iteration.assets_used[0].relevance_score == 1.0  # 显式追踪相关度为 1.0
        assert iteration.assets_used[0].is_critical is True

    def test_hybrid_fallback_to_implicit(self):
        engine = IterationEngine(enable_explicit_tracking=True)
        engine.register_asset("安全库存", "safety_stock", AssetType.KNOWLEDGE_CHUNK)
        
        # 无 tool_calls，应 fallback 到隐式推断
        iteration = engine.run_iteration(
            problem="分析库存",
            agent_response="根据安全库存分析，建议补货",
        )
        
        assert len(iteration.assets_used) == 1
        assert iteration.assets_used[0].asset_name == "安全库存"


class TestExplicitAssetTracker:
    def test_track_from_tool_calls(self):
        tracker = ExplicitAssetTracker()
        
        tool_calls = [
            {
                "function": {
                    "name": "query_asset",
                    "arguments": '{"asset_type": "ontology_class", "asset_id": "inv_turnover"}',
                },
            },
            {
                "function": {
                    "name": "other_tool",  # 非 query_asset，应忽略
                    "arguments": '{}',
                },
            },
        ]
        
        usages = tracker.track_from_tool_calls(tool_calls)
        assert len(usages) == 1
        assert usages[0].asset_id == "inv_turnover"
        assert usages[0].asset_type == AssetType.ONTOLOGY_CLASS

    def test_build_query_tool_schema(self):
        tracker = ExplicitAssetTracker()
        schema = tracker.build_query_tool_schema()
        
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "query_asset"
        assert "asset_type" in schema["function"]["parameters"]["properties"]


class TestAttributionEngine:
    def test_simple_attribution(self):
        session = HarnessSession(problem_statement="分析库存")
        
        # 迭代 1: 初始状态，评分 3.0
        session.iterations.append(Iteration(
            number=1,
            agent_response="一般",
            expert_score=ExpertScore(structure=3, accuracy=3, completeness=3, style=3, reasoning=3),
            asset_changes=[],
        ))
        
        # 迭代 2: 加了库存周转率指标，评分 4.5
        session.iterations.append(Iteration(
            number=2,
            agent_response="根据库存周转率分析...",
            expert_score=ExpertScore(structure=4, accuracy=5, completeness=4, style=5, reasoning=5),
            asset_changes=[AssetChange(
                change_type=ChangeType.ADD,
                asset_type=AssetType.ONTOLOGY_CLASS,
                asset_id="inv_turnover",
                diff_summary="增加库存周转率指标定义",
            )],
        ))
        
        engine = AttributionEngine()
        report = engine.analyze(session)
        
        assert len(report.attribution_details) > 0
        assert report.attribution_details[0].improvement > 0
        assert report.overall_confidence > 0
        assert any("库存周转率" in insight for insight in report.key_insights)

    def test_insufficient_iterations(self):
        session = HarnessSession(problem_statement="分析库存")
        session.iterations.append(Iteration(number=1))
        
        engine = AttributionEngine()
        report = engine.analyze(session)
        
        assert report.overall_confidence == 0.0
        assert any("不足" in insight for insight in report.key_insights)


class TestTrainingDataExtractor:
    def test_extract_from_satisfactory_iteration(self):
        session = HarnessSession(
            problem_statement="分析 Q3 华东区库存",
            pattern_type="区域库存分析",
            domain="商业分析",
        )
        
        session.iterations.append(Iteration(
            number=1,
            agent_response="建议补货 800 台",
            is_satisfactory=True,
            assets_used=[
                AssetUsage(
                    asset_type=AssetType.ONTOLOGY_CLASS,
                    asset_id="inv_turnover",
                    asset_name="库存周转率",
                    asset_version="v1",
                    usage_context=UsageContext.ANSWER_REFERENCE,
                ),
            ],
            expert_score=ExpertScore(structure=4, accuracy=5, completeness=4, style=4, reasoning=5),
        ))
        
        extractor = TrainingDataExtractor()
        samples = extractor.extract_from_session(session)
        
        assert len(samples) == 1
        sample = samples[0]
        assert sample.source.source_type == "harness_iteration"
        assert len(sample.messages) == 3  # system + user + assistant
        assert len(sample.tools) == 1
        assert len(sample.tool_calls) == 1
        assert sample.tool_calls[0]["function"]["name"] == "query_asset"

    def test_extract_only_best(self):
        session = HarnessSession(problem_statement="分析库存")
        
        # 不满意
        session.iterations.append(Iteration(
            number=1,
            agent_response="一般",
            is_satisfactory=False,
            expert_score=ExpertScore(structure=3, accuracy=3, completeness=3, style=3, reasoning=3),
        ))
        
        # 满意
        session.iterations.append(Iteration(
            number=2,
            agent_response="很好",
            is_satisfactory=True,
            expert_score=ExpertScore(structure=4, accuracy=5, completeness=4, style=4, reasoning=5),
        ))
        
        extractor = TrainingDataExtractor()
        
        # 提取所有满意迭代
        all_samples = extractor.extract_from_session(session, include_all_satisfactory=True)
        assert len(all_samples) == 1
        
        # 只提取最佳
        best_samples = extractor.extract_from_session(session, include_all_satisfactory=False)
        assert len(best_samples) == 1

    def test_no_satisfactory_iterations(self):
        session = HarnessSession(problem_statement="分析库存")
        session.iterations.append(Iteration(
            number=1,
            is_satisfactory=False,
        ))
        
        extractor = TrainingDataExtractor()
        samples = extractor.extract_from_session(session)
        assert samples == []
