from __future__ import annotations

"""
In-memory session stitching.

Rules (per user request):
- Cosine similarity with threshold 0.62 (configurable)
- One session per person per video
- Session created on ENTRY, closed on EXIT
- Track zone visits for analytics

We maintain:
- A global person identity id (int) with a representative ReID embedding ("gallery").
- A session record keyed by that global person id, created on ENTRY.
- Zone visit tracking per session.
"""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from app.utils import cosine_similarity, l2_normalize


@dataclass
class ZoneVisit:
    """Records a visit to a zone."""
    zone_name: str
    entry_time: float
    exit_time: Optional[float] = None


@dataclass
class Session:
    session_id: str
    entry_time: Optional[float] = None
    exit_time: Optional[float] = None
    events: List[str] = field(default_factory=list)
    last_seen_time: Optional[float] = None  # Track when person was last detected in feed
    zone_visits: List[ZoneVisit] = field(default_factory=list)  # Track visits to zones

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "entry_time": self.entry_time,
            "exit_time": self.exit_time,
            "events": list(self.events),
            "zone_visits": [
                {
                    "zone_name": zv.zone_name,
                    "entry_time": zv.entry_time,
                    "exit_time": zv.exit_time,
                }
                for zv in self.zone_visits
            ],
        }


class SessionManager:
    def __init__(self, reid_cosine_threshold: float = 0.62):
        self.thr = float(reid_cosine_threshold)

        self._next_person_id = 1
        self._next_session_num = 1

        # Global identity gallery
        self._person_embedding: Dict[int, np.ndarray] = {}

        # Mapping from tracker track_id -> global person id (helps keep stable within a run)
        self._track_to_person: Dict[int, int] = {}

        # Session records keyed by person id (one per person per video)
        self._sessions: Dict[int, Session] = {}
        
        # Track last seen time for each person (for auto-inactive detection)
        self._person_last_seen: Dict[int, float] = {}
        
        # Timeout for marking sessions inactive if person not seen (in seconds)
        self._inactive_timeout = 5.0  # 5 seconds

    def assign_identity(self, track_id: int, embedding: np.ndarray) -> int:
        """
        Assigns a global person id for this track.

        Strategy (MVP-friendly):
        - If this track_id already mapped -> reuse
        - Else, match embedding to existing gallery by cosine similarity
        - If best >= threshold -> reuse that person id
        - Else, create new person id
        """
        current_time = time.time()
        
        if track_id in self._track_to_person:
            pid = self._track_to_person[track_id]
            # Update gallery embedding with a simple running blend (keeps representation fresh)
            self._update_gallery(pid, embedding)
            # Update last seen time
            self._person_last_seen[pid] = current_time
            # Update session last seen time
            if pid in self._sessions:
                self._sessions[pid].last_seen_time = current_time
            return pid

        emb = l2_normalize(np.asarray(embedding, dtype=np.float32).reshape(-1))
        if len(self._person_embedding) == 0:
            pid = self._create_person(emb)
            self._track_to_person[track_id] = pid
            return pid

        best_pid = None
        best_sim = -1.0
        for pid, g in self._person_embedding.items():
            sim = cosine_similarity(emb, g)
            if sim > best_sim:
                best_sim = sim
                best_pid = pid

        if best_pid is not None and best_sim >= self.thr:
            self._track_to_person[track_id] = best_pid
            self._update_gallery(best_pid, emb)
            return best_pid

        pid = self._create_person(emb)
        self._track_to_person[track_id] = pid
        # Set initial last seen time
        self._person_last_seen[pid] = current_time
        return pid

    def on_entry(self, person_id: int) -> None:
        """Create a session record if this person doesn't have one yet."""
        current_time = time.time()
        if person_id not in self._sessions:
            session_id = f"CUST_{self._next_session_num:03d}"
            self._next_session_num += 1
            self._sessions[person_id] = Session(session_id=session_id, last_seen_time=current_time)

        s = self._sessions[person_id]
        if s.entry_time is None:
            s.entry_time = current_time
        s.last_seen_time = current_time
        s.events.append("ENTRY")

    def on_exit(self, person_id: int) -> None:
        """Close session if it exists and isn't already closed."""
        if person_id not in self._sessions:
            return
        s = self._sessions[person_id]
        if s.exit_time is None:
            s.exit_time = time.time()
        s.events.append("EXIT")

    def get_session_id(self, person_id: int) -> Optional[str]:
        """
        Get the session ID for a person if it exists.
        IMPORTANT (per user requirement):
        - Sessions should start ONLY when the person crosses an ENTRY line.
        - That means sessions are created in `on_entry`, not here.
        """
        current_time = time.time()
        s = self._sessions.get(person_id)
        if s is None:
            # Person has not triggered ENTRY yet → no customer session
            return None

        # Keep last_seen_time fresh for active sessions and ensure entry_time is set.
        if s.entry_time is None:
            s.entry_time = current_time
        s.last_seen_time = current_time
        return s.session_id

    def mark_inactive_if_not_seen(self) -> None:
        """
        Automatically mark sessions as inactive if the person hasn't been seen
        in the feed for a while (even if they didn't cross exit line).
        """
        current_time = time.time()
        for pid, s in self._sessions.items():
            if s.exit_time is None:  # Only check active sessions
                last_seen = self._person_last_seen.get(pid)
                if last_seen is None:
                    # If never seen, use entry_time as fallback
                    last_seen = s.entry_time or s.last_seen_time
                
                if last_seen is not None:
                    time_since_seen = current_time - last_seen
                    if time_since_seen > self._inactive_timeout:
                        # Person hasn't been seen for timeout period, mark as inactive
                        if s.exit_time is None:
                            s.exit_time = last_seen  # Set exit time to when they were last seen
                            s.events.append("AUTO_EXIT")
    
    def active_sessions(self) -> Dict[int, Session]:
        """
        Returns sessions that have ENTRY but not EXIT.
        Also checks if person hasn't been seen recently and excludes them.
        """
        # First, mark inactive any sessions where person hasn't been seen
        self.mark_inactive_if_not_seen()
        
        out = {}
        current_time = time.time()
        for pid, s in self._sessions.items():
            if s.entry_time is not None and s.exit_time is None:
                # Check if person was seen recently
                last_seen = self._person_last_seen.get(pid) or s.last_seen_time
                if last_seen is not None:
                    time_since_seen = current_time - last_seen
                    if time_since_seen <= self._inactive_timeout:
                        # Person was seen recently, include in active sessions
                        out[pid] = s
                else:
                    # No last seen time, but session exists - include it
                    out[pid] = s
        return out

    def all_sessions(self) -> List[dict]:
        """Returns all sessions as dicts (for printing/logging)."""
        return [s.to_dict() for s in self._sessions.values()]

    def _create_person(self, emb: np.ndarray) -> int:
        pid = self._next_person_id
        self._next_person_id += 1
        self._person_embedding[pid] = l2_normalize(emb)
        return pid

    def on_zone_entry(self, person_id: int, zone_name: str) -> None:
        """Record that a person entered a zone."""
        if person_id not in self._sessions:
            return
        s = self._sessions[person_id]
        current_time = time.time()
        
        # Check if already in this zone (avoid duplicates)
        for zv in s.zone_visits:
            if zv.zone_name == zone_name and zv.exit_time is None:
                return  # Already in this zone
        
        # Create new zone visit
        s.zone_visits.append(ZoneVisit(zone_name=zone_name, entry_time=current_time))
    
    def on_zone_exit(self, person_id: int, zone_name: str) -> None:
        """Record that a person exited a zone."""
        if person_id not in self._sessions:
            return
        s = self._sessions[person_id]
        current_time = time.time()
        
        # Find the most recent open visit to this zone
        for zv in reversed(s.zone_visits):
            if zv.zone_name == zone_name and zv.exit_time is None:
                zv.exit_time = current_time
                return

    def _update_gallery(self, pid: int, emb: np.ndarray) -> None:
        """
        Simple exponential moving average to stabilize identity vector.
        """
        if pid not in self._person_embedding:
            self._person_embedding[pid] = l2_normalize(emb)
            return
        g = self._person_embedding[pid]
        blended = 0.8 * g + 0.2 * l2_normalize(emb)
        self._person_embedding[pid] = l2_normalize(blended)


