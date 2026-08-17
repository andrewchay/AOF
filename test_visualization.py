#!/usr/bin/env python3
"""
测试知识图谱可视化
生成示例数据并验证 API
"""

import asyncio
import sys
from pathlib import Path

# Add AOF to path
AOF_ROOT = Path(__file__).parent
sys.path.insert(0, str(AOF_ROOT))

from bridge.storage import StorageConfig, StorageFactory  # noqa: E402
from bridge.storage.base import Edge, Node  # noqa: E402


async def create_test_graph():
    """创建测试知识图谱"""
    print("🚀 创建测试知识图谱...")
    
    config = StorageConfig(backend_type='cognee')
    backend = StorageFactory.create(config)
    
    # 连接后端
    await backend.connect()
    print(f"✅ 已连接到后端: {backend.__class__.__name__}")
    
    # 清空现有数据
    try:
        await backend.clear()
        print("🧹 已清空现有数据")
    except Exception as e:
        print(f"⚠️ 清空数据警告: {e}")
    
    # 创建测试节点
    categories = [
        ("游戏内容_任务", "📜"),
        ("游戏内容_版本", "📅"),
        ("概念", "💡"),
        ("分析技能", "🧠"),
        ("增长分析_指标", "📊"),
    ]
    
    nodes = []
    node_id = 0
    
    for category, icon in categories:
        # 每个分类创建5-10个节点
        count = 5 + hash(category) % 6
        for i in range(count):
            node = Node(
                id=f"node_{node_id}",
                labels=[category],
                properties={
                    "name": f"{category}_{i+1}",
                    "icon": icon,
                    "description": f"这是一个{category}类型的测试节点",
                    "importance": round(0.3 + (i % 5) * 0.15, 2),
                    "created": "2024-04-08"
                }
            )
            nodes.append(node)
            node_id += 1
    
    print(f"📦 创建 {len(nodes)} 个节点")
    
    # 添加节点
    for node in nodes:
        await backend.add_node(node)
    
    # 创建边（随机连接）
    import random
    random.seed(42)
    
    edges = []
    for i in range(len(nodes)):
        # 每个节点连接 1-3 个其他节点
        num_connections = 1 + random.randint(0, 2)
        for _ in range(num_connections):
            target_idx = random.randint(0, len(nodes) - 1)
            if target_idx != i:
                edge = Edge(
                    source_id=nodes[i].id,
                    target_id=nodes[target_idx].id,
                    relation_type=random.choice(["包含", "引用", "相关", "依赖"]),
                    properties={"weight": round(random.random(), 2)}
                )
                edges.append(edge)
    
    print(f"🔗 创建 {len(edges)} 条边")
    
    # 添加边
    for edge in edges:
        await backend.add_edge(edge)
    
    # 验证数据
    stats = await backend.get_statistics()
    print("\n📊 图谱统计:")
    print(f"   节点数: {stats.node_count}")
    print(f"   边数: {stats.edge_count}")
    
    await backend.disconnect()
    print("\n✅ 测试数据创建完成!")
    
    return stats.node_count, stats.edge_count


async def test_api():
    """测试 API 端点"""
    import aiohttp
    
    print("\n🧪 测试 Graph API 端点...\n")
    
    base_url = "http://127.0.0.1:8787"
    
    async with aiohttp.ClientSession() as session:
        # 测试健康检查
        async with session.get(f"{base_url}/v1/graph/health") as resp:
            data = await resp.json()
            print(f"✅ /v1/graph/health: {data['status']}")
        
        # 测试统计
        async with session.get(f"{base_url}/v1/graph/statistics?dataset=default") as resp:
            data = await resp.json()
            print(f"✅ /v1/graph/statistics: {data['node_count']} 节点, {data['edge_count']} 边")
        
        # 测试分类
        async with session.get(f"{base_url}/v1/graph/categories?dataset=default") as resp:
            data = await resp.json()
            print(f"✅ /v1/graph/categories: {data['count']} 个分类")
            for cat in data.get('categories', [])[:5]:
                print(f"   - {cat['name']}: {cat['count']} 个节点")
        
        # 测试节点列表
        async with session.get(f"{base_url}/v1/graph/nodes?dataset=default&limit=5") as resp:
            data = await resp.json()
            print(f"✅ /v1/graph/nodes: 返回 {len(data['nodes'])} 个节点")
        
        # 测试搜索
        async with session.post(
            f"{base_url}/v1/graph/search",
            json={"query": "游戏", "dataset": "default", "limit": 5}
        ) as resp:
            data = await resp.json()
            print(f"✅ /v1/graph/search: 找到 {data['count']} 个结果")


if __name__ == "__main__":
    # 创建测试数据
    node_count, edge_count = asyncio.run(create_test_graph())
    
    if node_count > 0:
        # 测试 API
        asyncio.run(test_api())
        
        print("\n" + "="*50)
        print("🎉 测试完成! 可以打开可视化页面查看:")
        print("   open visualization/knowledge_graph_visualizer.html")
        print("="*50)
    else:
        print("\n❌ 数据创建失败")
