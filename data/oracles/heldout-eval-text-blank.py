import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
import solution as s

class FrozenOracle(unittest.TestCase):
    def test_contract_0(self):
        self.assertEqual(s.is_blank('  '), True)
    def test_contract_1(self):
        self.assertEqual(s.is_blank('x'), False)
    def test_contract_2(self):
        self.assertEqual(s.invariant(), 11)

if __name__ == '__main__':
    unittest.main()
