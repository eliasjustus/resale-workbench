import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from evaluation.check_run_handoffs import check_case, write_result
from evaluation.handoff import finalize
import subprocess
import sys


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class HandoffVerifierTests(unittest.TestCase):
    case = {
        "folder": "short-folder",
        "case_id": "qnap-3510473637",
        "frozen_input": "stages/short-folder/luna-input/target",
        "output": "stages/short-folder/luna-output",
    }

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.run = Path(self.temp.name)
        frozen = self.run / self.case["frozen_input"]
        output = self.run / self.case["output"]
        (frozen / "photos").mkdir(parents=True)
        (output / "evidence/target/photos").mkdir(parents=True)
        (output / "evidence/target").mkdir(exist_ok=True)
        (frozen / "photos/01.jpg").write_bytes(b"frozen-photo")
        packet = {
            "schema_version": 1,
            "photos": [{"index": 1, "status": "downloaded", "path": "photos/01.jpg", "sha256": digest(frozen / "photos/01.jpg")}],
        }
        (frozen / "input.json").write_text(json.dumps(packet), encoding="utf-8")
        (output / "evidence/target/input.json").write_bytes((frozen / "input.json").read_bytes())
        (output / "evidence/target/photos/01.jpg").write_bytes((frozen / "photos/01.jpg").read_bytes())
        self._write_handoff(output)

    def tearDown(self):
        self.temp.cleanup()

    def _write_handoff(self, output: Path, handoff=None, listed_extra=True):
        handoff = handoff or {
            "schema_version": 1,
            "case_id": self.case["case_id"],
            "role": "evidence_preparation",
            "target_price_seen": False,
            "purchase_authorized": False,
            "valuation_performed": False,
            "target": {
                "input_path": "evidence/target/input.json",
                "input_sha256": digest(output / "evidence/target/input.json"),
                "photo_paths": ["evidence/target/photos/01.jpg"],
                "opened_indices": [1],
            },
        }
        handoff_path = output / "handoff.json"
        handoff_path.write_text(json.dumps(handoff), encoding="utf-8")
        files = [
            {"path": "handoff.json", "sha256": digest(handoff_path)},
            {"path": "evidence/target/input.json", "sha256": digest(output / "evidence/target/input.json")},
            {"path": "evidence/target/photos/01.jpg", "sha256": digest(output / "evidence/target/photos/01.jpg")},
        ]
        if not listed_extra:
            files.pop()
        ready = {
            "case_id": handoff["case_id"],
            "handoff_sha256": digest(handoff_path),
            "files": files,
        }
        (output / "READY.json").write_text(json.dumps(ready), encoding="utf-8")

    def _reload_handoff(self):
        output = self.run / self.case["output"]
        handoff_path = output / "handoff.json"
        handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
        self._write_handoff(output, handoff)

    def _with_candidate(self, candidate):
        output = self.run / self.case["output"]
        handoff = json.loads((output / "handoff.json").read_text(encoding="utf-8"))
        handoff["candidates"] = [candidate]
        self._write_handoff(output, handoff)

    def test_passing_case_uses_explicit_case_mapping(self):
        result = check_case(self.run, self.case)
        self.assertTrue(result["ready"], result["errors"])
        self.assertEqual(result["case_id"], "qnap-3510473637")

    def test_wrong_identity_is_rejected(self):
        output = self.run / self.case["output"]
        handoff = json.loads((output / "handoff.json").read_text(encoding="utf-8"))
        handoff["case_id"] = "short-folder"
        self._write_handoff(output, handoff)
        self.assertIn("Wrong case identity", check_case(self.run, self.case)["errors"])

    def test_changed_input_and_photo_are_rejected(self):
        output = self.run / self.case["output"]
        (output / "evidence/target/input.json").write_text("changed", encoding="utf-8")
        (output / "evidence/target/photos/01.jpg").write_bytes(b"changed-photo")
        errors = check_case(self.run, self.case)["errors"]
        self.assertIn("Missing or changed file: evidence/target/input.json", errors)
        self.assertIn("Target input differs from frozen input", errors)
        self.assertIn("Target photo 1 differs from frozen photo", errors)

    def test_escaped_target_path_is_rejected(self):
        output = self.run / self.case["output"]
        handoff = json.loads((output / "handoff.json").read_text(encoding="utf-8"))
        handoff["target"]["input_path"] = "../outside.json"
        self._write_handoff(output, handoff)
        self.assertIn("Target input path escapes packet or is absent", check_case(self.run, self.case)["errors"])

    def test_omitted_file_is_rejected(self):
        output = self.run / self.case["output"]
        self._write_handoff(output, listed_extra=False)
        errors = check_case(self.run, self.case)["errors"]
        self.assertIn("Manifest does not cover exact packet file set", errors)
        self.assertIn("Target photo 1 is omitted from READY manifest", errors)

    def test_first_successful_barrier_is_preserved(self):
        result = {"all_ready": True, "cases": []}
        first = write_result(self.run, result)
        second = write_result(self.run, result)
        self.assertEqual(first.name, "handoff-check.json")
        self.assertEqual(second.name, "handoff-recheck-1.json")
        self.assertEqual(json.loads(first.read_text())["all_ready"], True)

    def test_candidate_gallery_must_be_in_packet_and_ready_manifest(self):
        self._with_candidate({
            "gallery_expected_count": 1,
            "gallery_opened_indices": [1],
            "gallery_paths": ["evidence/target/photos/01.jpg"],
        })
        result = check_case(self.run, self.case)
        self.assertTrue(result["ready"], result["errors"])
        self.assertTrue(result["substantive_ready"], result["substantive_errors"])
        self.assertEqual(result["gallery_audits"][0]["status"], "fully_audited")

    def test_candidate_gallery_escape_and_ready_omission_are_semantic_failures(self):
        self._with_candidate({
            "gallery_expected_count": 1,
            "gallery_opened_indices": [1],
            "gallery_paths": ["../outside.jpg"],
        })
        escaped = check_case(self.run, self.case)
        self.assertFalse(escaped["substantive_ready"])
        self.assertIn("escapes packet", " ".join(escaped["substantive_errors"]))

        extra = self.run / self.case["output"] / "evidence/target/unlisted.jpg"
        extra.write_bytes(b"unlisted-gallery")
        self._with_candidate({
            "gallery_expected_count": 1,
            "gallery_opened_indices": [1],
            "gallery_paths": ["evidence/target/unlisted.jpg"],
        })
        omitted = check_case(self.run, self.case)
        self.assertIn("omitted from READY manifest", " ".join(omitted["substantive_errors"]))

    def test_inconsistent_gallery_counts_are_not_full_audit(self):
        self._with_candidate({
            "gallery_expected_count": 5,
            "gallery_opened_indices": [1],
            "gallery_paths": [],
            "missing_evidence": ["Closed source gallery unavailable"],
        })
        result = check_case(self.run, self.case)
        self.assertFalse(result["substantive_ready"])
        self.assertIn("opened/retained gallery count inconsistent", " ".join(result["substantive_errors"]))
        self.assertEqual(result["gallery_audits"][0]["status"], "ready_as_incomplete")

    def test_explicit_nonvisual_scope_allows_limited_source_only_ref(self):
        self._with_candidate({
            "gallery_expected_count": 5,
            "gallery_opened_indices": [],
            "gallery_paths": [],
            "missing_evidence": ["Aggregate listing excluded from visual audit by scope"],
            "visual_audit_scope": {"status": "not_required", "reason": "aggregate_sale", "evidence": ["source row units_sold=3"]},
        })
        result = check_case(self.run, self.case)
        self.assertTrue(result["substantive_ready"], result["substantive_errors"])
        self.assertEqual(result["gallery_audits"][0]["status"], "limited_source_only")

    def test_unknown_country_is_not_an_exclusion(self):
        self._with_candidate({'gallery_expected_count': 3, 'gallery_opened_indices': [],
                              'gallery_paths': [], 'missing_evidence': ['seller country unknown']})
        self.assertFalse(check_case(self.run, self.case)['substantive_ready'])

    def test_asset_versions_count_as_one_gallery_position(self):
        output = self.run / self.case['output']
        extra = output / 'second-size.jpg'
        extra.write_bytes(b'second-size')
        paths = ['evidence/target/photos/01.jpg', 'second-size.jpg']
        self._with_candidate({'gallery_expected_count': 1, 'gallery_opened_indices': [1],
                              'gallery_paths': paths, 'gallery_assets': [{'index': 1, 'path': p} for p in paths]})
        finalize(output)
        result = check_case(self.run, self.case)
        self.assertTrue(result['ready'], result['errors'])
        self.assertTrue(result['substantive_ready'], result['substantive_errors'])
        self.assertEqual(result['gallery_audits'][0]['retained_count'], 1)
        handoff = json.loads((output / 'handoff.json').read_text())
        handoff['candidates'][0]['gallery_expected_count'] = 2
        (output / 'handoff.json').write_text(json.dumps(handoff))
        finalize(output)
        self.assertFalse(check_case(self.run, self.case)['substantive_ready'])

    def test_legacy_manifest_may_include_lessons_without_losing_required_checks(self):
        output = self.run / self.case['output']
        (output / 'lessons.md').write_text('A lesson')
        ready = finalize(output)
        ready['files'].append({'path': 'lessons.md', 'sha256': digest(output / 'lessons.md')})
        (output / 'READY.json').write_text(json.dumps(ready))
        self.assertTrue(check_case(self.run, self.case)['ready'])

    def test_cli_does_not_signal_dispatch_for_hash_valid_incomplete_gallery(self):
        self._with_candidate({'gallery_expected_count': 2, 'gallery_opened_indices': [], 'gallery_paths': []})
        (self.run / 'stage-config.json').write_text(json.dumps({'luna_cases': [self.case]}))
        command = [sys.executable, '-m', 'evaluation.check_run_handoffs', str(self.run)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report['all_ready'])
        self.assertFalse(report['dispatch_ready'])


if __name__ == "__main__":
    unittest.main()
