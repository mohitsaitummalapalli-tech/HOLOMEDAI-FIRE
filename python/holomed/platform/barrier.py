import threading
from typing import Callable, TypeVar, Any

T = TypeVar("T")

class StaleCommitError(Exception):
    """Raised when a commit is attempted on a stale session generation."""
    pass

class SessionLifecycleGate:
    """
    Atomic lifecycle boundary for Holomed sessions.
    
    Protects a session from adversarial telemetry races during lifecycle transitions.
    """
    
    def __init__(self, session_id: str):
        self.session_id = session_id
        self._lock = threading.Lock()
        self._generation = 1
        self._is_active = True
        
    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._is_active
            
    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation
            
    def capture_active_generation(self) -> int | None:
        """Returns the current generation if active, otherwise None."""
        with self._lock:
            if not self._is_active:
                return None
            return self._generation

    def execute_commit(self, captured_generation: int, commit_func: Callable[[], list[tuple[str, dict[str, Any]]]]) -> list[tuple[str, dict[str, Any]]]:
        """
        Executes the commit function atomically if the generation matches.
        
        The commit_func MUST BE PURE. It must only mutate in-memory state
        and return a list of event tuples to emit (topic, payload).
        """
        with self._lock:
            if not self._is_active:
                raise StaleCommitError(f"Session {self.session_id} is no longer active.")
            if self._generation != captured_generation:
                raise StaleCommitError(f"Captured generation {captured_generation} is stale (current: {self._generation}).")
            
            return commit_func()
            
    def mark_terminated(self) -> None:
        """Marks the session as inactive atomically."""
        with self._lock:
            self._is_active = False
            self._generation += 1

