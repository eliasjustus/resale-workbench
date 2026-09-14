"""Authenticated local decisions and commitments; no purchase/contact adapter."""
from dataclasses import dataclass
import ctypes
import os

from . import capacity, service, store
from .bindings import subject_claims
from .clock import parse_timestamp, utc_now
from .contracts import read_record, record_digest
from .errors import ConflictError, IntegrityError, UnresolvedError
from .serialization import digest, load_json

_CONTEXT_KEY = object()


def _host_identity():
    """Actual process token identity, never USERNAME/USER or a caller string."""
    if os.name == "posix":
        return "posix-uid:"+str(os.geteuid())
    if os.name != "nt":
        raise UnresolvedError("This host has no qualified local identity probe")
    from ctypes import wintypes
    advapi, kernel = ctypes.WinDLL("advapi32",use_last_error=True),ctypes.WinDLL("kernel32",use_last_error=True)
    token = wintypes.HANDLE()
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE,wintypes.DWORD,ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p,ctypes.POINTER(wintypes.LPWSTR)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(),8,ctypes.byref(token)):
        raise UnresolvedError("Cannot authenticate process token")
    try:
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token,1,None,0,ctypes.byref(size))
        if not size.value:
            raise UnresolvedError("Cannot inspect process user")
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token,1,buffer,size,ctypes.byref(size)):
            raise UnresolvedError("Cannot inspect process user")
        sid = ctypes.cast(buffer,ctypes.POINTER(ctypes.c_void_p))[0]
        text = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(sid,ctypes.byref(text)):
            raise UnresolvedError("Cannot resolve process SID")
        try:
            return "windows-sid:"+text.value
        finally:
            kernel.LocalFree(ctypes.cast(text,ctypes.c_void_p))
    finally:
        kernel.CloseHandle(token)


@dataclass(frozen=True)
class LocalContext:
    actor_ref: str
    identity_ref: str
    scope: str
    synthetic: bool
    process_id: int
    _key: object


def _actor(events,actor_ref,*,identity,scope,action,as_of,verify_claim):
    records = {record["record_id"]:record for _,record in events}
    actor = records.get(actor_ref)
    if actor is None:
        raise UnresolvedError("Actor authority is missing")
    actor = read_record(actor,expected_type="actor_authority")
    now = parse_timestamp(as_of)
    permission = action+":"+scope
    if actor["identity_ref"] != identity or permission not in actor["scopes"]:
        raise IntegrityError("Authenticated process identity lacks this human scope")
    if parse_timestamp(actor["created_at"]) > now or parse_timestamp(actor["effective_at"]) > now or parse_timestamp(actor["valid_until"]) <= now or actor["revoked_at"] is not None:
        raise UnresolvedError("Actor authority is not current")
    for _,record in events:
        if record["record_type"] == "actor_authority" and record["identity_ref"] == identity and record["synthetic"] == actor["synthetic"] and permission in record["scopes"] and record["revoked_at"] is not None and parse_timestamp(record["created_at"]) <= now:
            raise UnresolvedError("Actor scope has a retained revocation")
    check = subject_claims(actor["grant_claim_ids"],actor,as_of=as_of,resolve=records.get,verify_claim=verify_claim)
    if check["state"] != "pass" or not {"identity_ref","scopes","effective_at","valid_until","grant_claim_ids"}.issubset(check["verified_fields"]):
        raise UnresolvedError("Local human authority needs a reviewed explicit grant")
    return actor,check


def authenticate(path,actor_ref,*,scope,verify_claim=None):
    identity = _host_identity()
    with store._connection(path) as connection:
        actor,_ = _actor(store._events(connection),actor_ref,identity=identity,scope=scope,action="human_decision",as_of=utc_now(),verify_claim=verify_claim)
    return LocalContext(actor_ref,identity,scope,actor["synthetic"],os.getpid(),_CONTEXT_KEY)


def _context(context,events,*,action,as_of,verify_claim):
    if type(context) is not LocalContext or context._key is not _CONTEXT_KEY or context.process_id != os.getpid() or context.identity_ref != _host_identity():
        raise IntegrityError("An authenticated context from this process is required")
    actor,check = _actor(events,context.actor_ref,identity=context.identity_ref,scope=context.scope,action=action,as_of=as_of,verify_claim=verify_claim)
    if actor["synthetic"] != context.synthetic:
        raise IntegrityError("Authenticated context crossed synthetic boundary")
    return actor,check


def _retained(connection,identity,kind):
    row = connection.execute("SELECT * FROM records WHERE record_id=?",(identity,)).fetchone()
    if row is None:
        raise UnresolvedError("Required local record is missing: "+identity)
    return read_record(store._validated_row(row),expected_type=kind)


def _retry(connection,key,intent,record_id,kind):
    old = connection.execute("SELECT record_id FROM request_receipts WHERE request_key=?",(key,)).fetchone()
    if old is None:
        return None
    record = _retained(connection,old[0],kind)
    if record["record_id"] != record_id or record["request_sha256"] != digest(intent,domain="request"):
        raise ConflictError("Request key was already used for a different complete intent")
    return store._append(connection,record,request_key=key)


def _current(connection,snapshot,*,as_of,verify_claim,verify_edge):
    if parse_timestamp(snapshot["valid_until"]) <= parse_timestamp(as_of):
        raise UnresolvedError("Readiness expired; obtain new readiness and approval")
    request = _retained(connection,snapshot["request_id"],"readiness_request")
    result = service._assess(connection,request,as_of=as_of,verify_claim=verify_claim,verify_edge=verify_edge)
    if result["status"] != "ready_for_human_review" or snapshot["status"] != "ready_for_human_review" or result["binding_sha256"] != snapshot["binding_sha256"] or digest(load_json(snapshot["material_json"]),domain="projection") != snapshot["binding_sha256"]:
        raise UnresolvedError("Readiness material changed or is unresolved; new readiness and approval are required")
    return result


def _audit(events,actor,grant,judgment,attempt,identity):
    exposures = [record for _,record in events if record["record_type"] == "advice_exposure" and record["attempt_id"] == attempt["record_id"]]
    return {"actor":record_digest(actor),"grant":grant,"judgment":record_digest(judgment),"attempt":record_digest(attempt),"exposures":[record_digest(record) for record in exposures],"host_identity_ref":identity}


def _finish(start,*deadlines):
    finished = utc_now()
    if parse_timestamp(finished) < parse_timestamp(start) or any(parse_timestamp(finished) >= parse_timestamp(value) for value in deadlines):
        raise UnresolvedError("Authorization expired or clock moved backwards during checks")
    return finished


def decide(path,*,context,readiness_id,decision,initial_judgment_id,exposure_id,reason,valid_until,record_id,request_key,verify_claim=None,verify_edge=None):
    intent = {"actor_ref":getattr(context,"actor_ref",None),"readiness_id":readiness_id,"decision":decision,"initial_judgment_id":initial_judgment_id,"exposure_id":exposure_id,"reason":reason,"valid_until":valid_until,"record_id":record_id}
    with store._connection(path,write=True) as connection:
        events = store._events(connection)
        now = utc_now()
        actor,grant = _context(context,events,action="human_decision",as_of=now,verify_claim=verify_claim)
        retry = _retry(connection,request_key,intent,record_id,"human_decision")
        if retry is not None:
            return retry
        snapshot = _retained(connection,readiness_id,"readiness_snapshot")
        if (snapshot["actor_ref"],snapshot["scope"],snapshot["synthetic"]) != (context.actor_ref,context.scope,context.synthetic):
            raise IntegrityError("Human decision differs from the readiness actor/scope")
        if decision == "approve":
            _current(connection,snapshot,as_of=now,verify_claim=verify_claim,verify_edge=verify_edge)
        until = parse_timestamp(valid_until)
        if until <= parse_timestamp(now) or until > min(parse_timestamp(snapshot["valid_until"]),parse_timestamp(actor["valid_until"])):
            raise UnresolvedError("Decision validity exceeds current readiness or authority")
        judgment = _retained(connection,initial_judgment_id,"initial_judgment")
        exposure = _retained(connection,exposure_id,"advice_exposure")
        attempt = _retained(connection,judgment["attempt_id"],"review_attempt")
        for record in (judgment,exposure,attempt):
            if record["actor_ref"] != context.actor_ref or record["synthetic"] != context.synthetic or parse_timestamp(record["created_at"]) > parse_timestamp(now):
                raise IntegrityError("Judgment/exposure actor or observation time differs")
        if judgment["case_id"] != snapshot["case_id"] or attempt["case_id"] != snapshot["case_id"] or attempt["scope"] != context.scope or exposure["attempt_id"] != attempt["record_id"] or judgment["packet_sha256"] != attempt["packet_sha256"]:
            raise IntegrityError("Judgment, packet and exposure must belong to the same case attempt")
        audit = _audit(events,actor,grant,judgment,attempt,context.identity_ref)
        now = _finish(now,valid_until,snapshot["valid_until"],actor["valid_until"])
        record = {"record_type":"human_decision","schema_version":1,"record_id":record_id,"created_at":now,"synthetic":context.synthetic,"producer_ref":context.actor_ref,"case_id":snapshot["case_id"],"actor_ref":context.actor_ref,"decision":decision,"readiness_snapshot_id":readiness_id,"readiness_snapshot_sha256":record_digest(snapshot),"initial_judgment_ref":initial_judgment_id,"advice_exposure_log_ref":exposure_id,"reason":reason,"approved_scope":context.scope,"valid_until":valid_until,"host_identity_ref":context.identity_ref,"context_sha256":digest(audit,domain="projection"),"request_sha256":digest(intent,domain="request")}
        return store._append(connection,read_record(record),request_key=request_key)


def _manual_attempts(events):
    intents = {}
    for _,record in events:
        if record["record_type"] != "execution_attempt" or record["kind"] != "manual_action":
            continue
        prior = intents.get(record["intent_id"])
        if prior is None:
            if record["prior_attempt_id"] is not None or record["state"] != "preflight_passed":
                raise IntegrityError("Manual intent must begin with one preflight commitment")
        else:
            if record["prior_attempt_id"] != prior["record_id"] or prior["state"] not in {"preflight_passed","outcome_unknown"} or record["state"] not in {"outcome_unknown","confirmed_executed","failed_before_action"}:
                raise IntegrityError("Manual reconciliation is stale, duplicate or already terminal")
            if any(record[field] != prior[field] for field in ("decision_id","actor_ref","scope","synthetic","reservation_id","readiness_snapshot_id")):
                raise IntegrityError("Manual reconciliation changed its commitment binding")
        intents[record["intent_id"]] = record
    return intents


def require_reconciled_release(events,*,scope,synthetic,reservation_id):
    for record in _manual_attempts(events).values():
        if (record["scope"],record["synthetic"],record["reservation_id"]) == (scope,synthetic,reservation_id) and record["state"] in {"preflight_passed","outcome_unknown"}:
            raise UnresolvedError("Unconfirmed manual action retains its commitment until reviewed reconciliation")


def preflight(path,*,context,decision_id,intent_id,record_id,request_key,verify_claim=None,verify_edge=None):
    intent = {"decision_id":decision_id,"intent_id":intent_id,"actor_ref":getattr(context,"actor_ref",None),"record_id":record_id}
    with store._connection(path,write=True) as connection:
        events = store._events(connection)
        now = utc_now()
        actor,grant = _context(context,events,action="manual_preflight",as_of=now,verify_claim=verify_claim)
        retry = _retry(connection,request_key,intent,record_id,"execution_attempt")
        if retry is not None:
            return retry
        attempts = _manual_attempts(events)
        if intent_id in attempts or any(record["decision_id"] == decision_id for record in attempts.values()):
            raise ConflictError("Manual intent/decision already has a commitment; reconcile its outcome")
        decision = _retained(connection,decision_id,"human_decision")
        current_decisions = [record for _,record in events if record["record_type"] == "human_decision" and all(record[field] == decision[field] for field in ("case_id","actor_ref","approved_scope","synthetic"))]
        if current_decisions[-1]["record_id"] != decision_id:
            raise UnresolvedError("A later human decision displaced this approval")
        if decision["decision"] != "approve" or parse_timestamp(decision["valid_until"]) <= parse_timestamp(now):
            raise UnresolvedError("Current explicit human approval is required")
        if (decision["actor_ref"],decision["approved_scope"],decision["synthetic"],decision["host_identity_ref"]) != (context.actor_ref,context.scope,context.synthetic,context.identity_ref):
            raise IntegrityError("Preflight actor differs from the scoped human approval")
        judgment = _retained(connection,decision["initial_judgment_ref"],"initial_judgment")
        attempt = _retained(connection,judgment["attempt_id"],"review_attempt")
        if digest(_audit(events,actor,grant,judgment,attempt,context.identity_ref),domain="projection") != decision["context_sha256"]:
            raise UnresolvedError("Human review context changed; new approval is required")
        snapshot = _retained(connection,decision["readiness_snapshot_id"],"readiness_snapshot")
        if record_digest(snapshot) != decision["readiness_snapshot_sha256"]:
            raise IntegrityError("Approval readiness digest changed")
        current = _current(connection,snapshot,as_of=now,verify_claim=verify_claim,verify_edge=verify_edge)
        now = _finish(now,decision["valid_until"],snapshot["valid_until"],actor["valid_until"],current["valid_until"])
        account = capacity.reduce_resources(events)[(context.scope,context.synthetic)]
        record = {"record_type":"execution_attempt","schema_version":1,"record_id":record_id,"created_at":now,"synthetic":context.synthetic,"producer_ref":context.actor_ref,"intent_id":intent_id,"decision_id":decision_id,"actor_ref":context.actor_ref,"correlation_id":None,"state":"preflight_passed","occurred_at":now,"cost_cents":None,"reconciliation_claim_ids":[],"prior_attempt_id":None,"kind":"manual_action","scope":context.scope,"reservation_id":snapshot["reservation_id"],"readiness_snapshot_id":snapshot["record_id"],"request_sha256":digest(intent,domain="request"),"host_identity_ref":context.identity_ref}
        action = {"record_type":"resource_action","schema_version":1,"record_id":record_id+":commit","created_at":now,"synthetic":context.synthetic,"producer_ref":context.actor_ref,"scope":context.scope,"reservation_id":snapshot["reservation_id"],"operation":"commit","expected_resource_revision":account["revision"],"quantities":dict.fromkeys(capacity.RESOURCES,0),"cash_event_id":None,"cash_delta_cents":None,"cash_state":"not_applicable","occurred_at":now,"work_period":None,"basis_claim_ids":[]}
        capacity.reduce_resources(events+[(len(events)+1,action)])
        _manual_attempts(events+[(len(events)+1,record)])
        # Both append calls share this writer transaction. A failure in either
        # rolls back both the resource transition and execution commitment.
        store._append(connection,read_record(action),request_key=request_key+":resource-commit")
        return store._append(connection,read_record(record),request_key=request_key)


def reconcile(path,record,*,context,request_key,verify_claim=None):
    record = read_record(record,expected_type="execution_attempt")
    if record["kind"] != "manual_action" or record["state"] not in {"outcome_unknown","confirmed_executed","failed_before_action"}:
        raise IntegrityError("Manual reconciliation must report unknown, executed, or proven pre-action failure")
    with store._connection(path,write=True) as connection:
        events = store._events(connection)
        now = utc_now()
        _context(context,events,action="manual_preflight",as_of=now,verify_claim=verify_claim)
        if (record["actor_ref"],record["scope"],record["synthetic"],record["host_identity_ref"]) != (context.actor_ref,context.scope,context.synthetic,context.identity_ref) or parse_timestamp(record["created_at"]) > parse_timestamp(now):
            raise IntegrityError("Reconciliation differs from authenticated actor/context")
        if connection.execute("SELECT 1 FROM request_receipts WHERE request_key=?",(request_key,)).fetchone():
            return store._append(connection,record,request_key=request_key)
        if record["state"] != "outcome_unknown":
            check = subject_claims(record["reconciliation_claim_ids"],record,as_of=now,resolve={item["record_id"]:item for _,item in events}.get,verify_claim=verify_claim)
            if check["state"] != "pass" or not {"intent_id","prior_attempt_id","state","occurred_at","decision_id","reservation_id","cost_cents","correlation_id"}.issubset(check["verified_fields"]):
                raise UnresolvedError("Terminal manual outcome requires reviewed exact execution evidence")
        _manual_attempts(events+[(len(events)+1,record)])
        return store._append(connection,record,request_key=request_key)
