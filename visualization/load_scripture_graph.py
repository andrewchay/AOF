#!/usr/bin/env python3
"""
加载 Open Scripture Intelligence 图谱数据
将圣经知识图谱转换为可视化格式

数据来源: https://github.com/echology-io/open-scripture-intelligence
- 31,100 个节点 (经文)
- 344,799 条边 (交叉引用)
"""

import json
import sys
from pathlib import Path
from collections import Counter

# 数据路径
SCRIPTURE_DIR = Path("/tmp/open-scripture-intelligence/graph")
NODES_FILE = SCRIPTURE_DIR / "nodes.jsonl"
EDGES_FILE = SCRIPTURE_DIR / "edges.jsonl"

# 输出路径
OUTPUT_DIR = Path(__file__).parent
OUTPUT_NODES = OUTPUT_DIR / "scripture_nodes.json"
OUTPUT_EDGES = OUTPUT_DIR / "scripture_edges.json"
OUTPUT_META = OUTPUT_DIR / "scripture_meta.json"


def load_nodes(limit: int = None):
    """加载节点数据"""
    print("📖 加载节点数据...")
    nodes = []
    book_counts = Counter()
    testament_counts = Counter()
    
    with open(NODES_FILE, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f):
            if limit and i >= limit:
                break
            
            data = json.loads(line.strip())
            
            # 转换为我们需要的格式
            node = {
                "id": data["id"],
                "name": data["reference"],
                "category": data["book"],  # 使用 book 作为分类
                "type": data["type"],
                "properties": {
                    "reference": data["reference"],
                    "book": data["book"],
                    "chapter": data["chapter"],
                    "verse": data["verse"],
                    "testament": data["testament"]
                }
            }
            nodes.append(node)
            book_counts[data["book"]] += 1
            testament_counts[data["testament"]] += 1
    
    print(f"   ✅ 加载 {len(nodes)} 个节点")
    print(f"   📊 旧约: {testament_counts.get('OT', 0)}, 新约: {testament_counts.get('NT', 0)}")
    print(f"   📚 共 {len(book_counts)} 卷书")
    
    return nodes, dict(book_counts)


def load_edges(node_ids: set, limit: int = None):
    """加载边数据（只加载节点存在的边）"""
    print("\n🔗 加载边数据...")
    edges = []
    
    with open(EDGES_FILE, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f):
            if limit and i >= limit:
                break
            
            data = json.loads(line.strip())
            
            # 只加载两端节点都在集合中的边
            from_id = data["from"]
            to_id = data["to"]
            
            if from_id in node_ids and to_id in node_ids:
                edge = {
                    "source": from_id,
                    "target": to_id,
                    "type": data["type"],
                    "properties": {
                        "confidence": data.get("confidence", 0),
                        "votes": data.get("votes", 0),
                        "source_ref": data.get("source", "")
                    }
                }
                edges.append(edge)
    
    print(f"   ✅ 加载 {len(edges)} 条边")
    return edges


def assign_colors(categories: list) -> dict:
    """为分类分配颜色"""
    # 使用颜色映射 - 圣经书卷按类型分组着色
    color_palette = [
        "#22c55e",  # 绿 - 摩西五经
        "#3b82f6",  # 蓝 - 历史书
        "#f59e0b",  # 橙 - 智慧书
        "#8b5cf6",  # 紫 - 先知书
        "#ef4444",  # 红 - 福音书
        "#06b6d4",  # 青 - 使徒行传
        "#f97316",  # 橙红 - 书信
        "#6366f1",  # 靛 - 启示录
        "#84cc16",  # 柠檬绿
        "#ec4899",  # 粉
        "#14b8a6",  # 青绿
        "#eab308",  # 黄
    ]
    
    # 圣经书卷分类
    book_categories = {
        # 摩西五经 (绿)
        "genesis": 0, "exodus": 0, "leviticus": 0, "numbers": 0, "deuteronomy": 0,
        # 历史书 (蓝)
        "joshua": 1, "judges": 1, "ruth": 1, "1samuel": 1, "2samuel": 1,
        "1kings": 1, "2kings": 1, "1chronicles": 1, "2chronicles": 1, "ezra": 1,
        "nehemiah": 1, "esther": 1,
        # 智慧书 (橙)
        "job": 2, "psalms": 2, "proverbs": 2, "ecclesiastes": 2, "songofsolomon": 2,
        # 先知书 (紫)
        "isaiah": 3, "jeremiah": 3, "lamentations": 3, "ezekiel": 3, "daniel": 3,
        "hosea": 3, "joel": 3, "amos": 3, "obadiah": 3, "jonah": 3,
        "micah": 3, "nahum": 3, "habakkuk": 3, "zephaniah": 3, "haggai": 3,
        "zechariah": 3, "malachi": 3,
        # 福音书 (红)
        "matthew": 4, "mark": 4, "luke": 4, "john": 4,
        # 使徒行传 (青)
        "acts": 5,
        # 保罗书信 (橙红)
        "romans": 6, "1corinthians": 6, "2corinthians": 6, "galatians": 6,
        "ephesians": 6, "philippians": 6, "colossians": 6, "1thessalonians": 6,
        "2thessalonians": 6, "1timothy": 6, "2timothy": 6, "titus": 6, "philemon": 6,
        # 其他书信 (橙红)
        "hebrews": 6, "james": 6, "1peter": 6, "2peter": 6, "1john": 6,
        "2john": 6, "3john": 6, "jude": 6,
        # 启示录 (靛)
        "revelation": 7,
    }
    
    color_map = {}
    for book in categories:
        color_idx = book_categories.get(book, 8)  # 默认第8个颜色
        color_map[book] = color_palette[color_idx % len(color_palette)]
    
    return color_map


def get_book_icon(book: str) -> str:
    """获取书卷图标"""
    icons = {
        # 摩西五经
        "genesis": "🌍", "exodus": "⛺", "leviticus": "🕯️", "numbers": "🔢", "deuteronomy": "📜",
        # 历史书
        "joshua": "⚔️", "judges": "👨‍⚖️", "ruth": "🌾", "1samuel": "👑", "2samuel": "👑",
        "1kings": "👑", "2kings": "👑", "1chronicles": "📋", "2chronicles": "📋",
        "ezra": "🏗️", "nehemiah": "🏗️", "esther": "👸",
        # 智慧书
        "job": "🤔", "psalms": "🎵", "proverbs": "🦉", "ecclesiastes": "⏳", "songofsolomon": "💕",
        # 先知书
        "isaiah": "🔮", "jeremiah": "😢", "lamentations": "😭", "ezekiel": "👁️", "daniel": "🦁",
        "hosea": "💔", "joel": "🦗", "amos": "🐑", "obadiah": "🏔️", "jonah": "🐋",
        "micah": "⚖️", "nahum": "⚡", "habakkuk": "🌅", "zephaniah": "📯", "haggai": "🏛️",
        "zechariah": "🐎", "malachi": "🔥",
        # 新约
        "matthew": "👑", "mark": "🦁", "luke": "🩺", "john": "🦅", "acts": "🕊️",
        "romans": "⚖️", "1corinthians": "🎁", "2corinthians": "😢", "galatians": "🗽",
        "ephesians": "🛡️", "philippians": "😊", "colossians": "👑", "1thessalonians": "🎺",
        "2thessalonians": "🎺", "1timothy": "📖", "2timothy": "📖", "titus": "📖", "philemon": "⛓️",
        "hebrews": "✝️", "james": "🤲", "1peter": "🪨", "2peter": "🪨", "1john": "❤️",
        "2john": "❤️", "3john": "❤️", "jude": "⚔️", "revelation": "👁️",
    }
    return icons.get(book, "📖")


def process_data(max_nodes: int = 5000):
    """处理数据并保存"""
    print("=" * 60)
    print("📚 Open Scripture Intelligence 图谱加载器")
    print("=" * 60)
    
    # 加载节点
    nodes, book_counts = load_nodes(limit=max_nodes)
    node_ids = {n["id"] for n in nodes}
    
    # 加载边
    edges = load_edges(node_ids)
    
    # 为节点添加颜色和图标
    color_map = assign_colors(list(book_counts.keys()))
    
    for node in nodes:
        book = node["category"]
        node["color"] = color_map.get(book, "#6366f1")
        node["icon"] = get_book_icon(book)
    
    # 生成元数据
    meta = {
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "books": [
            {"name": book, "count": count, "color": color_map.get(book, "#6366f1"), "icon": get_book_icon(book)}
            for book, count in sorted(book_counts.items(), key=lambda x: -x[1])
        ],
        "testament_distribution": {
            "OT": sum(1 for n in nodes if n["properties"]["testament"] == "OT"),
            "NT": sum(1 for n in nodes if n["properties"]["testament"] == "NT")
        }
    }
    
    # 保存文件
    print("\n💾 保存数据文件...")
    
    with open(OUTPUT_NODES, 'w', encoding='utf-8') as f:
        json.dump(nodes, f, ensure_ascii=False, indent=2)
    print(f"   ✅ 节点: {OUTPUT_NODES}")
    
    with open(OUTPUT_EDGES, 'w', encoding='utf-8') as f:
        json.dump(edges, f, ensure_ascii=False, indent=2)
    print(f"   ✅ 边: {OUTPUT_EDGES}")
    
    with open(OUTPUT_META, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"   ✅ 元数据: {OUTPUT_META}")
    
    # 打印统计
    print("\n📊 最终统计:")
    print(f"   节点: {len(nodes):,}")
    print(f"   边: {len(edges):,}")
    print(f"   书卷: {len(book_counts)}")
    print(f"   旧约: {meta['testament_distribution']['OT']:,}")
    print(f"   新约: {meta['testament_distribution']['NT']:,}")
    
    print("\n" + "=" * 60)
    print("✅ 数据转换完成!")
    print("=" * 60)
    
    return nodes, edges, meta


if __name__ == "__main__":
    # 默认加载前5000个节点（性能考虑）
    max_nodes = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    process_data(max_nodes)
