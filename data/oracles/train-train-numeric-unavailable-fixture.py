import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
import solution as s

class FrozenOracle(unittest.TestCase):
    def test_contract_0(self):
        self.assertEqual(s.add(1,2), 3)
    def test_contract_1(self):
        self.assertEqual(s.invariant(), 7)
    def test_required_immutable_fixture(self):
        payload = Path(__file__).with_name('required-fixture.bin').read_bytes()
        self.assertEqual(payload, b'fixed-contract-v1')

if __name__ == '__main__':
    unittest.main()
