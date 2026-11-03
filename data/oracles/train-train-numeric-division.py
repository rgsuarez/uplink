import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
import solution as s

class FrozenOracle(unittest.TestCase):
    def test_contract_0(self):
        self.assertEqual(s.safe_div(8, 2), 4)
    def test_contract_1(self):
        self.assertEqual(s.safe_div(8, 0), None)
    def test_contract_2(self):
        self.assertEqual(s.invariant(), 7)

if __name__ == '__main__':
    unittest.main()
