"""Stable errors for operational callers, separate from evaluator errors."""


class OperationsError(ValueError):
    code = "operations_error"

    def __init__(self, message, *, path=""):
        super().__init__(message)
        self.path = path

    def as_dict(self):
        return {"code": self.code, "path": self.path, "message": str(self)}


class ContractError(OperationsError):
    code = "invalid_contract"


class IntegrityError(OperationsError):
    code = "integrity_conflict"


class UnresolvedError(OperationsError):
    code = "unresolved_prerequisite"


class ConflictError(IntegrityError):
    code = "idempotency_conflict"


class StoreError(OperationsError):
    code = "store_unavailable"


class BusyError(StoreError):
    code = "store_busy"


class VersionError(StoreError):
    code = "unsupported_store_version"
