"""SAA-90 + SAA-92: In-memory priority queue for escalated sessions.

Ordering: highest urgency_score first.
FIFO tie-breaking: earlier triggered_at wins when scores are equal.
"""
from __future__ import annotations

import heapq
import threading
from datetime import datetime
from typing import Iterator

from .snapshot import EscalationSnapshot


class EscalationQueue:
    """Thread-safe max-priority queue of EscalationSnapshots."""

    def __init__(self) -> None:
        self._heap: list[tuple] = []   # (neg_score, triggered_at, seq, snapshot)
        self._lock = threading.Lock()
        self._index: dict[str, EscalationSnapshot] = {}  # session_id → snapshot
        self._seq: int = 0  # monotonic counter to break ties without comparing snapshots

    def push(self, snapshot: EscalationSnapshot) -> None:
        """Add or replace a session in the queue."""
        with self._lock:
            self._index[snapshot.session_id] = snapshot
            heapq.heappush(
                self._heap,
                # negate score for max-heap; ISO timestamp + seq for FIFO tie-break
                (-snapshot.urgency_score, snapshot.triggered_at.isoformat(), self._seq, snapshot),
            )
            self._seq += 1

    def peek(self) -> EscalationSnapshot | None:
        """Return the highest-urgency snapshot without removing it."""
        with self._lock:
            self._prune()
            if self._heap:
                return self._heap[0][2]
            return None

    def pop(self) -> EscalationSnapshot | None:
        """Remove and return the highest-urgency snapshot."""
        with self._lock:
            self._prune()
            while self._heap:
                _, _, _, snapshot = heapq.heappop(self._heap)
                live = self._index.get(snapshot.session_id)
                if live is snapshot:
                    del self._index[snapshot.session_id]
                    return snapshot
            return None

    def remove(self, session_id: str) -> bool:
        """Remove a session from the queue (e.g. operator resolved it)."""
        with self._lock:
            if session_id in self._index:
                del self._index[session_id]
                return True
            return False

    def get(self, session_id: str) -> EscalationSnapshot | None:
        with self._lock:
            return self._index.get(session_id)

    def all_sorted(self) -> list[EscalationSnapshot]:
        """Return all live snapshots sorted by urgency (highest first)."""
        with self._lock:
            live = list(self._index.values())
        live.sort(key=lambda s: (-s.urgency_score, s.triggered_at))
        return live

    def __len__(self) -> int:
        with self._lock:
            return len(self._index)

    # ── internal ──────────────────────────────────────────────────────────────

    def _prune(self) -> None:
        """Discard heap entries whose session has been removed or replaced."""
        while self._heap:
            _, _, _, snapshot = self._heap[0]
            if self._index.get(snapshot.session_id) is snapshot:
                break
            heapq.heappop(self._heap)


# Module-level singleton
_queue = EscalationQueue()


def get_escalation_queue() -> EscalationQueue:
    return _queue
