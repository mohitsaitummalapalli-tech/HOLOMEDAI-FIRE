# -*- coding: utf-8 -*-
"""Deterministic Length-Prefixed Wire Framing for M11 Gateway."""

from __future__ import annotations

import struct
from typing import List

from holomed.gateway.exceptions import GatewayFramingError
from holomed.gateway.models import MAX_FRAME_BYTES

HEADER_STRUCT = struct.Struct(">I")
HEADER_SIZE = HEADER_STRUCT.size  # Exactly 4 bytes


def encode_frame(payload_bytes: bytes) -> bytes:
    """Encode payload bytes with 4-byte unsigned big-endian length prefix."""
    if not isinstance(payload_bytes, bytes):
        raise GatewayFramingError(f"Payload must be bytes, got {type(payload_bytes).__name__}")
    
    length = len(payload_bytes)
    if length == 0:
        raise GatewayFramingError("Zero-length frame is forbidden")
    if length > MAX_FRAME_BYTES:
        raise GatewayFramingError(
            f"Payload length ({length} bytes) exceeds MAX_FRAME_BYTES ({MAX_FRAME_BYTES} bytes)"
        )

    return HEADER_STRUCT.pack(length) + payload_bytes


class FrameParser:
    """Incremental chunked frame parser with pre-allocation size enforcement."""

    def __init__(self, max_frame_bytes: int = MAX_FRAME_BYTES) -> None:
        self._max_frame_bytes = max_frame_bytes
        self._buffer: bytearray = bytearray()

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    def feed(self, chunk: bytes) -> List[bytes]:
        """Feed raw incoming bytes and return any fully reassembled frames."""
        if not isinstance(chunk, (bytes, bytearray)):
            raise GatewayFramingError(f"Expected bytes chunk, got {type(chunk).__name__}")

        self._buffer.extend(chunk)
        frames: List[bytes] = []

        while len(self._buffer) >= HEADER_SIZE:
            # Inspect 4-byte length prefix BEFORE allocating payload memory
            declared_length: int = HEADER_STRUCT.unpack_from(self._buffer, 0)[0]

            if declared_length == 0:
                self.reset()
                raise GatewayFramingError("Zero-length frame is forbidden")

            if declared_length > self._max_frame_bytes:
                self.reset()
                raise GatewayFramingError(
                    f"Frame length ({declared_length} bytes) exceeds MAX_FRAME_BYTES ({self._max_frame_bytes} bytes)"
                )

            total_needed = HEADER_SIZE + declared_length
            if len(self._buffer) < total_needed:
                # Incomplete frame; await additional chunks
                break

            # Extract payload bytes and slice buffer
            payload = bytes(self._buffer[HEADER_SIZE:total_needed])
            del self._buffer[:total_needed]
            frames.append(payload)

        return frames

    def reset(self) -> None:
        """Clear parser buffer on connection error or disconnect."""
        self._buffer.clear()
