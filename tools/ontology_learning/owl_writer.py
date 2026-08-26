"""OWL 生成/更新：把学习到的类合并进 ontology。

从 POC 的 owl_generator.py 提升而来，去除对特定路径的耦合，改为纯函数式。
"""
from __future__ import annotations

NS = "http://example.org/cso/ontology#"
OWL_NS = "http://www.w3.org/2002/07/owl#"
RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#"


def read_class_names(owl_path):
    """从 OWL 文件读取已有类名(RDF:Class)。"""
    import re
    from pathlib import Path

    text = Path(owl_path).read_text(encoding="utf-8")
    classes = set(
        re.findall(
            r'rdf:about="[^"]*#([^"]+)"[^>]*>\s*<rdf:type[^>]*owl#Class',
            text,
        )
    )
    return {c for c in classes if c}


def build_owl_text(class_names: set[str], relations: list[tuple[str, str, str]] | None = None) -> str:
    """生成含给定类 + 对象属性的 OWL（RDF/XML）文本。"""
    relations = relations or []
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<rdf:RDF",
        f'   xmlns:csorb="{NS}"',
        '   xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"',
        '   xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"',
        ">",
        "",
        "  <!-- ===== Classes ===== -->",
    ]
    for cls in sorted(class_names):
        parts.append(f'  <rdf:Description rdf:about="{NS}{cls}">')
        parts.append(f'    <rdf:type rdf:resource="{OWL_NS}Class"/>')
        parts.append(f"    <rdfs:label>{cls}</rdfs:label>")
        parts.append("  </rdf:Description>")

    parts.append("")
    parts.append("  <!-- ===== Object Properties ===== -->")
    seen = set()
    for _s, pred, _o in relations:
        if pred not in seen:
            seen.add(pred)
            parts.append(f'  <rdf:Description rdf:about="{NS}{pred}">')
            parts.append(f'    <rdf:type rdf:resource="{OWL_NS}ObjectProperty"/>')
            parts.append(f"    <rdfs:label>{pred}</rdfs:label>")
            parts.append("  </rdf:Description>")

    parts.append("</rdf:RDF>")
    return "\n".join(parts)


def write_owl(path, class_names: set[str], relations=None):
    """把类集合写成 OWL 文件。"""
    text = build_owl_text(class_names, relations)
    from pathlib import Path

    Path(path).write_text(text, encoding="utf-8")
    return path


__all__ = ["read_class_names", "build_owl_text", "write_owl"]
