"""nGQL 查询构建器

简化 nGQL 查询的构建，提供类似 SQLAlchemy 的流畅接口。

使用示例:
    from bridge.storage.ngql_builder import nGQLBuilder as Q
    
    query = (Q().match()
             .node("entity", "v")
             .edge("relates_to", "e")
             .node("entity", "v2")
             .where(Q.F.v.type == "Person")
             .return_("v.name", "v2.name")
             .limit(10)
             .build())
    
    # 结果: MATCH (v:entity)-[e:relates_to]->(v2:entity) WHERE v.type == "Person" RETURN v.name, v2.name LIMIT 10
"""

from __future__ import annotations

from typing import Optional, List, Dict, Any, Union
from dataclasses import dataclass


class Field:
    """字段引用"""
    
    def __init__(self, name: str, alias: Optional[str] = None):
        self.name = name
        self.alias = alias
    
    def __eq__(self, value) -> "Condition":
        return Condition(f"{self.name} == {self._format_value(value)}")
    
    def __ne__(self, value) -> "Condition":
        return Condition(f"{self.name} != {self._format_value(value)}")
    
    def __gt__(self, value) -> "Condition":
        return Condition(f"{self.name} > {self._format_value(value)}")
    
    def __ge__(self, value) -> "Condition":
        return Condition(f"{self.name} >= {self._format_value(value)}")
    
    def __lt__(self, value) -> "Condition":
        return Condition(f"{self.name} < {self._format_value(value)}")
    
    def __le__(self, value) -> "Condition":
        return Condition(f"{self.name} <= {self._format_value(value)}")
    
    def contains(self, value) -> "Condition":
        return Condition(f"{self.name} CONTAINS {self._format_value(value)}")
    
    def startswith(self, value) -> "Condition":
        return Condition(f"{self.name} STARTS WITH {self._format_value(value)}")
    
    def in_(self, values: list) -> "Condition":
        formatted = ", ".join(self._format_value(v) for v in values)
        return Condition(f"{self.name} IN [{formatted}]")
    
    def _format_value(self, value) -> str:
        if isinstance(value, str):
            return f'"{value}"'
        elif isinstance(value, bool):
            return "true" if value else "false"
        elif value is None:
            return "NULL"
        return str(value)
    
    def __str__(self):
        return self.name


class F:
    """字段访问器（类似 SQLAlchemy 的 column）"""
    
    @classmethod
    def __getattr__(cls, name: str) -> Field:
        return Field(name)
    
    @staticmethod
    def field(name: str) -> Field:
        return Field(name)


@dataclass
class Condition:
    """查询条件"""
    expression: str
    
    def __and__(self, other: "Condition") -> "Condition":
        return Condition(f"({self.expression} AND {other.expression})")
    
    def __or__(self, other: "Condition") -> "Condition":
        return Condition(f"({self.expression} OR {other.expression})")
    
    def __invert__(self) -> "Condition":
        return Condition(f"NOT ({self.expression})")
    
    def __str__(self):
        return self.expression


class nGQLBuilder:
    """nGQL 查询构建器"""
    
    def __init__(self):
        self._parts: List[str] = []
        self._match_parts: List[str] = []
        self._where_conditions: List[str] = []
        self._return_fields: List[str] = []
        self._order_by: Optional[str] = None
        self._limit: Optional[int] = None
        self._skip: Optional[int] = None
    
    # ========== MATCH 子句 ==========
    
    def match(self) -> "nGQLBuilder":
        """开始 MATCH 模式"""
        self._match_parts = ["MATCH"]
        return self
    
    def node(
        self,
        tag: Optional[str] = None,
        alias: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None,
    ) -> "nGQLBuilder":
        """添加节点模式 (v:tag{props})"""
        parts = []
        if alias:
            parts.append(alias)
        
        if tag:
            parts.append(f":{tag}")
        
        if properties:
            props_str = ", ".join(f'{k}: "{v}"' for k, v in properties.items())
            parts.append(f"{{{props_str}}}")
        
        self._match_parts.append(f"({''.join(parts)})")
        return self
    
    def edge(
        self,
        edge_type: Optional[str] = None,
        alias: Optional[str] = None,
        direction: str = "out",  # "out", "in", "both"
        properties: Optional[Dict[str, Any]] = None,
    ) -> "nGQLBuilder":
        """添加边模式 -[e:type]-> 或 <-[e:type]-"""
        parts = []
        if alias:
            parts.append(alias)
        
        if edge_type:
            parts.append(f":{edge_type}")
        
        if properties:
            props_str = ", ".join(f'{k}: "{v}"' for k, v in properties.items())
            parts.append(f"{{{props_str}}}")
        
        edge_str = f"[{''.join(parts)}]"
        
        if direction == "out":
            self._match_parts.append(f"-{edge_str}->")
        elif direction == "in":
            self._match_parts.append(f"<-{edge_str}-")
        else:  # both
            self._match_parts.append(f"-{edge_str}-")
        
        return self
    
    def go(self, steps: int = 1, from_vid: Optional[str] = None) -> "nGQLBuilder":
        """GO 语句（NebulaGraph 特有）"""
        self._parts.append(f"GO {steps} STEP")
        
        if from_vid:
            self._parts.append(f'FROM "{from_vid}"')
        
        return self
    
    def over(self, *edge_types: str) -> "nGQLBuilder":
        """OVER 子句（NebulaGraph 特有）"""
        if edge_types:
            self._parts.append(f'OVER {", ".join(edge_types)}')
        else:
            self._parts.append("OVER *")
        return self
    
    def reversely(self) -> "nGQLBuilder":
        """REVERSELY 反向遍历"""
        self._parts.append("REVERSELY")
        return self
    
    def bidirect(self) -> "nGQLBuilder":
        """BIDIRECT 双向遍历"""
        self._parts.append("BIDIRECT")
        return self
    
    def upto(self, max_steps: int) -> "nGQLBuilder":
        """UPTO 最大步数"""
        self._parts.append(f"UPTO {max_steps} STEPS")
        return self
    
    # ========== WHERE 子句 ==========
    
    def where(self, condition: Union[Condition, str]) -> "nGQLBuilder":
        """添加 WHERE 条件"""
        if isinstance(condition, Condition):
            self._where_conditions.append(str(condition))
        else:
            self._where_conditions.append(condition)
        return self
    
    def and_where(self, condition: Union[Condition, str]) -> "nGQLBuilder":
        """添加 AND 条件"""
        if isinstance(condition, Condition):
            self._where_conditions.append(f"AND {condition}")
        else:
            self._where_conditions.append(f"AND {condition}")
        return self
    
    def or_where(self, condition: Union[Condition, str]) -> "nGQLBuilder":
        """添加 OR 条件"""
        if isinstance(condition, Condition):
            self._where_conditions.append(f"OR {condition}")
        else:
            self._where_conditions.append(f"OR {condition}")
        return self
    
    # ========== RETURN 子句 ==========
    
    def return_(self, *fields: str) -> "nGQLBuilder":
        """添加 RETURN 字段"""
        self._return_fields.extend(fields)
        return self
    
    def return_all(self) -> "nGQLBuilder":
        """RETURN *"""
        self._return_fields = ["*"]
        return self
    
    def distinct(self) -> "nGQLBuilder":
        """RETURN DISTINCT"""
        self._return_fields.insert(0, "DISTINCT")
        return self
    
    def as_(self, alias: str) -> "nGQLBuilder":
        """设置别名（对最后一个字段）"""
        if self._return_fields:
            last = self._return_fields[-1]
            if not last.upper().startswith("DISTINCT"):
                self._return_fields[-1] = f"{last} AS {alias}"
        return self
    
    # ========== ORDER BY 子句 ==========
    
    def order_by(self, field: str, direction: str = "ASC") -> "nGQLBuilder":
        """ORDER BY 排序"""
        self._order_by = f"ORDER BY {field} {direction}"
        return self
    
    # ========== LIMIT/OFFSET 子句 ==========
    
    def limit(self, n: int) -> "nGQLBuilder":
        """LIMIT 限制"""
        self._limit = n
        return self
    
    def skip(self, n: int) -> "nGQLBuilder":
        """SKIP 偏移（NebulaGraph 使用 LIMIT offset, count）"""
        self._skip = n
        return self
    
    # ========== 其他子句 ==========
    
    def yield_(self, *fields: str) -> "nGQLBuilder":
        """YIELD 子句（NebulaGraph 特有）"""
        self._parts.append(f"YIELD {', '.join(fields)}")
        return self
    
    # ========== 构建 ==========
    
    def build(self) -> str:
        """构建最终查询字符串"""
        parts = []
        
        # MATCH 或 GO
        if self._match_parts:
            parts.append(" ".join(self._match_parts))
        else:
            parts.extend(self._parts)
        
        # WHERE
        if self._where_conditions:
            parts.append("WHERE " + " ".join(self._where_conditions))
        
        # RETURN
        if self._return_fields:
            parts.append("RETURN " + ", ".join(self._return_fields))
        
        # ORDER BY
        if self._order_by:
            parts.append(self._order_by)
        
        # LIMIT/OFFSET
        if self._limit is not None:
            if self._skip is not None:
                parts.append(f"LIMIT {self._skip}, {self._limit}")
            else:
                parts.append(f"LIMIT {self._limit}")
        
        return " ".join(parts)
    
    def __str__(self):
        return self.build()


# ========== 便捷函数 ==========

def match() -> nGQLBuilder:
    """开始构建 MATCH 查询"""
    return nGQLBuilder().match()


def go(steps: int = 1, from_vid: Optional[str] = None) -> nGQLBuilder:
    """开始构建 GO 查询"""
    return nGQLBuilder().go(steps, from_vid)


def lookup(tag: str) -> nGQLBuilder:
    """开始构建 LOOKUP 查询"""
    builder = nGQLBuilder()
    builder._parts.append(f"LOOKUP ON {tag}")
    return builder


def fetch(tag: str, vid: str) -> str:
    """构建 FETCH 查询"""
    return f'FETCH PROP ON {tag} "{vid}" YIELD properties(vertex)'


def insert_vertex(
    tag: str,
    properties: List[str],
    values: Dict[str, List[Any]]
) -> str:
    """构建 INSERT VERTEX 语句
    
    Args:
        tag: 标签名
        properties: 属性名列表
        values: {vid: [prop1, prop2, ...]}
    """
    props_str = ", ".join(properties)
    
    value_parts = []
    for vid, props in values.items():
        formatted_props = ", ".join(
            f'"{p}"' if isinstance(p, str) else str(p)
            for p in props
        )
        value_parts.append(f'"{vid}":({formatted_props})')
    
    return f"INSERT VERTEX {tag}({props_str}) VALUES {', '.join(value_parts)}"


def insert_edge(
    edge_type: str,
    properties: List[str],
    values: List[Tuple[str, str, List[Any]]]
) -> str:
    """构建 INSERT EDGE 语句
    
    Args:
        edge_type: 边类型
        properties: 属性名列表
        values: [(src_vid, dst_vid, [prop1, prop2, ...]), ...]
    """
    props_str = ", ".join(properties)
    
    value_parts = []
    for src, dst, props in values:
        formatted_props = ", ".join(
            f'"{p}"' if isinstance(p, str) else str(p)
            for p in props
        )
        value_parts.append(f'"{src}"->"{dst}":({formatted_props})')
    
    return f"INSERT EDGE {edge_type}({props_str}) VALUES {', '.join(value_parts)}"


def delete_vertex(vid: str) -> str:
    """构建 DELETE VERTEX 语句"""
    return f'DELETE VERTEX "{vid}"'


def delete_edge(edge_type: str, src: str, dst: str) -> str:
    """构建 DELETE EDGE 语句"""
    return f'DELETE EDGE {edge_type} "{src}"->"{dst}"'
