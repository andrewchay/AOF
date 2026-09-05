"""Published business-query contracts produce bounded dependency plans."""

from collections.abc import Mapping

from .agentic_system import AgenticSystemError
from .canonical import canonical_data, content_digest
from .query_plans import QueryRequest, TrustedSnapshotResolver


def published_plan(control, request, headers):
    principal = control.verifier.verify(headers)
    resolver = TrustedSnapshotResolver(
        control._compilation_repository(principal.tenant_id)
    )
    snapshot = resolver.plan(
        QueryRequest.create(
            channel=request.channel,
            capability="semantic_search",
            query=request.query,
            purpose=request.purpose,
            parameters={
                "retrieval_mode": "skill",
                "expected_release_id": request.release_id,
                "expected_release_digest": request.release_digest,
            },
        ),
        tenant_id=principal.tenant_id,
    )
    run = resolver.repository.get(snapshot.run_id)
    payload = resolver._artifact_payload(run, snapshot.artifacts[0])
    contract = next(
        (
            item
            for item in payload.get("resources", [])
            if item["resource_id"] == request.query_contract_id
            and item["kind"] == "QueryContract"
        ),
        None,
    )
    if contract is None:
        raise AgenticSystemError("published QueryContract not found")
    spec = contract["spec"]
    if request.query not in spec.get("accepted_queries", []):
        raise AgenticSystemError(
            "clarification required: question does not match the published business contract"
        )
    if request.purpose != spec.get("purpose"):
        raise AgenticSystemError("business contract purpose mismatch")
    steps = canonical_data(spec.get("agentic_steps", []))
    if not steps or len(steps) > request.max_steps:
        raise AgenticSystemError("published plan exceeds the requested step budget")
    seen = set()
    by_id = {item["resource_id"]: item for item in payload.get("resources", [])}
    for step in steps:
        if not isinstance(step, Mapping) or step.get("kind") != "capability":
            raise AgenticSystemError("invalid business plan step")
        step_id = step.get("step_id")
        if (
            not isinstance(step_id, str)
            or not step_id
            or ":" in step_id
            or step_id in seen
        ):
            raise AgenticSystemError("invalid or duplicate business step ID")
        if step.get("capability") not in request.allowed_capabilities:
            raise AgenticSystemError("business plan requires an unapproved capability")
        if not set(step.get("depends_on", [])).issubset(seen):
            raise AgenticSystemError(
                "business plan dependencies must precede their consumer"
            )
        if step["capability"] == "skill_execute":
            function = by_id.get(step.get("function_id"), {})
            if (
                function.get("kind") != "Function"
                or function["spec"].get("side_effects") is not False
            ):
                raise AgenticSystemError(
                    "analysis Skill must be a published read-only Function"
                )
            if function["spec"].get("operation") != "finance_variance":
                raise AgenticSystemError(
                    "no local executor for the published analysis function"
                )
            step["function_revision"] = function["revision_id"]
            step["release_digest"] = request.release_digest
        seen.add(step_id)
    return steps


def execute_finance_analysis(context):
    step = context["step"]
    source, catalog = step["data_from"], step["catalog_from"]
    if not {source, catalog}.issubset(step["depends_on"]):
        raise AgenticSystemError("Skill data and catalog must be explicit dependencies")
    previous = context["previous_results"]
    hits = previous[catalog]["output"]["data"]["hits"]
    if not any(
        hit.get("resource_id") == step["function_id"]
        and hit.get("revision_id") == step["function_revision"]
        for hit in hits
    ):
        raise AgenticSystemError(
            "analysis Skill was not returned by the authorized catalog query"
        )
    data = previous[source]["output"]["data"]
    if data.get("executed") is not True:
        raise AgenticSystemError("analysis Skill requires executed business data")
    import math

    rows = []
    for row in data["rows"]:
        values = [
            row[name] for name in ("revenue", "budget", "receipts", "outstanding")
        ]
        if not all(
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and math.isfinite(value)
            for value in values
        ):
            raise AgenticSystemError("finance Skill requires finite numeric measures")
        rows.append({**row, "variance": row["revenue"] - row["budget"]})
    summary = "\n".join(
        f"客户 {row['customer']}：收入 {row['revenue']:g}，预算 {row['budget']:g}，偏差 {row['variance']:g}；已回款 {row['receipts']:g}，未回款 {row['outstanding']:g}。"
        for row in rows
    )
    summary += "\n未回款须人工核对验收日期、合同条件及账务记录；本结果不认定违约，也未确定偏差原因。"
    return {
        "output": {
            "summary": summary,
            "data": {"rows": rows, "analysis": "finance_variance"},
        },
        "evidence": [
            {
                "id": step["function_id"],
                "content_hash": step["function_revision"],
                "release_digest": context["release_digest"],
                "source_output_digest": content_digest(previous[source]["output"]),
            }
        ],
        "receipt": previous[source]["receipt"],
    }


def rule_facts(step, previous_results):
    binding = step.get("facts_from")
    if not binding:
        return []
    source = binding["step_id"]
    if source not in step.get("depends_on", []):
        raise AgenticSystemError("rule fact source must be an explicit dependency")
    data = previous_results[source]["output"]["data"]
    if data.get("executed") is not True:
        raise AgenticSystemError("rule facts require executed SQL evidence")
    facts = []
    for row in data["rows"]:
        for rule in binding["bindings"]:
            if (
                rule.get("when_positive") is not None
                and row[rule["when_positive"]] <= 0
            ):
                continue
            facts.append(
                {
                    "predicate": rule["predicate"],
                    "terms": [str(row[field]) for field in rule["fields"]],
                }
            )
    return facts


def submit_review_action(control, context, headers):
    step = context["step"]
    if not context.get("query_contract_id"):
        raise AgenticSystemError("actions require a published business contract")
    source = step["objects_from"]
    if source not in step.get("depends_on", []):
        raise AgenticSystemError("action object source must be an explicit dependency")
    source_result = context["previous_results"][source]
    facts = source_result["output"]["data"]["derived_facts"]
    object_ids = sorted(
        {
            fact["terms"][0]
            for fact in facts
            if fact["predicate"] == step["object_predicate"]
        }
    )
    if not object_ids:
        raise AgenticSystemError("no evidence-supported objects require an action")
    payload = {
        "channel": context["channel"],
        "action_type_id": step["action_type_id"],
        "inputs": step["inputs"],
        "object_ids": object_ids,
        "purpose": context["purpose"],
        "idempotency_key": context["run_id"] + ":" + step["step_id"],
        "policy_resource_id": step["policy_resource_id"],
    }
    plan = control.plan(payload, headers=headers)
    if (
        plan["release_id"] != context["release_id"]
        or plan["release_digest"] != context["release_digest"]
    ):
        raise AgenticSystemError("action plan release does not match business review")
    if not plan["impact_report"]["approval_required"]:
        raise AgenticSystemError("Agentic actions require independent approval")
    run = control.submit(
        payload
        | {
            "expected_plan_digest": plan["plan_digest"],
            "rationale": "Review anomalies supported by query result "
            + source_result["result_digest"],
        },
        headers=headers,
    )
    return {
        "output": {
            "summary": "Action submitted for independent approval; no business write executed.",
            "action_run_id": run["run_id"],
            "status": run["status"],
            "plan": plan,
        },
        "receipt": {"decision_id": run["history"][-1]["decision_id"]},
        "evidence": [
            {
                "id": run["run_id"],
                "content_hash": run["run_digest"],
                "release_digest": context["release_digest"],
                "source_result_digest": source_result["result_digest"],
            }
        ],
    }
