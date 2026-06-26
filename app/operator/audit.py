"""SAA-98: In-memory audit trail for operator actions."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class OperatorAction(str, Enum):
    TAKEOVER       = "takeover"
    RETURN_TO_AGENT = "return_to_agent"
    HANGUP         = "hangup"
    NOTE           = "note"


@dataclass
class AuditEntry:
    session_id: str
    operator_id: str
    action: OperatorAction
    timestamp: datetime = field(default_factory=datetime.now)
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "session_id":  self.session_id,
            "operator_id": self.operator_id,
            "action":      self.action.value,
            "timestamp":   self.timestamp.isoformat(),
            "detail":      self.detail,
        }


class AuditLog:
    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []
        self._lock = threading.Lock()

    def record(
        self,
        session_id: str,
        operator_id: str,
        action: OperatorAction,
        detail: str = "",
    ) -> AuditEntry:
        entry = AuditEntry(session_id=session_id, operator_id=operator_id,
                           action=action, detail=detail)
        with self._lock:
            self._entries.append(entry)
        return entry

    def get_all(self) -> list[AuditEntry]:
        with self._lock:
            return list(self._entries)

    def get_for_session(self, session_id: str) -> list[AuditEntry]:
        with self._lock:
            return [e for e in self._entries if e.session_id == session_id]


_audit_log = AuditLog()


def get_audit_log() -> AuditLog:
    return _audit_log
