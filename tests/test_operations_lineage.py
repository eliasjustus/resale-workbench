from copy import deepcopy
import json
from pathlib import Path
import unittest

from operations.lineage import group_events

RECORDS = json.loads((Path(__file__).parent / "fixtures/operations-records.json").read_text(encoding="utf-8"))


class EventLineageTests(unittest.TestCase):
    def setUp(self):
        self.first = dict(deepcopy(RECORDS["evidence_event"]),record_id="first",event_id="sale-a",lineage_status="established")
        self.second = dict(self.first,record_id="second",event_id="sale-b",evidence_id="another-file")
        self.edge = dict(deepcopy(RECORDS["lineage_edge"]),record_id="merge",left_evidence_id="first",right_evidence_id="second",relation="same_event",status="confirmed",basis_claim_ids=["identity-review"])
        self.inputs = dict(as_of="2026-01-01T12:30:00Z",synthetic=True,verify_edge=lambda *args,**kwargs:{"state":"pass"})

    def test_copies_group_once_but_later_physical_resale_stays_distinct(self):
        merged = group_events([self.first,self.second],[self.edge],**self.inputs)
        self.assertEqual(merged["independent_event_count"],1)
        resale = group_events([self.first,self.second],[dict(self.edge,relation="same_physical_item")],**self.inputs)
        self.assertEqual(resale["independent_event_count"],2)
        self.assertEqual(resale["partition_groups"],[["first","second"]])

    def test_disputed_overlap_neither_adds_support_nor_leaks_between_partitions(self):
        result = group_events([self.first,self.second],[dict(self.edge,status="disputed")],**self.inputs)
        self.assertEqual(result["independent_event_count"],0)
        self.assertEqual(result["partition_groups"],[["first","second"]])
        self.assertEqual(result["unresolved_evidence_ids"],["first","second"])

    def test_split_is_append_only_and_old_group_digest_reproduces(self):
        correction = dict(self.edge,record_id="split",relation="distinct_event",supersedes_edge_id="merge",created_at="2026-01-02T12:00:00Z",reviewed_at="2026-01-02T12:00:00Z")
        original = deepcopy([self.edge,correction])
        historical = group_events([self.first,self.second],original,**self.inputs)
        current = group_events([self.first,self.second],original,**dict(self.inputs,as_of="2026-01-02T12:30:00Z"))
        self.assertEqual(historical["independent_event_count"],1)
        self.assertEqual(current["independent_event_count"],2)
        self.assertNotEqual(historical["grouping_sha256"],current["grouping_sha256"])
        self.assertEqual(group_events([self.first,self.second],original,**self.inputs),historical)
        self.assertEqual(original,[self.edge,correction])


if __name__ == "__main__":
    unittest.main()
