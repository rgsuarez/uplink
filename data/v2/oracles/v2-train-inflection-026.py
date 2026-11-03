# Frozen independent behavior cases, corpus v2.
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
import solution as s

class Contract(unittest.TestCase):
    def test_case_000(self):
        self.assertEqual(s.parameterize('Donald E. Knuth'), 'donald-e-knuth')
    def test_case_001(self):
        self.assertEqual(s.parameterize('red green', '_'), 'red_green')
    def test_case_002(self):
        self.assertEqual(s.parameterize(''), '')
    def test_case_003(self):
        self.assertEqual(s.dasherize('plain'), 'plain')
    def test_case_004(self):
        self.assertEqual(s.ordinal(4), 'th')

if __name__ == '__main__':
    unittest.main(verbosity=2)
