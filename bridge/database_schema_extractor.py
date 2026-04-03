#!/usr/bin/env python3
"""数据库 Schema 提取模块 - 自动从数据库提取 Schema 并生成 OWL 本体。

本模块直接调用 Cognee 引擎的能力，支持：
- 自动连接数据库并提取 Schema（表、列、主键、外键）
- 将 Schema 转换为 OWL 本体结构
- 与现有本体合并或生成差异报告
- 支持多种数据库（PostgreSQL, MySQL, SQLite 等）

使用示例:
    extractor = DatabaseSchemaExtractor()
    result = await extractor.extract_and_update_ontology(
        db_config={"url": "postgresql://user:pass@localhost/db"},
        ontology_file=Path("ontologies/my_db.owl"),
        mode="merge"
    )
"""

from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class MergeMode(str, Enum):
    """本体合并模式。"""
    MERGE = "merge"  # 合并新发现的内容到现有本体
    REPLACE = "replace"  # 完全替换现有本体
    DIFF = "diff"  # 仅生成差异报告，不修改本体


@dataclass
class ClassDefinition:
    """OWL Class 定义。"""
    name: str
    comment: str = ""
    parent: str = "Thing"


@dataclass
class DatatypeProperty:
    """OWL DatatypeProperty 定义。"""
    name: str
    domain: str
    range_type: str = "string"
    comment: str = ""


@dataclass
class ObjectProperty:
    """OWL ObjectProperty 定义。"""
    name: str
    domain: str
    range_class: str
    comment: str = ""


@dataclass
class Ontology:
    """OWL 本体结构。"""
    classes: list[ClassDefinition] = field(default_factory=list)
    datatype_properties: list[DatatypeProperty] = field(default_factory=list)
    object_properties: list[ObjectProperty] = field(default_factory=list)

    def to_owl_xml(self) -> str:
        """转换为 OWL XML 格式。"""
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"',
            '         xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"',
            '         xmlns:owl="http://www.w3.org/2002/07/owl#"',
            '         xmlns:ex="http://example.org/ontology#">',
            '',
            '  <!-- Auto-generated from database schema -->',
            '',
        ]

        # Classes
        for cls in self.classes:
            lines.extend([
                f'  <owl:Class rdf:about="http://example.org/ontology#{cls.name}">',
                f'    <rdfs:comment>{self._escape_xml(cls.comment)}</rdfs:comment>',
            ])
            if cls.parent != "Thing":
                lines.append(
                    f'    <rdfs:subClassOf rdf:resource="http://example.org/ontology#{cls.parent}"/>'
                )
            lines.extend([
                '  </owl:Class>',
                '',
            ])

        # DatatypeProperties
        for prop in self.datatype_properties:
            lines.extend([
                f'  <owl:DatatypeProperty rdf:about="http://example.org/ontology#{prop.name}">',
                f'    <rdfs:domain rdf:resource="http://example.org/ontology#{prop.domain}"/>',
                f'    <rdfs:range rdf:resource="http://www.w3.org/2001/XMLSchema#{prop.range_type}"/>',
            ])
            if prop.comment:
                lines.append(f'    <rdfs:comment>{self._escape_xml(prop.comment)}</rdfs:comment>')
            lines.extend([
                '  </owl:DatatypeProperty>',
                '',
            ])

        # ObjectProperties
        for prop in self.object_properties:
            lines.extend([
                f'  <owl:ObjectProperty rdf:about="http://example.org/ontology#{prop.name}">',
                f'    <rdfs:domain rdf:resource="http://example.org/ontology#{prop.domain}"/>',
                f'    <rdfs:range rdf:resource="http://example.org/ontology#{prop.range_class}"/>',
            ])
            if prop.comment:
                lines.append(f'    <rdfs:comment>{self._escape_xml(prop.comment)}</rdfs:comment>')
            lines.extend([
                '  </owl:ObjectProperty>',
                '',
            ])

        lines.append('</rdf:RDF>')
        return '\n'.join(lines)

    @staticmethod
    def _escape_xml(text: str) -> str:
        """XML 特殊字符转义。"""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )


@dataclass
class OntologyUpdateResult:
    """本体更新结果。"""
    tables_discovered: int = 0
    new_classes: list[str] = field(default_factory=list)
    new_properties: list[str] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    diff_report: Optional[str] = None


class DatabaseSchemaExtractor:
    """数据库 Schema 提取器。
    
    直接调用 Cognee 引擎提取数据库结构并转换为 OWL 本体。
    """

    # SQL 类型到 XSD 类型的映射
    SQL_TO_XSD_TYPE = {
        # 字符串类型
        "varchar": "string",
        "char": "string",
        "text": "string",
        "string": "string",
        # 整数类型
        "integer": "integer",
        "int": "integer",
        "bigint": "long",
        "smallint": "short",
        # 浮点类型
        "float": "float",
        "double": "double",
        "decimal": "decimal",
        "numeric": "decimal",
        # 布尔类型
        "boolean": "boolean",
        "bool": "boolean",
        # 日期时间类型
        "date": "date",
        "datetime": "dateTime",
        "timestamp": "dateTime",
        "time": "time",
        # 二进制类型
        "blob": "base64Binary",
        "bytea": "base64Binary",
        # 默认
        "default": "string",
    }

    def __init__(self):
        self._cognee_available = self._check_cognee()

    def _check_cognee(self) -> bool:
        """检查 Cognee 是否可用。"""
        return importlib.util.find_spec("cognee") is not None

    async def extract_schema(
        self,
        db_url: str,
        provider: str = "postgresql",
    ) -> dict[str, Any]:
        """从数据库提取 Schema。
        
        Args:
            db_url: 数据库连接字符串
            provider: 数据库类型 (postgresql, mysql, sqlite)
            
        Returns:
            Schema 字典: {table_name: {columns, primary_key, foreign_keys}}
        """
        if not self._cognee_available:
            raise RuntimeError(
                "Cognee 未安装。请安装 Cognee 以使用数据库 Schema 提取功能。"
            )

        # 导入 Cognee 相关模块
        from cognee.infrastructure.databases.relational.config import (
            get_migration_config,
        )
        from cognee.infrastructure.databases.relational import (
            get_migration_relational_engine,
        )

        # 配置数据库连接
        migration_config = get_migration_config()
        migration_config.migration_db_provider = provider
        migration_config.migration_db_path = db_url if provider == "sqlite" else ""
        
        # 对于非 SQLite 数据库，设置连接字符串
        if provider != "sqlite":
            # 动态设置连接字符串
            migration_config.database_url = db_url

        try:
            engine = get_migration_relational_engine()
            schema = await engine.extract_schema()
            return schema
        except Exception as e:
            raise RuntimeError(f"提取 Schema 失败: {e}") from e

    async def extract_and_update_ontology(
        self,
        db_config: dict[str, str],
        ontology_file: Path,
        mode: MergeMode = MergeMode.MERGE,
    ) -> OntologyUpdateResult:
        """提取 Schema 并更新 OWL 本体。
        
        Args:
            db_config: 数据库配置 {"url": "...", "provider": "postgresql"}
            ontology_file: 现有本体文件路径
            mode: 合并模式 (merge/replace/diff)
            
        Returns:
            更新结果
        """
        db_url = db_config.get("url", "")
        provider = db_config.get("provider", "postgresql")

        # 1. 提取数据库 Schema
        schema = await self.extract_schema(db_url, provider)

        # 2. 转换为 OWL 本体
        schema_ontology = self._schema_to_ontology(schema)

        # 3. 根据模式处理
        if mode == MergeMode.DIFF:
            # 生成差异报告
            diff = await self._generate_diff(ontology_file, schema_ontology)
            return OntologyUpdateResult(
                tables_discovered=len(schema),
                diff_report=diff,
            )
        elif mode == MergeMode.REPLACE:
            # 完全替换
            await self._write_ontology(ontology_file, schema_ontology)
            return OntologyUpdateResult(
                tables_discovered=len(schema),
                new_classes=[c.name for c in schema_ontology.classes],
                new_properties=[
                    p.name for p in schema_ontology.datatype_properties
                ] + [p.name for p in schema_ontology.object_properties],
            )
        else:  # MERGE
            # 合并模式
            merged = await self._merge_ontology(ontology_file, schema_ontology)
            await self._write_ontology(ontology_file, merged.ontology)
            return OntologyUpdateResult(
                tables_discovered=len(schema),
                new_classes=merged.new_classes,
                new_properties=merged.new_properties,
                conflicts=merged.conflicts,
            )

    def _schema_to_ontology(self, schema: dict[str, Any]) -> Ontology:
        """将数据库 Schema 转换为 OWL 本体。"""
        ontology = Ontology()

        # 添加基础类
        ontology.classes.append(
            ClassDefinition(
                name="DatabaseTable",
                comment="Base class for all database tables",
                parent="Thing",
            )
        )
        ontology.classes.append(
            ClassDefinition(
                name="DatabaseColumn",
                comment="Base class for database columns",
                parent="Thing",
            )
        )

        for table_name, table_info in schema.items():
            # 表名转换为类名
            class_name = self._to_class_name(table_name)
            
            # 创建类定义
            cls = ClassDefinition(
                name=class_name,
                comment=f"Database table: {table_name}",
                parent="DatabaseTable",
            )
            ontology.classes.append(cls)

            # 处理列
            for col in table_info.get("columns", []):
                col_name = col.get("name", "")
                col_type = col.get("type", "string")
                
                # 创建 DatatypeProperty
                prop_name = f"has{self._to_pascal(col_name)}"
                xsd_type = self._sql_type_to_xsd(col_type)
                
                prop = DatatypeProperty(
                    name=prop_name,
                    domain=class_name,
                    range_type=xsd_type,
                    comment=f"Column {col_name} of type {col_type}",
                )
                ontology.datatype_properties.append(prop)

            # 处理外键关系
            for fk in table_info.get("foreign_keys", []):
                ref_table = fk.get("ref_table", "")
                ref_class = self._to_class_name(ref_table)
                
                # 创建 ObjectProperty
                rel_name = f"relatesTo{ref_class}"
                
                # 检查是否已存在（避免重复）
                if not any(p.name == rel_name and p.domain == class_name for p in ontology.object_properties):
                    obj_prop = ObjectProperty(
                        name=rel_name,
                        domain=class_name,
                        range_class=ref_class,
                        comment=f"Foreign key relationship to {ref_table}",
                    )
                    ontology.object_properties.append(obj_prop)

        return ontology

    def _to_class_name(self, table_name: str) -> str:
        """将表名转换为类名。
        
        规则:
        - users -> User
        - user_profiles -> UserProfile
        - order_items -> OrderItem
        """
        # 移除常见前缀
        name = re.sub(r"^(tbl_|table_|t_)", "", table_name, flags=re.IGNORECASE)
        
        # 蛇形命名转驼峰
        parts = name.split("_")
        return "".join(p.capitalize() for p in parts if p)

    def _to_pascal(self, name: str) -> str:
        """转换为 PascalCase。"""
        parts = name.split("_")
        return "".join(p.capitalize() for p in parts if p)

    def _sql_type_to_xsd(self, sql_type: str) -> str:
        """将 SQL 类型转换为 XSD 类型。"""
        sql_type_lower = sql_type.lower()
        
        # 处理带参数的类型的 (varchar(255))
        base_type = re.sub(r"\(.*\)", "", sql_type_lower).strip()
        
        return self.SQL_TO_XSD_TYPE.get(base_type, "string")

    async def _generate_diff(
        self,
        existing_file: Path,
        new_ontology: Ontology,
    ) -> str:
        """生成差异报告。"""
        if not existing_file.exists():
            return f"新本体将包含 {len(new_ontology.classes)} 个类"

        # 简化的差异报告
        lines = [
            "# Schema 差异报告",
            "",
            f"## 新发现的类 ({len(new_ontology.classes)})",
        ]
        for cls in new_ontology.classes:
            lines.append(f"- {cls.name}: {cls.comment}")

        lines.extend([
            "",
            f"## 新发现的属性 ({len(new_ontology.datatype_properties) + len(new_ontology.object_properties)})",
        ])
        for prop in new_ontology.datatype_properties:
            lines.append(f"- {prop.name} (datatype): {prop.comment}")
        for prop in new_ontology.object_properties:
            lines.append(f"- {prop.name} (object): {prop.comment}")

        return "\n".join(lines)

    async def _merge_ontology(
        self,
        existing_file: Path,
        new_ontology: Ontology,
    ) -> Any:
        """合并本体。"""
        from dataclasses import dataclass

        @dataclass
        class MergeResult:
            ontology: Ontology
            new_classes: list[str]
            new_properties: list[str]
            conflicts: list[dict]

        if not existing_file.exists():
            return MergeResult(
                ontology=new_ontology,
                new_classes=[c.name for c in new_ontology.classes],
                new_properties=[
                    p.name for p in new_ontology.datatype_properties
                ] + [p.name for p in new_ontology.object_properties],
                conflicts=[],
            )

        # 简化实现：合并两个本体的内容
        # 实际应该解析现有 OWL 文件进行合并
        merged = Ontology()
        new_classes = []
        new_properties = []
        conflicts = []

        # 添加新本体的所有内容
        existing_classes = set()  # 实际应从文件解析
        
        for cls in new_ontology.classes:
            if cls.name not in existing_classes:
                merged.classes.append(cls)
                new_classes.append(cls.name)
            else:
                conflicts.append({
                    "type": "class",
                    "name": cls.name,
                    "reason": "Already exists",
                })

        merged.datatype_properties.extend(new_ontology.datatype_properties)
        merged.object_properties.extend(new_ontology.object_properties)
        new_properties = [
            p.name for p in new_ontology.datatype_properties
        ] + [p.name for p in new_ontology.object_properties]

        return MergeResult(
            ontology=merged,
            new_classes=new_classes,
            new_properties=new_properties,
            conflicts=conflicts,
        )

    async def _write_ontology(self, file_path: Path, ontology: Ontology) -> None:
        """写入 OWL 本体文件。"""
        file_path.parent.mkdir(parents=True, exist_ok=True)
        owl_content = ontology.to_owl_xml()
        file_path.write_text(owl_content, encoding="utf-8")


# 便捷函数
async def extract_db_schema_to_ontology(
    db_url: str,
    output_file: Path,
    provider: str = "postgresql",
    mode: str = "merge",
) -> OntologyUpdateResult:
    """便捷函数：提取数据库 Schema 到 OWL 本体。
    
    Args:
        db_url: 数据库连接字符串
        output_file: 输出 OWL 文件路径
        provider: 数据库类型
        mode: 合并模式 (merge/replace/diff)
        
    Returns:
        更新结果
    """
    extractor = DatabaseSchemaExtractor()
    return await extractor.extract_and_update_ontology(
        db_config={"url": db_url, "provider": provider},
        ontology_file=output_file,
        mode=MergeMode(mode),
    )
