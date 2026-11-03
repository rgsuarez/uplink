# Frozen independent behavior cases, corpus v2.
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
import solution as s

class Contract(unittest.TestCase):
    def test_case_000(self):
        self.assertEqual(s.midpoint(2,8), 5.0)
    def test_case_001(self):
        self.assertEqual(s.midpoint(-4,4), 0.0)
    def test_case_002(self):
        self.assertEqual(s.identity(7), 7)
    def test_case_003(self):
        self.assertEqual(s.identity(0), 0)

if __name__ == '__main__':
    unittest.main(verbosity=2)
