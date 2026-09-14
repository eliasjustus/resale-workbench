"""Invented labels and scoped reviewers exercise tooling, never real accuracy."""
from copy import deepcopy
import unittest

from operations import benchmark
from operations.contracts import record_digest
from operations.errors import IntegrityError
from test_operations_capacity import RECORDS, verifier


class BenchmarkFixture:
    def __init__(self,*,synthetic=True,sampling_kind="synthetic_controls"):
        self.synthetic = synthetic
        self.records,self.claims,self.cases = {},{},[]
        self.source = self.make("source_rule","source",scope="benchmark",purpose="benchmark-retention",retention="allowed",basis_refs=["source-basis"])
        self.put(self.source)
        self.bind(self.source,"source-basis",kind="legal_applicability")
        self.actor = self.make("actor_authority","reviewer",identity_ref="independent-human",scopes=["adjudicate:benchmark"],grant_claim_ids=["reviewer-grant"])
        self.put(self.actor)
        self.bind(self.actor,"reviewer-grant")
        self.protocol = self.make("benchmark_protocol","protocol",created_at="2026-01-01T13:00:00Z",frozen_at="2026-01-01T13:00:00Z",scope="benchmark",source_purpose="benchmark-retention",method_identity_ref="assessed-method",method_version="method-1",protocol_version="protocol-1",label_scope="current evidence supports this case",sampling_kind=sampling_kind,basis_claim_ids=["protocol-basis"])

    def make(self,kind,identity,**updates):
        return dict(deepcopy(RECORDS[kind]),record_id=identity,synthetic=self.synthetic,**updates)

    def put(self,record):
        self.records[record["record_id"]] = record
        return record

    def bind(self,record,identity,kind="identity"):
        claim = self.make("material_claim",identity,created_at=record["created_at"],claim_type=kind,subject={"record_id":record["record_id"],"record_type":record["record_type"],"record_sha256":record_digest(record),"verified_fields":list(record)})
        self.put(claim)
        self.claims[identity] = claim

    def case(self,identity,label="unsupported",partition="held_out",**event_changes):
        event = self.make("evidence_event","event-"+identity,event_id="transaction-"+identity,source_rule_id="source",lineage_status="established",**event_changes)
        self.put(event)
        adjudication = None
        if label is not None:
            adjudication = self.make("benchmark_label","label-"+identity,case_id=identity,scope="benchmark",label_scope=self.protocol["label_scope"],label=label,severity="critical",reviewer_ref="reviewer",evidence_event_ids=[event["record_id"]],disagreement=False,basis_claim_ids=["label-basis-"+identity])
            self.put(adjudication)
            self.bind(adjudication,"label-basis-"+identity)
        case = {"case_id":identity,"partition":partition,"evidence_refs":[{"record_id":event["record_id"],"record_sha256":record_digest(event)}],"reviewer_ref":"reviewer","label_id":adjudication["record_id"] if adjudication else None,"label_sha256":record_digest(adjudication) if adjudication else None,"exposed_to_method":False}
        self.cases.append(case)
        return case

    def freeze(self):
        self.protocol["cases"] = deepcopy(self.cases)
        self.bind(self.protocol,"protocol-basis")
        return benchmark.freeze(self.protocol,list(self.records.values()),verify_claim=verifier(self.claims))

    def prediction(self,frozen,case_id,decision):
        record = self.make("benchmark_prediction","prediction-"+case_id,case_id=case_id,created_at="2026-01-01T14:00:00Z",benchmark_version_sha256=frozen["benchmark_version_sha256"],method_identity_ref=self.protocol["method_identity_ref"],method_version=self.protocol["method_version"],prediction=decision,basis_claim_ids=["prediction-basis-"+case_id])
        self.bind(record,record["basis_claim_ids"][0])
        return record

    def score(self,frozen,predictions):
        return benchmark.score(self.protocol,frozen,list(self.records.values()),predictions,verify_claim=verifier(self.claims))


class BenchmarkTests(unittest.TestCase):
    def test_missing_adjudication_and_zero_support_are_explicit(self):
        fixture = BenchmarkFixture()
        fixture.case("wrong")
        fixture.case("unlabelled",label=None)
        fixture.case("right",label="supported")
        frozen = fixture.freeze()
        predictions = [fixture.prediction(frozen,identity,"supported") for identity in ("wrong","unlabelled","right")]
        report = fixture.score(frozen,predictions)
        self.assertEqual(report["false_support_rate"],{"numerator":1,"denominator":2})
        self.assertEqual(report["missing_adjudication_rate"],{"numerator":1,"denominator":3})
        self.assertEqual(report["counts"]["supported_predictions"],3)
        self.assertEqual(report["severity"]["critical"]["false_support"],1)
        abstentions = [fixture.prediction(frozen,identity,"unresolved") for identity in ("wrong","unlabelled","right")]
        report = fixture.score(frozen,abstentions)
        self.assertIsNone(report["false_support_rate"])
        self.assertEqual(report["support_coverage"],{"numerator":0,"denominator":3})

    def test_duplicate_and_related_groups_never_cross_blind_partition(self):
        for related in (False,True):
            with self.subTest(related=related):
                fixture = BenchmarkFixture()
                fixture.case("train",partition="development")
                case = fixture.case("test")
                event = fixture.records["event-test"]
                event.update(related_event_ids=["event-train"] if related else [],event_id="transaction-test" if related else "transaction-train")
                case["evidence_refs"][0]["record_sha256"] = record_digest(event)
                frozen = fixture.freeze()
                report = fixture.score(frozen,[fixture.prediction(frozen,"test","supported")])
                self.assertEqual(report["counts"]["excluded_cases"],1)
                self.assertIn("leakage_group_crosses_partitions",report["audit"]["test"]["exclusion_reasons"])
                self.assertIsNone(report["false_support_rate"])

    def test_future_evidence_and_contamination_remain_audited(self):
        fixture = BenchmarkFixture()
        fixture.case("future",created_at="2026-01-01T12:30:00Z",observed_at="2026-01-01T12:30:00Z")
        fixture.case("exposed")["exposed_to_method"] = True
        frozen = fixture.freeze()
        report = fixture.score(frozen,[])
        self.assertEqual(report["counts"]["excluded_cases"],2)
        self.assertEqual(len(report["audit"]),2)
        self.assertIn("case_evidence_missing_or_future:event-future",report["audit"]["future"]["exclusion_reasons"])

    def test_challenge_sample_has_no_natural_prevalence_estimate(self):
        fixture = BenchmarkFixture(synthetic=False,sampling_kind="challenge")
        fixture.case("fictional-real-mode")
        frozen = fixture.freeze()
        report = fixture.score(frozen,[fixture.prediction(frozen,"fictional-real-mode","supported")])
        self.assertEqual(report["false_support_rate"],{"numerator":1,"denominator":1})
        self.assertIsNone(report["natural_prevalence_estimate"])
        self.assertEqual(report["population_inference_status"],"sampling_kind_does_not_estimate_natural_prevalence")

    def test_assessed_method_cannot_supply_its_own_ground_truth(self):
        fixture = BenchmarkFixture()
        fixture.case("self-labelled")
        fixture.actor["identity_ref"] = fixture.protocol["method_identity_ref"]
        fixture.bind(fixture.actor,"reviewer-grant")
        frozen = fixture.freeze()
        report = fixture.score(frozen,[fixture.prediction(frozen,"self-labelled","supported")])
        self.assertEqual(report["counts"]["missing_adjudications"],1)
        self.assertIsNone(report["false_support_rate"])
        self.assertIn("reviewer_not_independent:reviewer",report["audit"]["self-labelled"]["adjudication"]["reason_codes"])

    def test_label_method_and_split_changes_invalidate_frozen_comparison(self):
        for change in ("label","method","split"):
            with self.subTest(change=change):
                fixture = BenchmarkFixture()
                fixture.case("case")
                frozen = fixture.freeze()
                prediction = fixture.prediction(frozen,"case","supported")
                if change == "label":
                    label = fixture.records["label-case"]
                    label["label"] = "supported"
                    fixture.bind(label,"label-basis-case")
                    fixture.cases[0]["label_sha256"] = record_digest(label)
                elif change == "method":
                    fixture.protocol["method_version"] = "method-2"
                else:
                    fixture.cases[0]["partition"] = "development"
                updated = fixture.freeze()
                self.assertNotEqual(frozen["benchmark_version_sha256"],updated["benchmark_version_sha256"])
                with self.assertRaises(IntegrityError):
                    fixture.score(frozen,[prediction])

    def test_unverified_rights_and_disagreement_are_not_scored_correct(self):
        fixture = BenchmarkFixture()
        case = fixture.case("case")
        label = fixture.records["label-case"]
        label["disagreement"] = True
        fixture.bind(label,"label-basis-case")
        case["label_sha256"] = record_digest(label)
        frozen = fixture.freeze()
        report = fixture.score(frozen,[fixture.prediction(frozen,"case","supported")])
        self.assertEqual(report["counts"]["missing_adjudications"],1)
        self.assertIsNone(report["false_support_rate"])
        fixture.source["retention"] = "denied"
        fixture.bind(fixture.source,"source-basis",kind="legal_applicability")
        frozen = fixture.freeze()
        report = fixture.score(frozen,[])
        self.assertEqual(report["counts"]["excluded_cases"],1)

    def test_later_label_correction_withdraws_old_comparison_without_editing_label(self):
        fixture = BenchmarkFixture()
        fixture.case("case")
        original = deepcopy(fixture.records["label-case"])
        frozen = fixture.freeze()
        prediction = fixture.prediction(frozen,"case","supported")
        correction = dict(original,record_id="corrected-label",created_at="2026-01-01T15:00:00Z",label="supported",supersedes_label_id="label-case",basis_claim_ids=["correction-basis"])
        fixture.put(correction)
        fixture.bind(correction,"correction-basis")
        with self.assertRaises(IntegrityError):
            fixture.score(frozen,[prediction])
        self.assertEqual(fixture.records["label-case"],original)

    def test_exposure_propagates_to_copies_even_within_heldout_partition(self):
        fixture = BenchmarkFixture()
        fixture.case("exposed")["exposed_to_method"] = True
        case = fixture.case("copy")
        event = fixture.records["event-copy"]
        event["event_id"] = "transaction-exposed"
        case["evidence_refs"][0]["record_sha256"] = record_digest(event)
        frozen = fixture.freeze()
        report = fixture.score(frozen,[fixture.prediction(frozen,"copy","supported")])
        self.assertEqual(report["counts"]["excluded_cases"],2)
        self.assertIsNone(report["false_support_rate"])

    def test_later_source_denial_invalidates_frozen_rights(self):
        fixture = BenchmarkFixture()
        fixture.case("case")
        frozen = fixture.freeze()
        prediction = fixture.prediction(frozen,"case","supported")
        denial = dict(fixture.source,record_id="source-denied",created_at="2026-01-01T12:45:00Z",checked_at="2026-01-01T12:45:00Z",retention="denied",basis_refs=["denial-basis"])
        fixture.put(denial)
        fixture.bind(denial,"denial-basis",kind="legal_applicability")
        with self.assertRaises(IntegrityError):
            fixture.score(frozen,[prediction])
        updated = fixture.freeze()
        report = fixture.score(updated,[])
        self.assertEqual(report["counts"]["excluded_cases"],1)

    def test_postfreeze_lineage_discovery_withdraws_blind_comparison(self):
        fixture = BenchmarkFixture()
        fixture.case("train",partition="development")
        fixture.case("test")
        frozen = fixture.freeze()
        prediction = fixture.prediction(frozen,"test","supported")
        edge = fixture.make("lineage_edge","discovered-link",created_at="2026-01-01T13:30:00Z",reviewed_at="2026-01-01T13:30:00Z",left_evidence_id="event-train",right_evidence_id="event-test",relation="same_physical_item",basis_claim_ids=["link-basis"])
        fixture.put(edge)
        fixture.bind(edge,"link-basis")
        with self.assertRaises(IntegrityError):
            fixture.score(frozen,[prediction])
        fixture.protocol.update(created_at="2026-01-01T14:00:00Z",frozen_at="2026-01-01T14:00:00Z",protocol_version="regrouped")
        updated = fixture.freeze()
        report = fixture.score(updated,[])
        self.assertEqual(report["counts"]["excluded_cases"],1)
        self.assertEqual(fixture.protocol["as_of"],"2026-01-01T12:00:00Z")


if __name__ == "__main__":
    unittest.main()
