# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Bounded natural-language grounding against a verified release vocabulary."""

import math
import re
from collections import Counter

from .semantic_query import SemanticIntent, SemanticQueryCompileError


def mentions(query, label):
    label = str(label).casefold().strip()
    if not label:
        return False
    if re.fullmatch(r"[a-z0-9_-]+", label):
        return (
            re.search(
                r"(?<![a-z0-9_])" + re.escape(label) + r"(?![a-z0-9_])",
                query.casefold(),
            )
            is not None
        )
    return label in query.casefold()


def ground_intent(query, resources, purpose):
    """Resolve named metrics/dimensions; unsupported qualifiers require clarification."""
    selected = {"Metric": [], "Dimension": []}
    for resource in resources:
        if resource.get("kind") not in selected:
            continue
        labels = [resource["name"], *resource.get("spec", {}).get("aliases", [])]
        if any(mentions(query, label) for label in labels):
            selected[resource["kind"]].append(resource["resource_id"])
    if not selected["Metric"]:
        raise SemanticQueryCompileError(
            "clarification required: name a published metric"
        )
    # Never silently discard filters or requested calculations.
    if re.search(
        r"\d|去年|今年|上月|本月|昨天|今天|增长|同比|环比|大于|小于|where|last |this |growth|greater|less",
        query,
        re.I,
    ):
        raise SemanticQueryCompileError(
            "clarification required: use a typed intent for filters or derived calculations"
        )
    return SemanticIntent.create(
        metrics=selected["Metric"], dimensions=selected["Dimension"], purpose=purpose
    )


def tokens(text):
    parts = re.findall(r"[a-z0-9_-]+|[\u4e00-\u9fff]", text.casefold())
    return Counter(parts)


def rank_resources(query, resources, mode, kinds=()):
    """Sparse term-frequency cosine vectors, with explicit algorithm provenance."""
    q = tokens(query)
    hits = []
    for resource in resources:
        if kinds and resource.get("kind") not in kinds:
            continue
        text = " ".join(
            [
                resource["name"],
                resource.get("description", ""),
                *resource.get("spec", {}).get("aliases", []),
            ]
        )
        vector = tokens(text)
        overlap = sum(q[t] * vector[t] for t in q)
        norm = math.sqrt(
            sum(v * v for v in q.values()) * sum(v * v for v in vector.values())
        )
        score = overlap / norm if norm else 0
        if score > 0 or mentions(query, resource["name"]):
            hits.append(
                {
                    key: resource[key]
                    for key in (
                        "resource_id",
                        "revision_id",
                        "kind",
                        "name",
                        "description",
                        "evidence",
                    )
                    if key in resource
                }
                | {"score": round(score, 8)}
            )
    hits.sort(key=lambda hit: (-hit["score"], hit["resource_id"]))
    return hits
