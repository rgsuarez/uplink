# Frozen independent behavior cases, corpus v2.
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
import solution as s

class Contract(unittest.TestCase):
    def test_case_000(self):
        self.assertEqual(s.active_names([{'name':'a','active':True},{'name':'b','active':False}]), ['a'])
    def test_case_001(self):
        self.assertEqual(s.active_names([]), [])
    def test_case_002(self):
        self.assertEqual(s.identity({'x':1}), {'x':1})
    def test_case_003(self):
        self.assertEqual(s.identity({}), {})

if __name__ == '__main__':
    unittest.main(verbosity=2)
