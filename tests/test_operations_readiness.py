"""Exhaustive truth table, independent of any route or economic implementation."""
from itertools import product
import unittest

from operations.errors import ContractError
from operations.readiness import CHECK_NAMES, compose


class ReadinessTruthTableTests(unittest.TestCase):
    def test_all_729_combinations_preserve_unknowns_and_never_authorize(self):
        count = 0
        for states in product(("pass","fail","unknown"),repeat=6):
            inputs = {name:{"state":state,"reason_codes":[] if state == "pass" else [name+":"+state]} for name,state in zip(CHECK_NAMES,states)}
            result = compose(inputs)
            expected = "blocked" if "fail" in states else "unresolved" if "unknown" in states else "ready_for_human_review"
            self.assertEqual(result["status"],expected)
            self.assertIs(result["purchase_authorized"],False)
            for name,state in zip(CHECK_NAMES,states):
                self.assertEqual(result["checks"][name]["state"],state)
                if state == "unknown":
                    self.assertEqual(result["checks"][name]["reasons"][0]["code"],name+":unknown")
            count += 1
        self.assertEqual(count,729)

    def test_missing_extra_or_malformed_checks_are_not_truthy_passes(self):
        valid = {name:{"state":"pass"} for name in CHECK_NAMES}
        for value in ({},dict(valid,extra={"state":"pass"}),dict(valid,rights=True),dict(valid,rights={"state":True}),dict(valid,rights={"state":"pass","reason_codes":"hidden"})):
            with self.assertRaises(ContractError):
                compose(value)


if __name__ == "__main__":
    unittest.main()
