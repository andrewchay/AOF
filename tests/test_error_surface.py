import unittest
from unittest.mock import AsyncMock, patch

from bridge.cognee_add_runner import run_add_from_spec
from bridge.cognee_runner import run_cognify_from_spec
from bridge.errors import AddExecutionError, CognifyExecutionError


class TestErrorSurface(unittest.IsolatedAsyncioTestCase):
    async def test_add_includes_root_cause(self):
        spec = {"runtime": {"retries": 0, "backoff_seconds": 1.0}, "cognee": {}}

        with patch("bridge.cognee_add_runner._import_cognee", return_value=object()), patch(
            "bridge.cognee_add_runner._add_with_retry",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            with self.assertRaises(AddExecutionError) as ctx:
                await run_add_from_spec(spec, data="x")

        msg = str(ctx.exception)
        self.assertIn("root_cause=RuntimeError: boom", msg)

    async def test_cognify_includes_root_cause(self):
        spec = {
            "runtime": {"retries": 0, "backoff_seconds": 1.0},
            "cognee": {},
            "ontology": {},
        }

        with patch("bridge.cognee_runner._import_cognee", return_value=object()), patch(
            "bridge.cognee_runner.map_aof_spec_to_cognee", return_value={}
        ), patch("bridge.cognee_runner._build_ontology_config", return_value=None), patch(
            "bridge.cognee_runner._cognify_with_retry",
            new=AsyncMock(side_effect=ValueError("bad args")),
        ):
            with self.assertRaises(CognifyExecutionError) as ctx:
                await run_cognify_from_spec(spec)

        msg = str(ctx.exception)
        self.assertIn("root_cause=ValueError: bad args", msg)


if __name__ == "__main__":
    unittest.main()
