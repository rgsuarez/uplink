# Frozen independent behavior cases, corpus v2.
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
import solution as s

class Contract(unittest.TestCase):
    def test_case_000(self):
        self.assertEqual(s.Bits('101')[0], True)
    def test_case_001(self):
        self.assertEqual(s.Bits('101')[1], False)
    def test_case_002(self):
        self.assertEqual(s.Bits('101')[-1], True)
    def test_case_003(self):
        self.assertEqual(s.Bits('101')[1:].as_bin(), '01')
    def test_case_004(self):
        self.assertEqual(s.clamp(1,0,5), 1)
    def test_case_005(self):
        self.assertEqual(s.ceil(4,[2,4,8]), 4)

if __name__ == '__main__':
    unittest.main(verbosity=2)
