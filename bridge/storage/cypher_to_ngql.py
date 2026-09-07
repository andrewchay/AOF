# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Cypher 到 nGQL 转换器

提供基本的 Cypher 查询到 nGQL 的转换。
注意：只支持常见模式，复杂查询需要手动重写。

使用示例:
    from bridge.storage.cypher_to_ngql import CypherToNGQL
    
    converter = CypherToNGQL()
    ngql = converter.convert("MATCH (n)-[:KNOWS]->(m) RETURN n.name, m.name")
"""

from __future__ import annotations

import re
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class ConversionError(Exception):
    """转换错误"""
    pass


class CypherToNGQL:
    """Cypher 到 nGQL 转换器"""
    
    # 常见 Cypher 模式到 nGQL 的映射
    PATTERNS = {
        # 简单节点匹配
        r'MATCH\s*\((\w+):?(\w*)\)\s*RETURN\s*\1':
            lambda m: f'MATCH (v:{m.group(2) or "entity"}) RETURN v',
        
        # 带属性的节点匹配
        r'MATCH\s*\((\w+):?(\w*)\s*\{([^}]+)\}\)\s*RETURN':
            lambda m: _convert_property_match(m),
        
        # 简单关系匹配 -[r]->
        r'MATCH\s*\((\w+)\)-\[(\w*):?(\w*)\]->\((\w+)\)\s*RETURN':
            lambda m: f'MATCH (v1)-[e:{m.group(3) or "relates_to"}]->(v2) RETURN v1, v2',
        
        # 双向关系匹配
        r'MATCH\s*\((\w+)\)-\[(\w*):?(\w*)\]-\((\w+)\)\s*RETURN':
            lambda m: f'MATCH (v1)-[e:{m.group(3) or "relates_to"}]-(v2) RETURN v1, v2',
    }
    
    def __init__(self):
        self.conversion_log: list = []
    
    def convert(self, cypher: str) -> str:
        """转换 Cypher 到 nGQL
        
        Args:
            cypher: Cypher 查询语句
        
        Returns:
            nGQL 查询语句
        
        Raises:
            ConversionError: 如果无法转换
        """
        original = cypher.strip()
        
        # 尝试直接匹配已知模式
        for pattern, converter in self.PATTERNS.items():
            match = re.match(pattern, original, re.IGNORECASE)
            if match:
                try:
                    result = converter(match)
                    self.conversion_log.append({
                        "cypher": original,
                        "ngql": result,
                        "pattern": pattern,
                        "success": True,
                    })
                    return result
                except Exception as e:
                    logger.warning(f"Pattern match failed: {e}")
        
        # 使用通用转换
        try:
            result = self._generic_convert(original)
            self.conversion_log.append({
                "cypher": original,
                "ngql": result,
                "pattern": "generic",
                "success": True,
            })
            return result
        except Exception as e:
            self.conversion_log.append({
                "cypher": original,
                "ngql": None,
                "error": str(e),
                "success": False,
            })
            raise ConversionError(f"Could not convert Cypher query: {e}")
    
    def _generic_convert(self, cypher: str) -> str:
        """通用转换逻辑"""
        ngql = cypher
        
        # 1. 转换基本关键字（大小写不敏感）
        ngql = re.sub(r'\bMATCH\b', 'MATCH', ngql, flags=re.IGNORECASE)
        ngql = re.sub(r'\bRETURN\b', 'RETURN', ngql, flags=re.IGNORECASE)
        ngql = re.sub(r'\bWHERE\b', 'WHERE', ngql, flags=re.IGNORECASE)
        ngql = re.sub(r'\bORDER BY\b', 'ORDER BY', ngql, flags=re.IGNORECASE)
        ngql = re.sub(r'\bLIMIT\b', 'LIMIT', ngql, flags=re.IGNORECASE)
        ngql = re.sub(r'\bSKIP\b', 'OFFSET', ngql, flags=re.IGNORECASE)
        
        # 2. 转换节点标签语法 (n:Label -> (n:Label))
        # NebulaGraph 使用 TAG 而不是 Label，但语法类似
        ngql = re.sub(
            r'\((\w+):(\w+)\)',
            r'(\1:\2)',
            ngql
        )
        
        # 3. 转换关系类型语法 -[r:TYPE]->
        ngql = re.sub(
            r'-\[(\w*):(\w+)\]->',
            r'-[\1:\2]->',
            ngql
        )
        
        # 4. 转换属性访问 n.prop -> n.prop (相同)
        # 5. 转换比较操作 = -> ==
        ngql = re.sub(r'([^=])=([^=])', r'\1==\2', ngql)
        
        # 6. 转换函数调用
        ngql = re.sub(r'\bcount\(([^)]+)\)', r'count(\1)', ngql, flags=re.IGNORECASE)
        ngql = re.sub(r'\bid\((\w+)\)', r'id(\1)', ngql, flags=re.IGNORECASE)
        
        # 7. 转换 CREATE 到 INSERT（如果存在）
        if re.search(r'\bCREATE\b', ngql, re.IGNORECASE):
            ngql = self._convert_create(ngql)
        
        # 8. 转换 DELETE
        if re.search(r'\bDELETE\b', ngql, re.IGNORECASE):
            ngql = self._convert_delete(ngql)
        
        return ngql
    
    def _convert_create(self, cypher: str) -> str:
        """转换 CREATE 语句"""
        # 提取节点创建
        node_pattern = r'CREATE\s*\((\w+):(\w+)\s*(?:\{([^}]*)\})?\)'
        
        def convert_node_match(m):
            alias = m.group(1)
            label = m.group(2)
            props = m.group(3) or ""
            
            # 转换属性格式
            if props:
                # a: 1, b: "x" -> a="1", b="x"
                props = re.sub(r'(\w+):\s*"?([^",]+)"?', r'\1="\2"', props)
                return f'INSERT VERTEX {label} (id, {props}) VALUES "{alias}"'
            else:
                return f'INSERT VERTEX {label} () VALUES "{alias}"'
        
        return re.sub(node_pattern, convert_node_match, cypher, flags=re.IGNORECASE)
    
    def _convert_delete(self, cypher: str) -> str:
        """转换 DELETE 语句"""
        # 提取节点删除
        # MATCH (n) WHERE id(n) = "x" DELETE n -> DELETE VERTEX "x"
        
        if 'WHERE' in cypher.upper():
            # 提取 ID
            vid_match = re.search(r'id\((\w+)\)\s*=\s*["\']([^"\']+)["\']', cypher)
            if vid_match:
                vid = vid_match.group(2)
                return f'DELETE VERTEX "{vid}"'
        
        return cypher
    
    def can_convert(self, cypher: str) -> bool:
        """检查是否可以转换"""
        try:
            self.convert(cypher)
            return True
        except ConversionError:
            return False
    
    def get_conversion_log(self) -> list:
        """获取转换日志"""
        return self.conversion_log.copy()


def _convert_property_match(match) -> str:
    """转换带属性的匹配"""
    alias = match.group(1)
    label = match.group(2) or "entity"
    props_str = match.group(3)
    
    # 解析属性
    conditions = []
    for prop_match in re.finditer(r'(\w+):\s*"?([^",]+)"?', props_str):
        prop_name = prop_match.group(1)
        prop_value = prop_match.group(2)
        conditions.append(f'{alias}.{prop_name} == "{prop_value}"')
    
    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    
    return f'MATCH ({alias}:{label}) {where_clause} RETURN {alias}'


# ========== 常见查询转换函数 ==========

def convert_get_neighbors(node_id: str, edge_type: Optional[str] = None) -> str:
    """转换获取邻居查询"""
    if edge_type:
        return f'GO FROM "{node_id}" OVER {edge_type} YIELD dst(edge) AS neighbor'
    else:
        return f'GO FROM "{node_id}" OVER * YIELD dst(edge) AS neighbor'


def convert_shortest_path(source: str, target: str, max_depth: int = 5) -> str:
    """转换最短路径查询"""
    return f'FIND SHORTEST PATH FROM "{source}" TO "{target}" OVER * UPTO {max_depth} STEPS'


def convert_pagerank(top_k: int = 100) -> str:
    """转换 PageRank 查询"""
    return f'SUBMIT JOB PAGERANK; FETCH PROP ON pagerank YIELD vertex AS node, pagerank.pagerank AS score | LIMIT {top_k}'


# ========== 便捷函数 ==========

def convert(cypher: str) -> str:
    """快速转换函数"""
    converter = CypherToNGQL()
    return converter.convert(cypher)


def convert_safe(cypher: str, default: Optional[str] = None) -> Optional[str]:
    """安全转换，失败返回默认值"""
    try:
        return convert(cypher)
    except ConversionError:
        return default
