import unittest

from bridge.spec_mapper.mapper import map_aof_spec_to_cognee


class TestSpecMapper(unittest.TestCase):
    def test_map_defaults(self):
        got = map_aof_spec_to_cognee({})
        self.assertEqual(got["datasets"], None)
        self.assertFalse(got["run_in_background"])
        self.assertTrue(got["incremental_loading"])
        self.assertEqual(got["data_per_batch"], 20)

    def test_map_values(self):
        spec = {
            "dataset": "demo",
            "runtime": {
                "run_in_background": True,
                "incremental_loading": False,
                "data_per_batch": 7,
            },
        }
        got = map_aof_spec_to_cognee(spec)
        self.assertEqual(got["datasets"], "demo")
        self.assertTrue(got["run_in_background"])
        self.assertFalse(got["incremental_loading"])
        self.assertEqual(got["data_per_batch"], 7)


if __name__ == "__main__":
    unittest.main()
