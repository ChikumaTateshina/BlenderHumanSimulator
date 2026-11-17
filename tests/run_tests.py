"""Run all unit tests inside Blender.

Usage:
    blender -b --factory-startup --python tests/run_tests.py
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")
for p in (ROOT, TESTS):
    if p not in sys.path:
        sys.path.insert(0, p)


def main():
    pattern = os.environ.get("WT_TEST_PATTERN", "test_*.py")
    suite = unittest.defaultTestLoader.discover(TESTS, pattern=pattern, top_level_dir=TESTS)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


main()
