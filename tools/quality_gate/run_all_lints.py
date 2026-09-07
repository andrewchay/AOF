#!/usr/bin/env python3
# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""
AOF 统一质量门入口
运行所有五层质量控制检查

Usage:
    python run_all_lints.py              # 运行所有检查
    python run_all_lints.py --l1         # 只运行 L1
    python run_all_lints.py --l1 --l2    # 运行 L1 和 L2
    python run_all_lints.py --quick      # 快速模式（L1-L3）
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


# 检查器配置
LINTERS = {
    'l1': {
        'name': 'L1 文本完整性',
        'script': 'lint_l1_text_integrity.py',
        'description': '文件编码、空文件、关键文件存在性'
    },
    'l2': {
        'name': 'L2 语法正确性',
        'script': 'lint_l2_syntax.py',
        'description': 'JSON/YAML 格式、Python 语法'
    },
    'l3': {
        'name': 'L3 数据完整性',
        'script': 'lint_l3_data_integrity.py',
        'description': '必填字段、值域约束'
    },
}


def run_linter(level: str) -> tuple[bool, str]:
    """运行指定层级的 linter。"""
    config = LINTERS[level]
    script_path = Path(__file__).parent / config['script']
    
    if not script_path.exists():
        return False, f"脚本不存在: {script_path}"
    
    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            timeout=60
        )
        success = result.returncode == 0
        return success, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return False, "执行超时"
    except Exception as e:
        return False, str(e)


def main() -> int:
    """主函数。"""
    parser = argparse.ArgumentParser(
        description='AOF 质量门 - 五层质量控制检查',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
质量控制层次说明:
  L1: 文本完整性 - 编码、空文件、文件存在性
  L2: 语法正确性 - JSON/YAML/Python 语法
  L3: 数据完整性 - 必填字段、值域约束
  L4: 业务逻辑验证 - （预留）
  L5: 跨工序一致性 - （预留）

示例:
  %(prog)s              # 运行所有检查
  %(prog)s --quick      # 快速模式（L1-L3）
  %(prog)s --l1 --l2    # 只运行 L1 和 L2
        '''
    )
    
    # 层级选择参数
    for level in LINTERS:
        parser.add_argument(
            f'--{level}',
            action='store_true',
            help=f'运行 {LINTERS[level]["name"]}'
        )
    
    parser.add_argument(
        '--quick',
        action='store_true',
        help='快速模式（只运行 L1-L3）'
    )
    
    parser.add_argument(
        '--fail-fast',
        action='store_true',
        help='遇到第一个错误时立即停止'
    )
    
    args = parser.parse_args()
    
    # 确定要运行的层级
    selected_levels = []
    if args.quick:
        selected_levels = ['l1', 'l2', 'l3']
    else:
        for level in LINTERS:
            if getattr(args, level):
                selected_levels.append(level)
    
    # 如果没有指定，运行所有
    if not selected_levels:
        selected_levels = list(LINTERS.keys())
    
    # 运行检查
    print("=" * 70)
    print("AOF 统一质量门")
    print("=" * 70)
    print(f"\n将运行 {len(selected_levels)} 个检查:\n")
    for level in selected_levels:
        config = LINTERS[level]
        print(f"  [{level.upper()}] {config['name']}")
        print(f"       {config['description']}")
    print()
    
    results = {}
    overall_success = True
    
    for level in selected_levels:
        config = LINTERS[level]
        print("=" * 70)
        print(f"[{level.upper()}] {config['name']}")
        print("=" * 70)
        
        success, output = run_linter(level)
        results[level] = success
        
        # 打印输出
        if output:
            print(output)
        
        if not success:
            overall_success = False
            if args.fail_fast:
                print("\n❌ 检查失败，根据 --fail-fast 停止后续检查")
                break
        
        print()
    
    # 最终报告
    print("=" * 70)
    print("质量门最终报告")
    print("=" * 70)
    
    for level in selected_levels:
        config = LINTERS[level]
        status = "✅ 通过" if results.get(level, False) else "❌ 失败"
        print(f"  [{level.upper()}] {config['name']}: {status}")
    
    print()
    if overall_success:
        print("✅ 所有检查通过！质量门已开启。")
        return 0
    else:
        print("❌ 部分检查未通过。请修复上述问题后重试。")
        return 1


if __name__ == '__main__':
    sys.exit(main())
