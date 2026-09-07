# Notices for AOF (Agentic Ontology Factory)

Copyright (C) 2026 Andrewchay

This project is licensed under the Business Source License 1.1 (see [LICENSE](LICENSE)).
It is **not** an Open Source license; each version converts to Apache License, Version 2.0
four years after its first public distribution.

## Third-party dependencies

The project depends on third-party packages that remain under their own licenses.
Key runtime dependencies and their licenses (full lists: web/pnpm-lock.yaml,
requirements/locks/reference-local.lock):

| Component | License |
|---|---|
| fastapi, starlette, pydantic, uvicorn | BSD-3 / MIT |
| sqlalchemy | MIT |
| cryptography | Apache-2.0 / BSD-3 |
| rdflib | BSD-3 |
| vue, vue-router, pinia, element-plus, axios | MIT |
| vis-network, vis-data | Apache-2.0 / MIT |

Dependency licenses are verified at build time via the SBOM pipeline (W07.05).

## Trademarks

"Business Source License" is a trademark of MariaDB plc. This repository uses
the standard BSL 1.1 text pursuant to MariaDB's grant allowing its use with
the required Covenants of Licensor (see LICENSE).
