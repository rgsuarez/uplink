import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
import solution as s

class FrozenOracle(unittest.TestCase):
    def test_contract_0(self):
        self.assertEqual(s.add(2, 3), 5)
    def test_contract_1(self):
        self.assertEqual(s.add(2, 0), 2)
    def test_contract_2(self):
        self.assertEqual(s.invariant(), 7)

if __name__ == '__main__':
    unittest.main()
