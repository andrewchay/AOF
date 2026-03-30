#!/usr/bin/env python3
"""数据库 Schema 提取命令行工具。

从数据库自动提取 Schema 并生成 OWL 本体文件。

使用示例:
    # PostgreSQL
    python tools/extract_db_schema.py \
        --db-url "postgresql://user:pass@localhost/mydb" \
        --provider postgresql \
        --output ontologies/mydb_schema.owl

    # SQLite
    python tools/extract_db_schema.py \
        --db-url "sqlite:///path/to/database.db" \
        --provider sqlite \
        --output ontologies/mydb_schema.owl

    # 生成差异报告（不修改现有本体）
    python tools/extract_db_schema.py \
        --db-url "$DATABASE_URL" \
        --output ontologies/mydb_schema.owl \
        --mode diff

    # 完全替换现有本体
    python tools/extract_db_schema.py \
        --db-url "$DATABASE_URL" \
        --output ontologies/mydb_schema.owl \
        --mode replace
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from bridge.database_schema_extractor import (
    DatabaseSchemaExtractor,
    MergeMode,
    extract_db_schema_to_ontology,
)


def print_result(result) -> None:
    """打印提取结果。"""
    print("\n" + "=" * 60)
    print("数据库 Schema 提取完成")
    print("=" * 60)
    print(f"\n发现的表数量: {result.tables_discovered}")
    
    if result.new_classes:
        print(f"\n新类 ({len(result.new_classes)}):")
        for cls in sorted(result.new_classes)[:10]:  # 最多显示10个
            print(f"  • {cls}")
        if len(result.new_classes) > 10:
            print(f"  ... 还有 {len(result.new_classes) - 10} 个")
    
    if result.new_properties:
        print(f"\n新属性 ({len(result.new_properties)}):")
        for prop in sorted(result.new_properties)[:10]:
            print(f"  • {prop}")
        if len(result.new_properties) > 10:
            print(f"  ... 还有 {len(result.new_properties) - 10} 个")
    
    if result.conflicts:
        print(f"\n冲突/重复 ({len(result.conflicts)}):")
        for conflict in result.conflicts[:5]:
            print(f"  ⚠ {conflict['type']}: {conflict['name']} - {conflict['reason']}")
    
    if result.diff_report:
        print("\n差异报告预览:")
        print("-" * 40)
        lines = result.diff_report.split("\n")[:20]
        print("\n".join(lines))
        if len(result.diff_report.split("\n")) > 20:
            print("...")
    
    print("\n" + "=" * 60)


async def main() -> int:
    """主函数。"""
    parser = argparse.ArgumentParser(
        description="从数据库提取 Schema 并生成 OWL 本体",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
环境变量:
    DATABASE_URL    数据库连接字符串（可作为 --db-url 的替代）
    
示例:
    # PostgreSQL
    python tools/extract_db_schema.py \\
        --db-url "postgresql://user:pass@localhost/db" \\
        --output ontologies/schema.owl

    # SQLite
    python tools/extract_db_schema.py \\
        --db-url "sqlite:///data/mydb.db" \\
        --provider sqlite \\
        --output ontologies/mydb.owl
        """,
    )
    
    parser.add_argument(
        "--db-url",
        default=os.environ.get("DATABASE_URL", ""),
        help="数据库连接字符串（默认从 DATABASE_URL 环境变量读取）",
    )
    parser.add_argument(
        "--provider",
        choices=["postgresql", "mysql", "sqlite"],
        default="postgresql",
        help="数据库类型 (默认: postgresql)",
    )
    parser.add_argument(
        "--output",
        "-o",
        required=True,
        help="输出 OWL 文件路径",
    )
    parser.add_argument(
        "--mode",
        choices=["merge", "replace", "diff"],
        default="merge",
        help="合并模式: merge(合并)/replace(替换)/diff(仅差异报告) (默认: merge)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="试运行，不实际写入文件",
    )
    
    args = parser.parse_args()
    
    # 验证参数
    if not args.db_url:
        print("错误: 请提供 --db-url 或设置 DATABASE_URL 环境变量")
        return 1
    
    output_path = Path(args.output)
    
    # 检查 cognee 是否可用
    try:
        import cognee  # noqa: F401
    except ImportError:
        print("错误: Cognee 未安装。请安装 Cognee:")
        print("  pip install -e /path/to/cognee")
        return 1
    
    print("=" * 60)
    print("数据库 Schema 提取工具")
    print("=" * 60)
    print(f"\n数据库 URL: {args.db_url[:50]}..." if len(args.db_url) > 50 else f"\n数据库 URL: {args.db_url}")
    print(f"数据库类型: {args.provider}")
    print(f"输出文件: {output_path}")
    print(f"合并模式: {args.mode}")
    print(f"试运行: {args.dry_run}")
    print()
    
    try:
        if args.dry_run:
            print("[试运行模式] 将执行以下操作:")
            print(f"  1. 连接到 {args.provider} 数据库")
            print(f"  2. 提取数据库 Schema（表、列、关系）")
            print(f"  3. 转换为 OWL 本体格式")
            if args.mode == "diff":
                print(f"  4. 生成与现有本体的差异报告")
            elif args.mode == "merge":
                print(f"  4. 合并新内容到现有本体（如有）")
            else:
                print(f"  4. 替换现有本体")
            print(f"  5. 输出到: {output_path}")
            print("\n[试运行] 未实际执行")
            return 0
        
        # 执行提取
        result = await extract_db_schema_to_ontology(
            db_url=args.db_url,
            output_file=output_path,
            provider=args.provider,
            mode=args.mode,
        )
        
        print_result(result)
        print(f"\n✅ 输出文件: {output_path.resolve()}")
        return 0
        
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
