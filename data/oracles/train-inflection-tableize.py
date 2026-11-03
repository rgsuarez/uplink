import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
import solution as s

class FrozenOracle(unittest.TestCase):
    def test_contract_0(self):
        self.assertEqual(s.tableize('FancyCategory'), 'fancy_categories')
    def test_contract_1(self):
        self.assertEqual(s.tableize('sheep'), 'sheep')
    def test_contract_2(self):
        self.assertEqual(s.ordinal(2), 'nd')

if __name__ == '__main__':
    unittest.main()
