# Frozen independent behavior cases, corpus v2.
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
import solution as s

class Contract(unittest.TestCase):
    def test_case_000(self):
        self.assertEqual(s.initials('red green'), 'RG')
    def test_case_001(self):
        self.assertEqual(s.initials(''), '')
    def test_case_002(self):
        self.assertEqual(s.identity('ok'), 'ok')
    def test_case_003(self):
        self.assertEqual(s.identity(''), '')
    def test_required_fixture(self):
        path = Path(__file__).with_name('required-v2-07.bin')
        self.assertEqual(path.read_bytes(), b'frozen-dependency-07')

if __name__ == '__main__':
    unittest.main(verbosity=2)
