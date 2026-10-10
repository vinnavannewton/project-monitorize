#!/usr/bin/env python3
"""Run discovered Python tests with optional, visible Flatpak exclusions."""

import argparse
import unittest


def cases(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from cases(test)
        else:
            yield test


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-flatpak", action="store_true", help="Temporarily skip Flatpak tests in CI")
    args = parser.parse_args()
    loader = unittest.TestLoader()
    suite = loader.discover("linux/tests", top_level_dir=".")
    if args.skip_flatpak:
        for test in cases(suite):
            if "flatpak" in test.id().lower():
                name = test.id().rsplit(".", 1)[1]
                method = getattr(test, name)
                setattr(test, name, unittest.skip("Flatpak checks temporarily disabled in CI")(method))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
