"""First Live Slice input data contracts and correlation schemas."""

import uuid
import time
from dataclasses import dataclass

def generate_correlation_id() -> str:
    """Generate a UUIDv7 for sequential correlation tracking.
    
    If uuid.uuid7 is natively available, it is used.
    Otherwise, a fallback sequential UUIDv4 structure is used for the live slice.
    """
    if hasattr(uuid, "uuid7"):
        return str(uuid.uuid7())
    # Fallback to a pseudo-sequential UUID if uuid7 is missing.
    ts_ms = int(time.time() * 1000)
    rand_uuid = uuid.uuid4()
    pseudo_v7_hex = f"{ts_ms:012x}" + rand_uuid.hex[12:]
    return str(uuid.UUID(pseudo_v7_hex))

@dataclass(frozen=True)
class RawVideoFrame:
    """Raw camera acquisition frame. Physical capture boundary."""
    capture_timestamp_ns: int
    frame_sequence: int
    data: bytes
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.frame_sequence < 0:
            raise ValueError("frame_sequence must be non-negative")
        if not isinstance(self.data, bytes):
            raise ValueError("data must be immutable bytes")

@dataclass(frozen=True)
class PerceptionObservation:
    """MediaPipe landmark extraction output. Marks the start of logical correlation."""
    capture_timestamp_ns: int
    perception_timestamp_ns: int
    frame_sequence: int  # Explicit mapping back to the raw captured frame
    correlation_id: str  # New logical interaction lineage ID starting here
    landmarks: tuple[tuple[float, float, float], ...]
    confidence: float

    def __post_init__(self) -> None:
        if self.frame_sequence < 0:
            raise ValueError("frame_sequence must be non-negative")
        if not self.correlation_id:
            raise ValueError("correlation_id must not be empty")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("confidence must be between 0.0 and 1.0")
        
        # Defensive recursive freezing for deeply nested structure
        try:
            frozen_landmarks = tuple(
                tuple(float(v) for v in lm) for lm in self.landmarks
            )
            for lm in frozen_landmarks:
                if len(lm) != 3:
                    raise ValueError("Each landmark must have exactly 3 dimensions")
        except (TypeError, ValueError) as e:
            raise ValueError(f"landmarks must be iterable of iterables of numeric types. {e}") from e
            
        object.__setattr__(self, "landmarks", frozen_landmarks)

@dataclass(frozen=True)
class UltronIntent:
    """Debounced intent mapping from perception."""
    correlation_id: str
    intent_timestamp_ns: int
    action: str
    value: float
    confidence: float

    def __post_init__(self) -> None:
        if not self.correlation_id:
            raise ValueError("correlation_id must not be empty")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("confidence must be between 0.0 and 1.0")
