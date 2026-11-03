# Frozen independent behavior cases, corpus v2.
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
import solution as s

class Contract(unittest.TestCase):
    def test_case_000(self):
        self.assertEqual(s.Bits('101') == s.Bits('101'), True)
    def test_case_001(self):
        self.assertEqual(s.Bits('101') == s.Bits('100'), False)
    def test_case_002(self):
        self.assertEqual(s.Bits('1') == s.Bits('01'), False)
    def test_case_003(self):
        self.assertEqual(s.clamp(1,0,5), 1)
    def test_case_004(self):
        self.assertEqual(s.ceil(4,[2,4,8]), 4)
    def test_required_fixture(self):
        path = Path(__file__).with_name('required-v2-01.bin')
        self.assertEqual(path.read_bytes(), b'frozen-dependency-01')

if __name__ == '__main__':
    unittest.main(verbosity=2)
