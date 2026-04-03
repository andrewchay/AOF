#!/usr/bin/env python3
"""
L3 数据完整性检查
- 必填字段检查
- 值域约束检查
- 枚举值合法性
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def check_spec_schema(spec_path: Path) -> list[dict[str, Any]]:
    """检查 spec 文件的数据完整性。"""
    errors = []
    
    try:
        with open(spec_path, 'r', encoding='utf-8') as f:
            spec = json.load(f)
    except Exception as e:
        return [{'field': 'file', 'error': str(e)}]
    
    # 检查必填字段
    required_fields = ['project_root', 'cognee']
    for field in required_fields:
        if field not in spec:
            errors.append({
                'field': field,
                'error': f'缺少必填字段: {field}'
            })
    
    # 检查 runtime 配置
    runtime = spec.get('runtime', {})
    if runtime:
        # data_per_batch 应该在合理范围
        data_per_batch = runtime.get('data_per_batch', 20)
        if not (1 <= data_per_batch <= 1000):
            errors.append({
                'field': 'runtime.data_per_batch',
                'error': f'值 {data_per_batch} 超出合理范围 [1, 1000]'
            })
        
        # retries 应该在合理范围
        retries = runtime.get('retries', 0)
        if not (0 <= retries <= 10):
            errors.append({
                'field': 'runtime.retries',
                'error': f'值 {retries} 超出合理范围 [0, 10]'
            })
    
    # 检查 ontology 配置
    ontology = spec.get('ontology', {})
    if ontology:
        # matching_cutoff 应该在 [0, 1] 范围
        cutoff = ontology.get('matching_cutoff', 0.8)
        if not (0 <= cutoff <= 1):
            errors.append({
                'field': 'ontology.matching_cutoff',
                'error': f'值 {cutoff} 超出合理范围 [0, 1]'
            })
        
        # 检查 ontology 文件是否存在
        ontology_file = ontology.get('file', '')
        if ontology_file and not Path(ontology_file).exists():
            errors.append({
                'field': 'ontology.file',
                'error': f'指定的本体文件不存在: {ontology_file}'
            })
    
    # 检查 cognee.root 是否存在
    cognee = spec.get('cognee', {})
    cognee_root = cognee.get('root', '')
    if cognee_root and not Path(cognee_root).exists():
        errors.append({
            'field': 'cognee.root',
            'error': f'指定的 cognee 根目录不存在: {cognee_root}'
        })
    
    return errors


def check_ontology_data(owl_path: Path) -> list[dict[str, Any]]:
    """检查本体文件的数据完整性。"""
    errors = []
    
    try:
        import xml.etree.ElementTree as ET
        tree = ET.parse(owl_path)
        root = tree.getroot()
        
        # 命名空间
        ns = {
            'owl': 'http://www.w3.org/2002/07/owl#',
            'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'
        }
        
        # 统计各类元素（支持两种格式）
        # 格式1: <owl:Class>
        classes = root.findall('.//owl:Class', ns)
        # 格式2: <rdf:Description> with <rdf:type rdf:resource="http://www.w3.org/2002/07/owl#Class"/>
        descriptions = root.findall('.//rdf:Description', ns)
        for desc in descriptions:
            type_elem = desc.find('rdf:type', ns)
            if type_elem is not None:
                type_resource = type_elem.get('{http://www.w3.org/1999/02/22-rdf-syntax-ns#}resource', '')
                if 'owl#Class' in type_resource:
                    classes.append(desc)
        
        # 检查是否有核心类（至少8个核心类）
        if len(classes) < 8:
            errors.append({
                'field': 'classes',
                'error': f'本体中 Class 数量不足 ({len(classes)} < 8)，可能需要重新生成本体'
            })
        
    except Exception as e:
        errors.append({
            'field': 'parse',
            'error': str(e)
        })
    
    return errors


def main() -> int:
    """主函数。"""
    project_root = Path(__file__).resolve().parents[2]
    
    print("=" * 60)
    print("L3 数据完整性检查")
    print("=" * 60)
    
    errors = []
    warnings = []
    
    # 1. 检查 spec 文件（跳过模板文件）
    print("\n[1/2] 检查 spec 文件数据完整性...")
    spec_files = [f for f in project_root.glob("aof_spec*.json") 
                  if not f.name.endswith('.template.json')]
    
    for spec_file in spec_files:
        print(f"\n  检查 {spec_file.name}:")
        spec_errors = check_spec_schema(spec_file)
        if spec_errors:
            for err in spec_errors:
                errors.append(f"{spec_file.name}.{err['field']}: {err['error']}")
                print(f"    ✗ {err['field']}: {err['error']}")
        else:
            print("    ✓ 通过")
    
    # 2. 检查本体文件
    print("\n[2/2] 检查本体文件数据完整性...")
    ontology_dir = project_root / "ontologies"
    if ontology_dir.exists():
        owl_files = list(ontology_dir.glob("*.owl"))
        checked = 0
        for owl_file in owl_files:
            owl_errors = check_ontology_data(owl_file)
            if owl_errors:
                for err in owl_errors:
                    errors.append(f"ontologies/{owl_file.name}.{err['field']}: {err['error']}")
            else:
                checked += 1
        print(f"  ✓ 检查 {checked}/{len(owl_files)} 个本体文件")
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
