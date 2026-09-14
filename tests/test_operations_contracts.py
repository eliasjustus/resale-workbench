"""Frozen synthetic adoption fixtures; no source/provider calls or real policy."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from operations.clock import parse_timestamp
from operations.config import policy_prerequisites, policy_snapshot, read_policy
from operations.contracts import RECORD_TYPES, read_record, record_digest, resolve_references, schema_snapshot
from operations.errors import ContractError, IntegrityError, UnresolvedError
from operations.serialization import canonical_bytes, digest, load_json

FIXTURES = Path(__file__).parent / "fixtures"
RECORDS = json.loads((FIXTURES / "operations-records.json").read_text(encoding="utf-8"))


class OperationsContractTests(unittest.TestCase):
    def test_documentation_protocol_reference_does_not_accept_inspection_protocol(self):
        references = {name:dict(RECORDS[kind],record_id=name) for name,kind in (("actor","actor_authority"),("protocol","documentation_protocol"),("offer","offer_item"),("cohort","cohort_definition"))}
        assignment = dict(RECORDS["documentation_assignment"],producer_ref="actor",protocol_id="protocol",offer_id="offer",cohort_id="cohort")
        self.assertEqual(set(resolve_references(assignment,references.get,as_of="2026-01-01T12:30:00Z")),set(references))
        references["protocol"] = dict(RECORDS["inspection_protocol"],record_id="protocol")
        with self.assertRaises(IntegrityError):
            resolve_references(assignment,references.get,as_of="2026-01-01T12:30:00Z")

    def test_all_original_and_supporting_readers_have_frozen_examples(self):
        original = json.loads((FIXTURES / "operations-draft.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(len(original["$defs"]), 8)
        self.assertEqual(len(RECORD_TYPES), 43)
        self.assertEqual(set(RECORDS), RECORD_TYPES)
        for name, fixture in RECORDS.items():
            with self.subTest(name=name):
                self.assertEqual(read_record(fixture, expected_type=name), fixture)
                self.assertEqual(read_record(json.dumps(fixture)), fixture)
                for field in ("record_id", "producer_ref", "synthetic"):
                    bad = deepcopy(fixture)
                    del bad[field]
                    with self.assertRaises(ContractError):
                        read_record(bad)
                bad = dict(fixture, injected="ignored?")
                with self.assertRaises(ContractError):
                    read_record(bad)
                with self.assertRaises(ContractError):
                    read_record(dict(fixture, schema_version=True))

    def test_money_and_json_are_strict_and_unknowns_stay_unknown(self):
        for value in (True, 1.0, 0.1, float("nan"), float("inf"), "100", -1):
            with self.subTest(value=value), self.assertRaises(ContractError):
                read_record(dict(RECORDS["evidence_event"], item_price_cents=value))
        signed = dict(RECORDS["outcome_event"], cash_delta_cents=-2300)
        self.assertEqual(read_record(signed)["cash_delta_cents"], -2300)
        self.assertIsNone(read_record(RECORDS["operating_review"])["acquisition_cents"])
        for malformed in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":1.0}', '{"x":Infinity}'):
            with self.assertRaises(ContractError):
                load_json(malformed)
        for kind in ("economic_review", "", None, []):
            with self.assertRaises(ContractError):
                read_record(dict(RECORDS["material_claim"], record_type=kind))

    def test_clock_formats_and_impossible_ordering(self):
        for stamp in ("2026-02-30T12:00:00Z", "2026-01-01T12:00:00", "2026-01-01 12:00:00Z", "2026-01-01T12:00:00-00:00", "2026-01-01T12:00:00+00:70", "2026-01-01T12:00:60Z"):
            with self.subTest(stamp=stamp), self.assertRaises(ContractError):
                read_record(dict(RECORDS["material_claim"], created_at=stamp))
        self.assertEqual(parse_timestamp("2026-01-01T13:00:00+01:00"), parse_timestamp("2026-01-01T12:00:00Z"))
        with self.assertRaises(ContractError):
            read_record(dict(RECORDS["evidence_event"], observed_at="2026-01-02T12:00:00Z"))
        with self.assertRaises(ContractError):
            read_record(dict(RECORDS["evidence_event"], event_at="2026-01-01T12:01:00Z"))
        with self.assertRaises(ContractError):
            read_record(dict(RECORDS["source_rule"], valid_until="2026-01-01T12:00:00Z"))
        with self.assertRaises(ContractError):
            read_record(dict(RECORDS["reservation"], expires_at=None))
        self.assertIsNone(read_record(dict(RECORDS["reservation"], state="committed", expires_at=None))["expires_at"])

    def test_meaning_is_not_coerced_to_a_positive_result(self):
        for stamp in ("0001-01-01T00:00:00+23:59", "9999-12-31T23:59:59-23:59"):
            with self.assertRaises(ContractError):
                parse_timestamp(stamp)
        review = dict(RECORDS["operating_review"], synthetic=False, scenario_status="complete", item_receipt_cents=10000, shipping_collected_cents=0, acquisition_cents=5000)
        review["cost_lines"] = [{"exposure_id":"cost", "category":"repair", "amount_cents":100, "treatment":"cash", "basis_kind":"synthetic", "basis_claim_ids":[], "incurred_at":None, "effective_until":None, "allocation_rule_id":"one-case", "cost_policy_id":None,"underlying_exposure_ids":["cost"]}]
        with self.assertRaises(ContractError):
            read_record(review)
        with self.assertRaises(ContractError):
            read_record(dict(RECORDS["evidence_event"], kind="asking_context", transaction_state="paid"))
        with self.assertRaises(ContractError):
            read_record(dict(RECORDS["material_claim"], status="established"))
        with self.assertRaises(ContractError):
            read_record(dict(RECORDS["operating_review"], scenario_status="complete"))
        readiness = deepcopy(RECORDS["readiness_snapshot"])
        readiness["checks"]["rights"]["state"] = "unknown"
        with self.assertRaises(ContractError):
            read_record(readiness)
        readiness["status"] = "unresolved"
        read_record(readiness)
        readiness["checks"]["route"]["state"] = "fail"
        readiness["status"] = "blocked"
        self.assertEqual(read_record(readiness)["checks"]["rights"]["state"], "unknown")
        with self.assertRaises(ContractError):
            read_record(dict(readiness, purchase_authorized=True))
        with self.assertRaises(ContractError):
            read_record(dict(RECORDS["human_decision"], execution_receipt_ref="late-mutation"))

    def test_reference_type_time_and_synthetic_boundary(self):
        claim = dict(RECORDS["material_claim"], reviewer_ref="authority", producer_ref="authority")
        authority = dict(RECORDS["actor_authority"], record_id="authority")
        records = {"authority": authority}
        now = "2026-01-01T12:30:00Z"
        self.assertEqual(set(resolve_references(claim, records.get, as_of=now)), {"authority"})
        judgment = dict(RECORDS["initial_judgment"], actor_ref="authority", producer_ref="authority", attempt_id="absent")
        with self.assertRaises(UnresolvedError):
            resolve_references(judgment, records.get, as_of=now)
        with self.assertRaises(UnresolvedError):
            resolve_references(claim, {}.get, as_of=now)
        for target in (dict(authority, synthetic=False), dict(RECORDS["cohort_definition"], record_id="authority")):
            with self.assertRaises(IntegrityError):
                resolve_references(claim, {"authority": target}.get, as_of=now)
        for now in ("2025-12-31T12:00:00Z", "2026-01-02T12:00:00Z"):
            with self.assertRaises(UnresolvedError):
                resolve_references(claim, records.get, as_of=now)

    def test_policy_is_explicit_no_example_inheritance_or_upstream_mutation(self):
        self.assertIsNone(read_policy(None))
        self.assertEqual(policy_prerequisites(None, as_of="2026-01-01T12:00:00Z"), ["operating_policy_missing"])
        policy = deepcopy(RECORDS["operating_policy"])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "operations.toml"
            upstream = Path(temp) / "resale.toml"
            upstream.write_bytes(b"untouched upstream run policy\r\n")
            # JSON strings/arrays are also valid TOML for these simple fields.
            lines = ["schema_version = 1", "[policy]"]
            lines += [f"{key} = {json.dumps(value)}" for key, value in policy.items() if value is not None]
            path.write_text("\n".join(lines), encoding="utf-8")
            before = {p.name:p.read_bytes() for p in Path(temp).iterdir()}
            parsed = read_policy(path)
            self.assertEqual(parsed, policy)
            self.assertEqual({p.name:p.read_bytes() for p in Path(temp).iterdir()}, before)
            for key in ("cash_floor_cents", "labor_cents_per_hour", "source_authority_refs", "deadline_policy"):
                self.assertIn(key + "_unresolved", policy_prerequisites(parsed, as_of="2026-01-01T12:00:00Z", synthetic=True))
            with self.assertRaises(ContractError):
                read_policy(upstream)
            with self.assertRaises(ContractError):
                read_policy(Path(temp) / "missing.toml")
            path.write_text("\n".join(lines)+"\nunknown = 1\n", encoding="utf-8")
            with self.assertRaises(ContractError):
                read_policy(path)

    def test_snapshots_are_independent_and_domain_separated(self):
        policy = deepcopy(RECORDS["operating_policy"])
        frozen = policy_snapshot(policy)
        policy["cash_floor_cents"] = 100
        self.assertIsNone(frozen["policy"]["cash_floor_cents"])
        self.assertNotEqual(policy_snapshot(policy)["sha256"], frozen["sha256"])
        self.assertNotEqual(digest(policy), digest(policy, domain="policy"))
        self.assertEqual(canonical_bytes({"b":2,"a":"ä"}), b'{"a":"\xc3\xa4","b":2}')
        self.assertEqual(record_digest(policy), record_digest(dict(reversed(list(policy.items())))))
        copy = schema_snapshot()
        copy.clear()
        self.assertIn("$defs", schema_snapshot())


if __name__ == "__main__":
    unittest.main()
