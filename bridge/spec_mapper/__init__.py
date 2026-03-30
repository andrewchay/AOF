"""AOF spec_mapper bridge.

Thin adapter only: map AOF run spec to Cognee call args.
No extraction logic is implemented here.
"""

from .mapper import map_aof_spec_to_cognee

__all__ = ["map_aof_spec_to_cognee"]
