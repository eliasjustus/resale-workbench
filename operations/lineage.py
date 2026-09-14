"""Reviewed event equivalence, distinct resales, and conservative split groups."""
from .clock import parse_timestamp
from .contracts import read_record
from .errors import IntegrityError
from .events import event_view
from .serialization import digest
from .verification import read_verification


class _Groups:
    def __init__(self, identities):
        self.parents = {identity:identity for identity in identities}

    def root(self, identity):
        while identity != self.parents[identity]:
            identity = self.parents[identity]
        return identity

    def join(self, left, right):
        left, right = self.root(left), self.root(right)
        if left != right:
            self.parents[max(left, right)] = min(left, right)

    def values(self):
        groups = {}
        for identity in sorted(self.parents):
            groups.setdefault(self.root(identity), []).append(identity)
        return sorted(groups.values())


def group_events(records, edges, *, as_of, synthetic, verify_edge=None):
    """Freeze an as-of grouping. Unreviewed overlaps cannot increase adequacy.

    verify_edge qualifies edge basis and reviewer scope; absence remains unknown.
    This grouping alone does not verify transaction or price truth.
    """
    view = event_view(records, as_of=as_of, synthetic=synthetic)
    events = {record["record_id"]:record for record in view["records"]}
    groups, partitions = _Groups(events), _Groups(events)
    unknown = {key for key,value in events.items() if value["event_id"] is None or value["lineage_status"] != "established"}
    by_event = {}
    for identity, record in events.items():
        if record["event_id"] is not None:
            prior = by_event.setdefault(record["event_id"], identity)
            groups.join(prior, identity)
            partitions.join(prior, identity)
    visible, superseded = {}, set()
    for value in edges:
        edge = read_record(value, expected_type="lineage_edge")
        if edge["synthetic"] != synthetic:
            raise IntegrityError("Lineage cannot mix real and synthetic records")
        if parse_timestamp(edge["created_at"]) > parse_timestamp(as_of) or parse_timestamp(edge["reviewed_at"]) > parse_timestamp(as_of):
            continue
        if edge["record_id"] in visible:
            raise IntegrityError("Duplicate lineage edge identity")
        visible[edge["record_id"]] = edge
    for edge in visible.values():
        left, right = edge["left_evidence_id"], edge["right_evidence_id"]
        if left not in events or right not in events or left == right:
            raise IntegrityError("Lineage endpoints must name two visible evidence records")
        previous_id = edge["supersedes_edge_id"]
        if previous_id:
            previous = visible.get(previous_id)
            if previous is None or previous_id in superseded or {previous["left_evidence_id"],previous["right_evidence_id"]} != {left,right} or parse_timestamp(previous["created_at"]) >= parse_timestamp(edge["created_at"]):
                raise IntegrityError("Lineage correction must replace one earlier edge for these endpoints")
            superseded.add(previous_id)
    active = [edge for identity,edge in sorted(visible.items()) if identity not in superseded]
    distinct = []
    for edge in active:
        left, right = edge["left_evidence_id"], edge["right_evidence_id"]
        verified = edge["status"] == "confirmed" and bool(edge["basis_claim_ids"]) and verify_edge is not None and read_verification(verify_edge(edge, as_of=as_of))["state"] == "pass"
        if not verified:
            unknown.update((left,right))
            partitions.join(left,right)
        elif edge["relation"] == "same_event":
            groups.join(left,right)
            partitions.join(left,right)
        elif edge["relation"] == "same_physical_item":
            partitions.join(left,right)
        else:
            distinct.append((left,right))
    for left,right in distinct:
        if groups.root(left) == groups.root(right):
            unknown.update((left,right))
    result_groups = groups.values()
    result = {"as_of":as_of, "event_groups":result_groups, "partition_groups":partitions.values(), "unresolved_evidence_ids":sorted(unknown), "independent_event_count":sum(not any(identity in unknown for identity in group) for group in result_groups), "active_edge_ids":[edge["record_id"] for edge in active], "excluded":view["excluded"], "purchase_authorized":False}
    return dict(result, grouping_sha256=digest(result, domain="projection"))
