import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
import solution as s

class FrozenOracle(unittest.TestCase):
    def test_contract_0(self):
        self.assertEqual(s.pathcase('redGreen'), 'red/green')
    def test_contract_1(self):
        self.assertEqual(s.pathcase('red'), 'red')
    def test_contract_2(self):
        self.assertEqual(s.capitalcase('test'), 'Test')

if __name__ == '__main__':
    unittest.main()
