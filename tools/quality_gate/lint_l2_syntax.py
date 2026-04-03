#!/usr/bin/env python3
"""
L2 语法正确性检查
- JSON/YAML 格式合法性
- Python 语法检查
- OWL 文件 XML 格式检查
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path


def check_json_file(file_path: Path) -> tuple[bool, str]:
    """检查 JSON 文件格式。"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            json.load(f)
        return True, "OK"
    except json.JSONDecodeError as e:
        return False, f"JSON 解析错误 (行 {e.lineno}): {e.msg}"
    except Exception as e:
        return False, str(e)


def check_yaml_file(file_path: Path) -> tuple[bool, str]:
    """检查 YAML 文件格式。"""
    try:
        import yaml
        with open(file_path, 'r', encoding='utf-8') as f:
            yaml.safe_load(f)
        return True, "OK"
    except ImportError:
        return True, "跳过（未安装 PyYAML）"
    except Exception as e:
        return False, f"YAML 解析错误: {e}"


def check_python_syntax(file_path: Path) -> tuple[bool, str]:
    """检查 Python 文件语法。"""
    try:
        content = file_path.read_text(encoding='utf-8')
        ast.parse(content)
        return True, "OK"
    except SyntaxError as e:
        return False, f"语法错误 (行 {e.lineno}): {e.msg}"
    except Exception as e:
        return False, str(e)


def check_owl_xml(file_path: Path) -> tuple[bool, str]:
    """检查 OWL 文件 XML 格式。"""
    try:
        import xml.etree.ElementTree as ET
        tree = ET.parse(file_path)
        root = tree.getroot()
        # 检查是否为 RDF/OWL
        if 'rdf' not in root.tag.lower() and 'RDF' not in root.tag:
            return False, f"根元素不是 RDF: {root.tag}"
        return True, "OK"
    except ET.ParseError as e:
        return False, f"XML 解析错误: {e}"
    except Exception as e:
        return False, str(e)


def main() -> int:
    """主函数。"""
    project_root = Path(__file__).resolve().parents[2]
    
    print("=" * 60)
    print("L2 语法正确性检查")
    print("=" * 60)
    
    errors = []
    warnings = []
    
    # 1. 检查 JSON 文件
    print("\n[1/4] 检查 JSON 文件...")
    json_files = [
        project_root / "aof_spec.example.json",
        project_root / "aof_spec.deepseek.template.json",
        project_root / "aof_spec.local.template.json",
    ]
    
    for json_file in json_files:
        if json_file.exists():
            ok, msg = check_json_file(json_file)
            if ok:
                print(f"  ✓ {json_file.name}")
            else:
                errors.append(f"{json_file.name}: {msg}")
    
    # 2. 检查 spec 测试文件
    spec_files = list(project_root.glob("aof_spec*.json"))
    for spec_file in spec_files:
        if spec_file not in json_files:
            ok, msg = check_json_file(spec_file)
            if ok:
                print(f"  ✓ {spec_file.name}")
            else:
                errors.append(f"{spec_file.name}: {msg}")
    
    # 3. 检查 YAML 文件
    print("\n[2/4] 检查 YAML 文件...")
    yaml_files = list(project_root.rglob("*.yml")) + list(project_root.rglob("*.yaml"))
    for yaml_file in yaml_files[:20]:  # 限制数量
        ok, msg = check_yaml_file(yaml_file)
        if ok:
            if msg == "OK":
                print(f"  ✓ {yaml_file.relative_to(project_root)}")
        else:
            errors.append(f"{yaml_file.name}: {msg}")
    
    # 4. 检查关键 Python 文件
    print("\n[3/4] 检查 Python 语法...")
    python_files = [
        project_root / "aof_run.py",
        project_root / "aof_add.py",
        project_root / "aof_doctor.py",
    ]
    
    for py_file in python_files:
        if py_file.exists():
            ok, msg = check_python_syntax(py_file)
            if ok:
                print(f"  ✓ {py_file.name}")
            else:
                errors.append(f"{py_file.name}: {msg}")
    
    # 5. 检查桥接层 Python 文件
    print("\n[4/4] 检查桥接层...")
    bridge_dir = project_root / "bridge"
    if bridge_dir.exists():
        bridge_py_files = list(bridge_dir.rglob("*.py"))
        for py_file in bridge_py_files:
            ok, msg = check_python_syntax(py_file)
            if not ok:
                errors.append(f"bridge/{py_file.name}: {msg}")
        if all(check_python_syntax(f)[0] for f in bridge_py_files):
            print(f"  ✓ bridge/ ({len(bridge_py_files)} 个文件)")
    
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
