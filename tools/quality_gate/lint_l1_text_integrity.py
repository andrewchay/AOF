#!/usr/bin/env python3
# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""
L1 文本完整性检查
- 文件编码正确性（UTF-8）
- 空文件检测
- 文件存在性检查
- BOM 头检测
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


def check_file_encoding(file_path: Path) -> tuple[bool, str]:
    """检查文件是否为有效的 UTF-8 编码。"""
    try:
        content = file_path.read_text(encoding='utf-8')
        # 检查 BOM
        if content.startswith('\ufeff'):
            return False, "文件包含 BOM 头，建议去除"
        return True, "OK"
    except UnicodeDecodeError as e:
        return False, f"编码错误: {e}"
    except Exception as e:
        return False, f"读取失败: {e}"


def check_empty_file(file_path: Path) -> tuple[bool, str]:
    """检查文件是否为空。"""
    try:
        content = file_path.read_text(encoding='utf-8')
        if not content.strip():
            return False, "文件为空或仅包含空白字符"
        return True, "OK"
    except Exception as e:
        return False, str(e)


def check_critical_files(project_root: Path) -> list[dict[str, Any]]:
    """检查关键文件是否存在。"""
    critical_files = [
        "README.md",
        "aof_run.py",
        "aof_add.py",
        "aof_doctor.py",
        "bridge/cognee_runner.py",
        "services/semantic_middle_layer_api/app.py",
    ]
    
    results = []
    for file_path in critical_files:
        full_path = project_root / file_path
        results.append({
            'file': file_path,
            'exists': full_path.exists(),
            'required': True
        })
    
    return results


def main() -> int:
    """主函数。"""
    project_root = Path(__file__).resolve().parents[2]
    
    print("=" * 60)
    print("L1 文本完整性检查")
    print("=" * 60)
    
    errors = []
    warnings = []
    
    # 1. 检查关键文件
    print("\n[1/3] 检查关键文件存在性...")
    critical_results = check_critical_files(project_root)
    for result in critical_results:
        if result['required'] and not result['exists']:
            errors.append(f"缺少关键文件: {result['file']}")
        else:
            print(f"  ✓ {result['file']}")
    
    # 2. 检查 spec 文件
    print("\n[2/3] 检查 spec 文件...")
    spec_files = list(project_root.glob("aof_spec*.json"))
    if not spec_files:
        warnings.append("未找到 spec 配置文件")
    else:
        for spec_file in spec_files:
            ok, msg = check_file_encoding(spec_file)
            if ok:
                ok2, msg2 = check_empty_file(spec_file)
                if ok2:
                    print(f"  ✓ {spec_file.name}")
                else:
                    errors.append(f"{spec_file.name}: {msg2}")
            else:
                errors.append(f"{spec_file.name}: {msg}")
    
    # 3. 检查本体文件
    print("\n[3/3] 检查本体文件...")
    ontology_dir = project_root / "ontologies"
    if ontology_dir.exists():
        owl_files = list(ontology_dir.glob("*.owl"))
        for owl_file in owl_files[:10]:  # 只检查前10个
            ok, msg = check_file_encoding(owl_file)
            if ok:
                print(f"  ✓ {owl_file.name}")
            else:
                errors.append(f"ontologies/{owl_file.name}: {msg}")
        if len(owl_files) > 10:
            print(f"  ... 还有 {len(owl_files) - 10} 个本体文件")
    else:
        print("  ontologies/ 目录不存在（正常）")
    
    # 报告
    print("\n" + "=" * 60)
    print("检查结果")
    print("=" * 60)
    
    if errors:
        print(f"\n❌ 错误 ({len(errors)}):")
        for error in errors:
            print(f"  • {error}")
    
    if warnings:
        print(f"\n⚠️  警告 ({len(warnings)}):")
        for warning in warnings:
            print(f"  • {warning}")
    
    if not errors and not warnings:
        print("\n✅ 所有检查通过")
    
    print(f"\n总计: {len(errors)} 错误, {len(warnings)} 警告")
    
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
