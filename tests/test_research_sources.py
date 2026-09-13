import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from evaluation.research_sources import (
    CAPTURE_METHOD, ROW_FIELDS, ValidationError, merge_packets,
    validate_capture, validate_packet,
)


class ResearchSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.capture_dir = self.root / "capture"
        self.capture_dir.mkdir()
        files = {}
        for kind, filename, second in (("dom_snapshot", "dom.txt", 1), ("rendered_text", "rendered.txt", 2)):
            data = b"Visible source text: EUR 100, sold"
            (self.capture_dir / filename).write_bytes(data)
            files[kind] = {"path": filename, "bytes": len(data),
                           "sha256": hashlib.sha256(data).hexdigest(),
                           "captured_at": f"2026-09-13T10:00:0{second}+00:00"}
        self.capture = {"schema_version": 1, "capture_method": CAPTURE_METHOD,
                        "started_at": "2026-09-13T10:00:00Z", "completed_at": "2026-09-13T10:00:03Z",
                        "source_url": "https://example.test/search", "source_url_after": "https://example.test/search",
                        "source_title": "Search", "context": {}, "files": files}
        self.write_capture()

    def write_capture(self):
        path = self.capture_dir / "capture.json"
        path.write_text(json.dumps(self.capture), encoding="utf-8")
        return path

    def row(self, **values):
        return {**dict.fromkeys(ROW_FIELDS), "title": "A source title", **values}

    def attempt(self, kind="sold", rows=None):
        return {"capture_path": "capture/capture.json", "query": "camera model",
                "displayed_period": "Letzte 90 Tage", "displayed_filters": None,
                "result_kind": kind, "rows": rows or [], "limits": []}

    def packet(self, name="packet.json", attempts=None):
        path = self.root / name
        path.write_text(json.dumps({"schema_version": 1, "role": "sold_search", "case_id": "case-1",
                                    "attempts": attempts or [self.attempt()], "lessons": ["A separate lesson"]}), encoding="utf-8")
        return path

    def test_valid_capture_and_packet(self):
        self.assertEqual(validate_capture(self.capture_dir)["source_title"], "Search")
        self.assertEqual(validate_packet(self.packet())["case_id"], "case-1")

    def test_missing_and_changed_raw_capture(self):
        raw = self.capture_dir / "dom.txt"
        original = raw.read_bytes()
        raw.unlink()
        with self.assertRaisesRegex(ValidationError, "Missing raw"):
            validate_capture(self.capture_dir)
        raw.write_bytes(b"X" * len(original))
        with self.assertRaisesRegex(ValidationError, "SHA-256"):
            validate_capture(self.capture_dir)

    def test_nonempty_and_byte_count(self):
        raw = self.capture_dir / "dom.txt"
        raw.write_bytes(b" ")
        with self.assertRaisesRegex(ValidationError, "Empty raw"):
            validate_capture(self.capture_dir)
        raw.write_bytes(b"changed length")
        with self.assertRaisesRegex(ValidationError, "Byte count"):
            validate_capture(self.capture_dir)

    def test_escaping_capture_and_packet_paths(self):
        self.capture["files"]["dom_snapshot"]["path"] = "../outside.txt"
        self.write_capture()
        with self.assertRaisesRegex(ValidationError, "escapes"):
            validate_capture(self.capture_dir)
        for value in ("../outside/capture.json", "C:\\outside\\capture.json", "/outside/capture.json"):
            with self.subTest(value=value):
                attempt = self.attempt()
                attempt["capture_path"] = value
                with self.assertRaises(ValidationError):
                    validate_packet(self.packet(attempts=[attempt]))

    def test_method_chronology_and_navigation(self):
        for field, value, pattern in (("capture_method", "transcribed", "capture_method"),
                                      ("completed_at", "2026-09-13T09:00:00Z", "chronology"),
                                      ("started_at", "2026-09-13T10:00:00", "timezone"),
                                      ("source_url_after", "https://example.test/other", "URL changed")):
            with self.subTest(field=field):
                original = self.capture[field]
                self.capture[field] = value
                self.write_capture()
                with self.assertRaisesRegex(ValidationError, pattern):
                    validate_capture(self.capture_dir)
                self.capture[field] = original

    def test_invalid_attempt_kind_and_nullable_rows(self):
        for kind in ("asking", None, [], "SOLD"):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValidationError, "result_kind"):
                validate_packet(self.packet(attempts=[self.attempt(kind)]))
        validate_packet(self.packet(attempts=[self.attempt(rows=[self.row()])]))
        with self.assertRaisesRegex(ValidationError, "string or null"):
            validate_packet(self.packet(attempts=[self.attempt(rows=[self.row(price_text=100)])]))

    def test_active_rows_excluded_even_with_duplicate_sold_id(self):
        path = self.packet(attempts=[self.attempt(rows=[self.row(sale_id="123", price_text="EUR 100")]),
                                     self.attempt("active_fallback", [self.row(sale_id="123", price_text="EUR 900")])])
        merged = merge_packets([path])
        self.assertEqual(len(merged["sold_evidence"]), 1)
        self.assertEqual(len(merged["sold_evidence"][0]["observations"]), 1)
        self.assertEqual(merged["sold_evidence"][0]["conflicts"], {})
        self.assertEqual(merged["excluded_attempts"][0]["rows"][0]["price_text"], "EUR 900")

    def test_duplicate_conflicts_preserved_unknown_ids_separate(self):
        a = self.packet("a.json", [self.attempt(rows=[self.row(sale_id="123", price_text="EUR 100", country_text="DE", units_text="1"), self.row()])])
        b = self.packet("b.json", [self.attempt(rows=[self.row(sale_id="123", price_text="EUR 90", country_text="AT", units_text="2"), self.row()])])
        merged = merge_packets([a, b])
        self.assertEqual(len(merged["sold_evidence"]), 1)
        evidence = merged["sold_evidence"][0]
        self.assertEqual(len(evidence["observations"]), 2)
        self.assertEqual(evidence["conflicts"], {"price_text": ["EUR 100", "EUR 90"], "units_text": ["1", "2"], "country_text": ["DE", "AT"]})
        self.assertEqual(len(merged["unmatched_sold_rows"]), 2)
        self.assertEqual(len(merged["lessons"]), 2)
        self.assertIn("packet_path", evidence["observations"][0])


if __name__ == "__main__":
    unittest.main()
