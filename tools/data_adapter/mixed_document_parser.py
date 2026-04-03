#!/usr/bin/env python3
"""混合文档解析器 - 提取代码、文本、标注的联合语义。

支持格式：
- Jupyter Notebook (.ipynb) - 代码 + Markdown 注释
- Markdown (.md) - 代码块 + 文本说明
- XML 标注文档 (.xml, .annot) - <snippet code="..." annotation="..."/>
- JSON 标注格式 (.json) - 结构化标注数据
- YAML 标注格式 (.yaml, .yml) - 代码/问答对标注
- 纯文本代码文件 (.py, .sql, .js 等) - 配合注释提取

输出统一的 JSONL 格式，支持 AOF 本体抽取流程。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class MixedDocumentParser:
    """解析包含代码+文本标注的混合文档。"""

    # 文件扩展名到格式的映射
    EXT_FORMAT_MAP = {
        ".ipynb": "jupyter",
        ".md": "markdown",
        ".markdown": "markdown",
        ".annot": "xml",
        ".xml": "xml",
        ".yaml": "yaml",
        ".yml": "yaml",
    }

    def __init__(self, doc_path: Path):
        self.path = doc_path
        self.format = self._detect_format()
        self._content_cache: str | None = None

    def _detect_format(self) -> str:
        """检测文档格式。"""
        ext = self.path.suffix.lower()

        # 优先使用扩展名映射
        if ext in self.EXT_FORMAT_MAP:
            return self.EXT_FORMAT_MAP[ext]

        # 通过内容特征检测
        content = self._get_content()[:2000]

        # Jupyter Notebook JSON 特征
        if ext == ".json" or ('"cells"' in content and '"metadata"' in content):
            try:
                data = json.loads(content)
                if "cells" in data and "metadata" in data:
                    return "jupyter"
            except json.JSONDecodeError:
                pass

        # XML 特征
        if ext in (".xml", ".annot") or content.strip().startswith("<"):
            return "xml"

        # Markdown 代码块特征
        if "```" in content or content.startswith("#"):
            return "markdown"

        # YAML 特征检测（缩进结构、键值对）
        if ext in (".yaml", ".yml"):
            return "yaml"
        if self._looks_like_yaml(content):
            return "yaml"

        # 代码文件（通过注释提取）
        if ext in (".py", ".sql", ".js", ".ts", ".java", ".go", ".rs"):
            return "code_with_comments"

        return "plain"

    def _looks_like_yaml(self, content: str) -> bool:
        """启发式检测是否为 YAML 格式。"""
        lines = content.strip().split("\n")[:50]
        
        # YAML 特征：缩进结构、键值对、列表项
        yaml_indicators = 0
        for line in lines:
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#"):
                continue
            # 键值对 (key: value)
            if re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*\s*:", stripped):
                yaml_indicators += 1
            # 列表项 (- item)
            elif stripped.startswith("- ") and ":" in stripped[:50]:
                yaml_indicators += 1
            # 文档分隔符
            elif stripped == "---":
                yaml_indicators += 2
        
        return yaml_indicators >= 3

    def _get_content(self) -> str:
        """获取文件内容（带缓存）。"""
        if self._content_cache is None:
            self._content_cache = self.path.read_text(encoding="utf-8", errors="ignore")
        return self._content_cache

    def parse(self) -> list[dict[str, Any]]:
        """解析为统一记录格式。"""
        parsers = {
            "jupyter": self._parse_jupyter,
            "markdown": self._parse_markdown,
            "xml": self._parse_xml,
            "yaml": self._parse_yaml,
            "code_with_comments": self._parse_code_with_comments,
            "plain": self._parse_plain,
        }

        parser = parsers.get(self.format, self._parse_plain)
        return parser()

    def _parse_jupyter(self) -> list[dict[str, Any]]:
        """解析 Jupyter Notebook：代码 + Markdown 注释。"""
        try:
            import nbformat

            nb = nbformat.read(self.path, as_version=4)
        except ImportError:
            # 回退到简单 JSON 解析
            return self._parse_jupyter_simple()

        records = []

        for i, cell in enumerate(nb.cells):
            base = {
                "doc_id": self.path.stem,
                "cell_index": i,
                "source_file": str(self.path),
            }

            if cell.cell_type == "code":
                # 代码块
                records.append(
                    {
                        **base,
                        "type": "code",
                        "lang": self._detect_lang(cell.source),
                        "text": cell.source,
                        "outputs": self._extract_outputs(cell.outputs),
                        "execution_count": getattr(cell, "execution_count", None),
                        "_kind": "code_block",
                        "_table": self._infer_table_from_code(cell.source),
                    }
                )

            elif cell.cell_type == "markdown":
                # Markdown 注释
                concepts = self._extract_concepts_from_text(cell.source)
                records.append(
                    {
                        **base,
                        "type": "annotation",
                        "annotation_type": "markdown",
                        "text": cell.source,
                        "extracted_concepts": concepts,
                        "_kind": "annotation",
                        "_table": "documentation",
                    }
                )

        # 建立代码-注释关系
        return self._link_code_annotation(records)

    def _parse_jupyter_simple(self) -> list[dict[str, Any]]:
        """简化的 Jupyter 解析（无需 nbformat）。"""
        content = json.loads(self._get_content())
        records = []

        for i, cell in enumerate(content.get("cells", [])):
            base = {
                "doc_id": self.path.stem,
                "cell_index": i,
                "source_file": str(self.path),
            }

            cell_type = cell.get("cell_type", "")
            source = "".join(cell.get("source", []))

            if cell_type == "code":
                records.append(
                    {
                        **base,
                        "type": "code",
                        "lang": self._detect_lang(source),
                        "text": source,
                        "_kind": "code_block",
                    }
                )
            elif cell_type == "markdown":
                records.append(
                    {
                        **base,
                        "type": "annotation",
                        "annotation_type": "markdown",
                        "text": source,
                        "extracted_concepts": self._extract_concepts_from_text(source),
                        "_kind": "annotation",
                    }
                )

        return self._link_code_annotation(records)

    def _parse_markdown(self) -> list[dict[str, Any]]:
        """解析 Markdown：```代码块 + 文本说明。"""
        content = self._get_content()
        records = []

        # 匹配代码块: ```lang\ncode\n```
        # 使用非贪婪匹配，并处理可选的语言标识
        code_block_pattern = r'```(\w+)?\n(.*?)```'

        # 分割文本和代码块
        parts = re.split(code_block_pattern, content, flags=re.DOTALL)

        # parts 结构: [text0, lang1, code1, text1, lang2, code2, text2, ...]
        i = 0
        while i < len(parts):
            part = parts[i].strip()

            if i == 0:
                # 第一个文本块
                if part:
                    records.append(self._create_text_record(part, i))
                i += 1
            elif i % 3 == 1 and i + 1 < len(parts):
                # 语言标识 + 代码块
                lang = part or "text"
                code = parts[i + 1].strip()
                if code:
                    records.append(
                        self._create_code_record(code, lang, len(records))
                    )
                i += 2
                # 代码块后的文本
                if i < len(parts) and parts[i].strip():
                    records.append(self._create_text_record(parts[i].strip(), i))
                i += 1
            else:
                i += 1

        return self._link_code_annotation(records)

    def _parse_xml(self) -> list[dict[str, Any]]:
        """解析 XML 标注文档。"""
        import xml.etree.ElementTree as ET

        try:
            tree = ET.parse(self.path)
            root = tree.getroot()
        except ET.ParseError:
            # 回退到正则解析
            return self._parse_xml_regex()

        records = []

        # 支持多种 XML 结构
        # 1. <snippet code="..." annotation="..."/>
        # 2. <annotation><code>...</code><text>...</text></annotation>
        # 3. <example><input>...</input><label>...</label></example>

        for elem in root.iter():
            if elem.tag in ("snippet", "example", "sample"):
                code = elem.get("code", "")
                annotation = elem.get("annotation", "")
                tags = elem.get("tags", "").split(",")

                if not code:
                    # 尝试子元素
                    code_elem = elem.find("code") or elem.find("input")
                    if code_elem is not None:
                        code = code_elem.text or ""

                if not annotation:
                    annot_elem = elem.find("annotation") or elem.find("label") or elem.find("text")
                    if annot_elem is not None:
                        annotation = annot_elem.text or ""

                if code or annotation:
                    records.append(
                        {
                            "type": "code_with_annotation",
                            "code": code,
                            "annotation": annotation,
                            "tags": [t.strip() for t in tags if t],
                            "extracted_concepts": self._extract_from_annotation(
                                annotation
                            ),
                            "_kind": "annotated_code",
                        }
                    )

        return records

    def _parse_xml_regex(self) -> list[dict[str, Any]]:
        """使用正则解析不完整的 XML。"""
        content = self._get_content()
        records = []

        # 匹配 <snippet code="..." annotation="..."/>
        pattern = r'<snippet[^>]*code="([^"]*)"[^>]*annotation="([^"]*)"[^>]*/>'
        for match in re.finditer(pattern, content):
            records.append(
                {
                    "type": "code_with_annotation",
                    "code": match.group(1),
                    "annotation": match.group(2),
                    "_kind": "annotated_code",
                }
            )

        return records

    def _parse_yaml(self) -> list[dict[str, Any]]:
        """解析 YAML 标注文档。
        
        支持常见的 YAML 标注格式：
        - 代码-描述对: {code: "...", description: "...", tags: [...]}
        - 问答对: {question: "...", answer: "...", category: "..."}
        - 指令-输出对: {instruction: "...", input: "...", output: "..."}
        - 实体标注: {text: "...", entities: [{start: 0, end: 5, label: "PERSON"}]}
        """
        content = self._get_content()
        records = []
        
        try:
            import yaml
            data = yaml.safe_load(content)
        except ImportError:
            # 回退到简单解析
            return self._parse_yaml_simple()
        except yaml.YAMLError:
            return self._parse_yaml_simple()
        
        if data is None:
            return records
        
        # 确保是列表
        if isinstance(data, dict):
            # 单文档或带键的字典，提取值
            if "data" in data:
                data = data["data"]
            elif "examples" in data:
                data = data["examples"]
            elif "samples" in data:
                data = data["samples"]
            else:
                data = [data]
        
        if not isinstance(data, list):
            data = [data]
        
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                continue
            
            record = self._extract_yaml_record(item, i)
            if record:
                records.append(record)
        
        return records
    
    def _extract_yaml_record(self, item: dict[str, Any], index: int) -> dict[str, Any] | None:
        """从 YAML 字典项提取记录。"""
        # 检测记录类型
        code = self._get_yaml_code(item)
        annotation = self._get_yaml_annotation(item)
        tags = self._get_yaml_tags(item)
        
        if code or annotation:
            return {
                "type": "code_with_annotation" if code else "annotation",
                "code": code or "",
                "annotation": annotation or "",
                "tags": tags,
                "extracted_concepts": self._extract_concepts_from_text(annotation or code or ""),
                "_kind": "annotated_code" if code else "annotation",
                "_index": index,
                # 保留原始字段用于追踪
                "_original_keys": list(item.keys()),
            }
        
        # 纯文本记录
        if "text" in item:
            return {
                "type": "text",
                "text": item["text"],
                "metadata": {k: v for k, v in item.items() if k != "text"},
                "extracted_concepts": self._extract_concepts_from_text(item["text"]),
                "_kind": "text",
                "_index": index,
            }
        
        return None
    
    def _get_yaml_code(self, item: dict[str, Any]) -> str:
        """提取 YAML 中的代码字段。"""
        code_fields = ["code", "sql", "query", "script", "command", "snippet", 
                       "input", "prompt", "source", "content"]
        for field in code_fields:
            if field in item and isinstance(item[field], str):
                code = item[field].strip()
                if code:
                    return code
        return ""
    
    def _get_yaml_annotation(self, item: dict[str, Any]) -> str:
        """提取 YAML 中的标注/描述字段。"""
        annot_fields = ["description", "annotation", "comment", "note", "explanation",
                        "output", "answer", "response", "result", "label",
                        "intent", "purpose", "meaning"]
        for field in annot_fields:
            if field in item and isinstance(item[field], str):
                annot = item[field].strip()
                if annot:
                    return annot
        return ""
    
    def _get_yaml_tags(self, item: dict[str, Any]) -> list[str]:
        """提取 YAML 中的标签。"""
        tag_fields = ["tags", "categories", "labels", "types", "keywords"]
        for field in tag_fields:
            if field in item:
                tags = item[field]
                if isinstance(tags, list):
                    # 清理每个标签（去除方括号等）
                    cleaned = []
                    for t in tags:
                        if t:
                            t_str = str(t).strip()
                            # 移除可能的方括号
                            t_str = t_str.strip("[]'")
                            if t_str:
                                cleaned.append(t_str)
                    return cleaned
                elif isinstance(tags, str):
                    # 处理字符串形式的列表（如 "[a, b, c]"）
                    tags_str = tags.strip()
                    if tags_str.startswith("[") and tags_str.endswith("]"):
                        # 解析为列表
                        inner = tags_str[1:-1]
                        return [t.strip().strip("'\"") for t in inner.split(",") if t.strip()]
                    else:
                        # 逗号分隔
                        return [t.strip() for t in tags.split(",") if t.strip()]
        
        # 从 category/type 字段提取单标签
        if "category" in item:
            return [str(item["category"])]
        if "type" in item:
            return [str(item["type"])]
        
        return []
    
    def _parse_yaml_simple(self) -> list[dict[str, Any]]:
        """简单 YAML 解析（无需 pyyaml）。"""
        content = self._get_content()
        records = []
        
        # 分割文档（--- 分隔符）
        docs = re.split(r"\n---\s*\n", content)
        
        for doc_idx, doc in enumerate(docs):
            lines = doc.strip().split("\n")
            current_item: dict[str, Any] = {}
            current_key = None
            
            for line in lines:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                
                # 顶级键值对
                top_match = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(.+)$", stripped)
                if top_match:
                    current_key = top_match.group(1)
                    current_item[current_key] = top_match.group(2).strip()
                    continue
                
                # 列表项开始
                list_match = re.match(r"^-\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(.+)$", stripped)
                if list_match:
                    if current_item:
                        record = self._extract_yaml_record(current_item, len(records))
                        if record:
                            records.append(record)
                    current_item = {list_match.group(1): list_match.group(2).strip()}
                    current_key = list_match.group(1)
                    continue
                
                # 多行值续接
                if current_key and line.startswith(" "):
                    current_item[current_key] = current_item.get(current_key, "") + "\n" + stripped
            
            # 处理最后一个项
            if current_item:
                record = self._extract_yaml_record(current_item, len(records))
                if record:
                    records.append(record)
        
        return records

    def _parse_code_with_comments(self) -> list[dict[str, Any]]:
        """解析代码文件，提取注释和代码的关系。"""
        content = self._get_content()
        ext = self.path.suffix.lower()

        # 根据语言选择注释模式
        if ext == ".py":
            lang = "python"
        elif ext in (".js", ".ts", ".java", ".go"):
            lang = ext.lstrip(".")
        elif ext == ".sql":
            lang = "sql"
        else:
            lang = "unknown"

        records = []
        lines = content.split("\n")

        current_comment = []
        current_code = []
        line_num = 0

        for line in lines:
            line_num += 1
            stripped = line.strip()

            # 检测注释行
            is_comment = False
            if lang == "python":
                is_comment = stripped.startswith("#") or stripped.startswith('"""')
            elif lang == "sql":
                is_comment = stripped.startswith("--") or stripped.startswith("/*")
            else:
                is_comment = stripped.startswith("//") or stripped.startswith("/*")

            if is_comment or not stripped:
                # 保存之前的代码块
                if current_code:
                    code_text = "\n".join(current_code)
                    records.append(
                        {
                            "type": "code",
                            "lang": lang,
                            "text": code_text,
                            "preceding_comment": "\n".join(current_comment),
                            "start_line": line_num - len(current_code),
                            "_kind": "code_block",
                        }
                    )
                    current_code = []

                if stripped:
                    current_comment.append(line)
            else:
                # 代码行
                if current_comment:
                    # 保存注释
                    comment_text = "\n".join(current_comment)
                    records.append(
                        {
                            "type": "annotation",
                            "annotation_type": "code_comment",
                            "text": comment_text,
                            "extracted_concepts": self._extract_concepts_from_text(
                                comment_text
                            ),
                            "line": line_num,
                            "_kind": "annotation",
                        }
                    )
                    current_comment = []

                current_code.append(line)

        # 处理最后的块
        if current_code:
            records.append(
                {
                    "type": "code",
                    "lang": lang,
                    "text": "\n".join(current_code),
                    "preceding_comment": "\n".join(current_comment),
                    "_kind": "code_block",
                }
            )

        return self._link_code_annotation(records)

    def _parse_plain(self) -> list[dict[str, Any]]:
        """解析纯文本文件。"""
        content = self._get_content()

        return [
            {
                "type": "text",
                "text": content,
                "extracted_concepts": self._extract_concepts_from_text(content),
                "_kind": "plain_text",
            }
        ]

    def _create_code_record(self, code: str, lang: str, index: int) -> dict[str, Any]:
        """创建代码记录。"""
        return {
            "type": "code",
            "lang": lang,
            "text": code,
            "_kind": "code_block",
            "_table": self._infer_table_from_code(code),
            "index": index,
        }

    def _create_text_record(self, text: str, index: int) -> dict[str, Any]:
        """创建文本记录。"""
        concepts = self._extract_concepts_from_text(text)
        return {
            "type": "annotation",
            "annotation_type": "documentation",
            "text": text,
            "extracted_concepts": concepts,
            "_kind": "annotation",
            "_table": "documentation",
            "index": index,
        }

    def _detect_lang(self, code: str) -> str:
        """检测代码语言。"""
        # SQL 特征
        if re.search(
            r"\b(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|FROM|WHERE|JOIN|TABLE)\b",
            code,
            re.IGNORECASE,
        ):
            return "sql"

        # Python 特征
        if re.search(r"\b(def |class |import |from .+ import|if __name__)", code):
            return "python"

        # JavaScript/TypeScript 特征
        if re.search(r"\b(const |let |var |function |=> |async |await )", code):
            if ":" in code.split("\n")[0] and "interface" in code:
                return "typescript"
            return "javascript"

        # Java 特征
        if re.search(r"\b(public |private |class \w+ \{|void |String |int )", code):
            return "java"

        return "unknown"

    def _extract_concepts_from_text(self, text: str) -> list[str]:
        """从文本中提取概念。"""
        concepts = set()

        # 代码术语 `term`
        concepts.update(re.findall(r"`([^`]+)`", text))

        # 粗体/斜体 **term** 或 *term*
        concepts.update(re.findall(r"\*\*([^*]+)\*\*", text))
        concepts.update(re.findall(r"\*([^*]+)\*", text))

        # 大写驼峰（可能是类名）
        concepts.update(
            re.findall(r"\b([A-Z][a-zA-Z0-9]*(?:[A-Z][a-zA-Z0-9]+)+)\b", text)
        )

        # 章节标题
        header_matches = re.findall(r"^#{1,4}\s+(.+)$", text, re.MULTILINE)
        concepts.update(header_matches)

        # 带括号的术语
        concepts.update(re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s*\(", text))

        # 过滤和清理
        result = []
        for c in concepts:
            c = c.strip()
            if len(c) > 2 and len(c) < 100:  # 长度过滤
                result.append(c)

        return sorted(set(result))

    def _extract_from_annotation(self, annotation: str) -> list[str]:
        """从标注文本中提取关键概念。"""
        return self._extract_concepts_from_text(annotation)

    def _infer_table_from_code(self, code: str) -> str | None:
        """从代码中推断表名（主要用于 SQL）。"""
        # 匹配 SQL 表名
        patterns = [
            r"\bFROM\s+[`\"\[]?([\w\.]+)[`\"\[]?",
            r"\bINTO\s+[`\"\[]?([\w\.]+)[`\"\[]?",
            r"\bJOIN\s+[`\"\[]?([\w\.]+)[`\"\[]?",
            r"\bTABLE\s+[`\"\[]?([\w\.]+)[`\"\[]?",
        ]

        for pattern in patterns:
            match = re.search(pattern, code, re.IGNORECASE)
            if match:
                return match.group(1).strip("`\"[]")

        return None

    def _extract_outputs(self, outputs: list[Any]) -> list[dict[str, Any]]:
        """提取 Jupyter cell 的输出。"""
        result = []
        for output in outputs:
            if isinstance(output, dict):
                output_type = output.get("output_type", "")
                if output_type == "execute_result":
                    result.append(
                        {
                            "type": "result",
                            "data": output.get("data", {}),
                        }
                    )
                elif output_type == "stream":
                    result.append(
                        {
                            "type": "stream",
                            "text": output.get("text", ""),
                        }
                    )
                elif output_type in ("display_data", "error"):
                    result.append(
                        {
                            "type": output_type,
                            "data": output.get("data", {}),
                            "ename": output.get("ename", ""),
                            "evalue": output.get("evalue", ""),
                        }
                    )
        return result

    def _link_code_annotation(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """建立代码和前置注释的关联。"""
        for i, rec in enumerate(records):
            if rec.get("type") == "code" and i > 0:
                prev = records[i - 1]
                if prev.get("type") == "annotation":
                    rec["context_annotation"] = prev["text"]
                    rec["context_concepts"] = prev.get("extracted_concepts", [])
        return records


def parse_mixed_document(doc_path: Path) -> list[dict[str, Any]]:
    """便捷函数：解析混合文档。"""
    parser = MixedDocumentParser(doc_path)
    return parser.parse()


def main() -> int:
    """CLI 入口。"""
    import argparse

    parser = argparse.ArgumentParser(description="Parse mixed documents for AOF")
    parser.add_argument("--input", required=True, help="Input file path")
    parser.add_argument("--output", required=True, help="Output JSONL path")
    parser.add_argument("--format", choices=["auto", "jupyter", "markdown", "xml", "code"], default="auto")

    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: File not found: {input_path}")
        return 1

    # 解析
    records = parse_mixed_document(input_path)

    # 输出
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # 统计
    type_counts = {}
    for rec in records:
        t = rec.get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    print(f"[ok] parsed={len(records)}")
    print(f"[format] detected={MixedDocumentParser(input_path).format}")
    for t, count in sorted(type_counts.items()):
        print(f"  - {t}: {count}")
    print(f"[output] {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
