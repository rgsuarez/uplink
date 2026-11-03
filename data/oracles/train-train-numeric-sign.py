import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
import solution as s

class FrozenOracle(unittest.TestCase):
    def test_contract_0(self):
        self.assertEqual(s.sign(-4), -1)
    def test_contract_1(self):
        self.assertEqual(s.sign(0), 0)
    def test_contract_2(self):
        self.assertEqual(s.invariant(), 7)

if __name__ == '__main__':
    unittest.main()
