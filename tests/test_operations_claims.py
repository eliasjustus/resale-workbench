"""Synthetic retained bytes, substantive contradiction and replacement probes."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import subprocess
from unittest.mock import patch

from operations.claims import verify_claim, verify_retained_file
from operations.contracts import record_digest
from operations.errors import IntegrityError
from operations.sources import assess_source, SCOPE_FIELDS

RECORDS = json.loads((Path(__file__).parent / "fixtures/operations-records.json").read_text(encoding="utf-8"))


class MaterialClaimTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "written.txt"
        self.source.write_bytes(b"Synthetic observation: ownership is disputed.")
        self.reference = {"relative_path":"written.txt", "sha256":hashlib.sha256(self.source.read_bytes()).hexdigest(), "locator":"sentence 1"}
        self.claim = dict(deepcopy(RECORDS["material_claim"]), status="established", claim_type="ownership", source_refs=[self.reference])
        self.review = dict(deepcopy(RECORDS["claim_review"]), claim_id=self.claim["record_id"], claim_sha256=record_digest(self.claim), source_refs=[self.reference], conclusion="established", competence_claim_ids=["synthetic-competence"])
        self.inputs = dict(root=self.root, as_of="2026-01-01T12:30:00Z", synthetic=True, review_authority=lambda *args, **kwargs:{"state":"pass"})

    def test_matching_digest_alone_is_not_substantive_verification(self):
        self.assertEqual(verify_retained_file(self.root, self.reference), self.reference)
        self.assertEqual(verify_claim(self.claim, reviews=[], **self.inputs)["state"], "unknown")
        self.assertEqual(verify_claim(self.claim, reviews=[self.review], **dict(self.inputs, review_authority=None))["state"], "unknown")
        contradicted = dict(self.review, conclusion="contradicted")
        result = verify_claim(self.claim, reviews=[contradicted], **self.inputs)
        self.assertEqual(result["state"], "fail")
        self.assertIn("substantive_review_contradiction", result["reason_codes"])

    def test_changed_bytes_and_paths_fail_and_deletion_keeps_history(self):
        original = deepcopy(self.claim)
        self.assertEqual(verify_claim(self.claim, reviews=[self.review], **self.inputs)["state"], "pass")
        self.source.write_bytes(b"modified")
        self.assertEqual(verify_claim(self.claim, reviews=[self.review], **self.inputs)["state"], "fail")
        self.source.unlink()
        self.assertEqual(verify_claim(self.claim, reviews=[self.review], **self.inputs)["state"], "unknown")
        self.assertEqual(self.claim, original)
        for value in ("../outside", "/outside", "I:/outside", "folder/../written.txt", "./written.txt", "folder\\written.txt"):
            with self.assertRaises(IntegrityError):
                verify_retained_file(self.root, dict(self.reference, relative_path=value))

    def test_check_open_replacement_is_detected_using_file_identity(self):
        original_open = Path.open
        switched = False
        # Save bytes before patch to avoid a recursive read in the attack helper.
        data = self.source.read_bytes()
        def replace_once(path, *args, **kwargs):
            nonlocal switched
            if path == self.source and not switched:
                switched = True
                replacement = self.root / "replacement"
                with original_open(replacement,"wb") as stream:
                    stream.write(data)
                os.replace(replacement, self.source)
            return original_open(path, *args, **kwargs)
        with patch.object(Path,"open",replace_once), self.assertRaises(IntegrityError):
            verify_retained_file(self.root,self.reference)

    def test_tombstone_and_conflict_cannot_be_erased_by_intact_bytes(self):
        tombstone = dict(RECORDS["evidence_tombstone"], source_refs=[self.reference], affected_claim_ids=[self.claim["record_id"]])
        result = verify_claim(self.claim,reviews=[self.review],tombstones=[tombstone],**self.inputs)
        self.assertIn("retained_source_deleted",result["reason_codes"])
        conflict = dict(self.review,conflicting_claim_ids=["other-claim"])
        self.assertEqual(verify_claim(self.claim,reviews=[conflict],**self.inputs)["state"],"unknown")
        other_locator = dict(tombstone,source_refs=[dict(self.reference,locator="another sentence")],affected_claim_ids=[])
        self.assertIn("retained_source_deleted",verify_claim(self.claim,reviews=[self.review],tombstones=[other_locator],**self.inputs)["reason_codes"])
        qualified = dict(self.review,limitations=["Only this synthetic document was reviewed"])
        result = verify_claim(self.claim,reviews=[qualified],**self.inputs)
        self.assertEqual(result["review_limitations"],[{"review_id":qualified["record_id"],"limitations":qualified["limitations"]}])

    def test_qualified_claim_is_bound_to_exact_source_scope_before_action(self):
        rule = dict(RECORDS["source_rule"],collection="allowed",basis_refs=[self.claim["record_id"]])
        claim = dict(self.claim,claim_type="legal_applicability",subject={"record_id":rule["record_id"],"record_type":"source_rule","record_sha256":record_digest(rule),"verified_fields":[*SCOPE_FIELDS,"collection"]})
        review = dict(self.review,claim_sha256=record_digest(claim))
        def verifier(identity, **kwargs):
            self.assertEqual(identity,claim["record_id"])
            return verify_claim(claim,reviews=[review],**self.inputs)
        context = {key:rule[key] for key in SCOPE_FIELDS}
        inputs = dict(action="collection",context=context,as_of=self.inputs["as_of"],synthetic=True,resolve={claim["record_id"]:claim}.get,verify_claim=verifier)
        self.assertEqual(assess_source(rule,**inputs)["state"],"pass")
        prior_claim = dict(claim,subject=None)
        prior_review = dict(review,claim_sha256=record_digest(prior_claim))
        def stale_verifier(identity,**kwargs):
            return verify_claim(prior_claim,reviews=[prior_review],**self.inputs)
        stale = assess_source(rule,**dict(inputs,verify_claim=stale_verifier))
        self.assertEqual(stale["state"],"fail")
        self.assertIn("authority_verification_snapshot_mismatch",stale["reason_codes"])
        changed = dict(rule,purpose="other purpose")
        result = assess_source(changed,**dict(inputs,context=dict(context,purpose="other purpose")))
        self.assertIn("authority_not_bound_to_source_action",result["reason_codes"])
        self.source.unlink()
        self.assertEqual(assess_source(rule,**inputs)["state"],"unknown")

    def test_actual_directory_link_is_rejected(self):
        target = self.root / "target"
        target.mkdir()
        (target/"written.txt").write_bytes(self.source.read_bytes())
        link = self.root / "linked"
        if os.name == "nt":
            made = subprocess.run(["cmd","/c","mklink","/J",str(link),str(target)],capture_output=True,text=True)
            if made.returncode:
                self.skipTest("Host cannot create a synthetic junction: "+made.stderr)
        else:
            link.symlink_to(target,target_is_directory=True)
        with self.assertRaises(IntegrityError):
            verify_retained_file(self.root,dict(self.reference,relative_path="linked/written.txt"))


if __name__ == "__main__":
    unittest.main()
