"""
迭代自举 - OWL 生成器
把候选类合并进现有 ontology, 生成下一轮的 .owl
- 保留已有类
- 新增候选类(若不在已有类中)
（实例作为 NamedIndividual 加入，关系暂留占位）
"""
from __future__ import annotations
import re
from pathlib import Path

NS = "http://example.org/cso/ontology#"


def read_class_names(owl_path: str | Path) -> set[str]:
    """从 owl 文件读取已有类名(RDF:Class)。"""
    text = Path(owl_path).read_text(encoding="utf-8")
    classes = set(re.findall(r'rdf:about="[^"]*#([^"]+)"[^>]*>\s*<rdf:type[^>]*owl#Class', text))
    return {c for c in classes if c}


def build_owl(existing_owl_path: str | Path, classes: set[str],
              relations: list[tuple[str, str, str]]) -> str:
    """
    生成完整的 owl 文本。
    classes: 所有类名集合
    relations: (subject, predicate, object) 三元组(对象关系, 用 camel case)
    """
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rdf:RDF',
        '   xmlns:csorb="' + NS + '"',
        '   xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"',
        '   xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"',
        '>',
        '',
        '  <!-- ===== CSO 类 ===== -->',
    ]
    for cls in sorted(classes):
        parts.append(f'  <rdf:Description rdf:about="{NS}{cls}">')
        parts.append('    <rdf:type rdf:resource="http://www.w3.org/2002/07/owl#Class"/>')
        parts.append(f'    <rdfs:label>{cls}</rdfs:label>')
        parts.append('  </rdf:Description>')
    
    parts.append('')
    parts.append('  <!-- ===== 关系(对象属性) ===== -->')
    seen_rel = set()
    for s, p, o in relations:
        if p not in seen_rel:
            seen_rel.add(p)
            parts.append(f'  <rdf:Description rdf:about="{NS}{p}">')
            parts.append('    <rdf:type rdf:resource="http://www.w3.org/2002/07/owl#ObjectProperty"/>')
            parts.append(f'    <rdfs:label>{p}</rdfs:label>')
            parts.append('  </rdf:Description>')
    
    parts.append('</rdf:RDF>')
    return "\n".join(parts)


if __name__ == "__main__":
    seed = str(Path(__file__).parent.parent / "data" / "cso_oncology.owl")
    existing = read_class_names(seed)
    print("种子已有类:", existing)
    # 新增候选类
    new_classes = existing | {"SurvivalEndpoint", "ResponseEndpoint"}
    relations = [("Endpoint", "isEvaluatedBy", "StudyObjective")]
    out = build_owl(seed, new_classes, relations)
    print("\n生成owl前300字符:\n", out[:300])
