"""Keep CI success counts honest when fixtures or individual subtests fail."""
import importlib.util
from io import StringIO
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location(
    "integration_suite_runner", Path(__file__).resolve().parents[1] / "scripts" / "run_integration_suite.py",
)
runner_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner_module)


class IntegrationSummaryTests(unittest.TestCase):
    @staticmethod
    def run_cases(*cases):
        suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(case) for case in cases)
        return unittest.TextTestRunner(stream=StringIO(), resultclass=runner_module.IntegrationResult).run(suite)

    def test_fixture_error_does_not_reduce_successful_method_count(self):
        class Passing(unittest.TestCase):
            def test_pass(self):
                pass

        class BrokenFixture(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise RuntimeError("Intentional fixture error to verify summary accounting")

            def test_never_runs(self):
                pass

        result = self.run_cases(Passing, BrokenFixture)
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(result.passed, 1)
        self.assertEqual(len(result.errors), 1)
        self.assertFalse(result.wasSuccessful())

    def test_multiple_failed_subtests_cannot_produce_negative_success_count(self):
        class BrokenSubtests(unittest.TestCase):
            def test_subtests(self):
                for value in (1, 2):
                    with self.subTest(value=value):
                        self.fail("Intentional subtest failure to verify summary accounting")

        result = self.run_cases(BrokenSubtests)
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(result.passed, 0)
        self.assertEqual(len(result.failures), 2)
        self.assertFalse(result.wasSuccessful())


if __name__ == "__main__":
    unittest.main()
