# OptiMed Protocol Semantic IR in AOF

This is a deliberately thin AOF use case. The clinical contract, vocabulary,
evidence interpretation, review decisions, and downstream projections are owned
by OptiMed.

AOF demonstrates only reusable methodology:

- register an externally governed semantic contract as an immutable resource;
- preserve the OptiMed revision and evidence envelope;
- attach tenant/security metadata;
- keep the resource non-queryable until an independent AOF release is approved;
- support generic release, impact-analysis, and replay workflows.

The adapter does not import OptiMed and does not contain clinical mapping rules.
It accepts the JSON produced by OptiMed's
`scripts/export_protocol_semantic_ir.py` and wraps it as a
`ContextAssertion` resource.

```python
import json
from pathlib import Path

from examples.optimed_protocol_semantic import build_context_resource

payload = json.loads(Path("protocol_semantic_ir.json").read_text())
resource = build_context_resource(payload, tenant_id="acme", owner="clinical-data")
print(resource.revision_id)
```

Publishing that resource is a separate AOF governance action. Importing an
OptiMed bundle is not equivalent to approving it for organizational queries.
