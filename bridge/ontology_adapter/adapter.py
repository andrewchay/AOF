# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Build Cognee ontology config from AOF settings.

Upstream target:
- /Users/chaihao/LLM/cognee/cognee/modules/ontology/rdf_xml/RDFLibOntologyResolver.py
- /Users/chaihao/LLM/cognee/cognee/modules/ontology/matching_strategies.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bridge.errors import OntologyConfigError


def build_cognee_ontology_config(
    ontology_file: str,
    resolver_cls,
    matching_strategy=None,
) -> dict:
    """Return Cognee Config shape for cognify(config=...)."""
    resolver = resolver_cls(ontology_file=ontology_file, matching_strategy=matching_strategy)
    return {"ontology_config": {"ontology_resolver": resolver}}


def apply_ontology(spec: dict[str, Any]) -> dict[str, Any] | None:
    """正式 AOF ontology 注入 API：从 spec 构建 cognee 的 ontology config。

    这是 AOF 官方、唯一的 ontology 注入触点，供三类入口复用（CLI/REST/MCP）。
    - spec.ontology.file 不存在 → 返回 None（无 ontology，cognee 用默认 RDF 解析）
    - file 存在 → 构建 ``{"ontology_config": {"ontology_resolver": resolver}}``
      （传入 cognify(config=...)，避免退回环境变量注入）

    Returns:
        可直接作为 ``cognify`` 的 ``config`` 的 dict；无 ontology 时返回 None。
    """
    ontology = spec.get("ontology") or {}
    ontology_file = ontology.get("file")
    if not ontology_file:
        return None
    if not Path(ontology_file).exists():
        raise OntologyConfigError(f"ontology.file 不存在: {ontology_file}")

    try:
        from cognee.modules.ontology.rdf_xml.RDFLibOntologyResolver import (  # type: ignore
            RDFLibOntologyResolver,
        )
        from cognee.modules.ontology.matching_strategies import FuzzyMatchingStrategy  # type: ignore

        cutoff = float(ontology.get("matching_cutoff", 0.8))
        strategy = FuzzyMatchingStrategy(cutoff=cutoff)
        return build_cognee_ontology_config(
            ontology_file=str(ontology_file),
            resolver_cls=RDFLibOntologyResolver,
            matching_strategy=strategy,
        )
    except Exception as e:  # pragma: no cover - runtime dependency boundary
        raise OntologyConfigError("构建 ontology config 失败") from e
