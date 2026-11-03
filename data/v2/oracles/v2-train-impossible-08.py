# Frozen independent behavior cases, corpus v2.
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
import solution as s

class Contract(unittest.TestCase):
    def test_case_000(self):
        self.assertEqual(s.tableize('FancyCategory'), 'fancy_categories')
    def test_case_001(self):
        self.assertEqual(s.tableize('RawScaledScorer'), 'raw_scaled_scorers')
    def test_case_002(self):
        self.assertEqual(s.tableize('sheep'), 'sheep')
    def test_case_003(self):
        self.assertEqual(s.dasherize('plain'), 'plain')
    def test_case_004(self):
        self.assertEqual(s.ordinal(4), 'th')
    def test_required_fixture(self):
        path = Path(__file__).with_name('required-v2-08.bin')
        self.assertEqual(path.read_bytes(), b'frozen-dependency-08')

if __name__ == '__main__':
    unittest.main(verbosity=2)
