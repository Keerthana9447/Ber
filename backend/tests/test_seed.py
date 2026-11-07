import unittest

from app.seed import seed_reference_data


class SeedModuleTests(unittest.TestCase):
    def test_seed_entrypoint_imports(self):
        self.assertTrue(callable(seed_reference_data))


if __name__ == "__main__":
    unittest.main()
