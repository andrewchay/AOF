# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
import unittest
from unittest.mock import AsyncMock, patch

from bridge.cognee_add_runner import run_add_from_spec


class TestAddDatasetBridge(unittest.IsolatedAsyncioTestCase):
    async def test_add_uses_dataset_name_from_spec(self):
        spec = {
            "dataset": "fresh_dataset",
            "runtime": {"retries": 0, "backoff_seconds": 1.0},
            "cognee": {},
        }

        mock_retry = AsyncMock(return_value="ok")
        with patch("bridge.cognee_add_runner._import_cognee", return_value=object()), patch(
            "bridge.cognee_add_runner._add_with_retry", new=mock_retry
        ):
            await run_add_from_spec(spec, data="x")

        kwargs = mock_retry.call_args.kwargs
        self.assertEqual(kwargs["add_kwargs"].get("dataset_name"), "fresh_dataset")
        self.assertNotIn("dataset_id", kwargs["add_kwargs"])

    async def test_add_prefers_dataset_id_over_name(self):
        spec = {
            "dataset": "ignored_name",
            "dataset_id": "123e4567-e89b-12d3-a456-426614174000",
            "runtime": {"retries": 0, "backoff_seconds": 1.0},
            "cognee": {},
        }

        mock_retry = AsyncMock(return_value="ok")
        with patch("bridge.cognee_add_runner._import_cognee", return_value=object()), patch(
            "bridge.cognee_add_runner._add_with_retry", new=mock_retry
        ):
            await run_add_from_spec(spec, data="x")

        kwargs = mock_retry.call_args.kwargs
        self.assertEqual(
            kwargs["add_kwargs"].get("dataset_id"),
            "123e4567-e89b-12d3-a456-426614174000",
        )
        self.assertNotIn("dataset_name", kwargs["add_kwargs"])


if __name__ == "__main__":
    unittest.main()
