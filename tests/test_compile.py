# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Every module compiles; the other tests do not import the editor window.

Run: python -m unittest discover tests
"""

import glob
import os
import py_compile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class CompileTest(unittest.TestCase):
    def test_all_modules_compile(self):
        files = glob.glob(os.path.join(ROOT, "*.py")) + glob.glob(os.path.join(ROOT, "packaging", "*.py"))
        self.assertTrue(files)
        for f in files:
            with self.subTest(file=os.path.basename(f)):
                py_compile.compile(f, doraise=True)


if __name__ == "__main__":
    unittest.main()
