import json
import asyncio
import logging
from uuid import UUID, uuid4
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Callable, Awaitable
from pydantic import BaseModel, Field, ValidationError
import websockets

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "1.0"

class AnatomyIpcEnvelope(BaseModel):
    protocol_version: str = PROTOCOL_VERSION
    message_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    correlation_id: UUID
    sequence_number: int
    geometry_version: int
    message_type: str
    payload: Dict[str, Any]
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class UnityIpcServer:
    def __init__(self, host: str, port: int, initial_geometry_version: int = 1):
        self.host = host
        self.port = port
        self.server = None
        self._handlers: Dict[str, Callable[[AnatomyIpcEnvelope], Awaitable[Optional[AnatomyIpcEnvelope]]]] = {}
        self._highest_seen_sequence: int = 0
        self._canonical_geometry_version: int = initial_geometry_version
        self._active_connections = set()

    def set_geometry_version(self, version: int):
        self._canonical_geometry_version = version

    def register_handler(self, message_type: str, handler: Callable[[AnatomyIpcEnvelope], Awaitable[Optional[AnatomyIpcEnvelope]]]):
        self._handlers[message_type] = handler

    async def start(self):
        self.server = await websockets.serve(self._handle_client, self.host, self.port)

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        for conn in list(self._active_connections):
            await conn.close()
        self._active_connections.clear()

    async def _handle_client(self, websocket):
        self._active_connections.add(websocket)
        self._highest_seen_sequence = 0  # Reset for new session reconnect
        connection_session_id = None
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    envelope = AnatomyIpcEnvelope(**data)
                except json.JSONDecodeError as e:
                    logger.warning(f"Malformed JSON rejected: {e}")
                    await websocket.send(json.dumps({"error": "malformed_json"}))
                    continue
                except ValidationError as e:
                    logger.warning(f"Malformed envelope rejected: {e}")
                    await websocket.send(json.dumps({"error": "malformed_envelope"}))
                    continue

                if envelope.protocol_version != PROTOCOL_VERSION:
                    logger.warning(f"Protocol version mismatch: {envelope.protocol_version}")
                    await websocket.send(json.dumps({"error": "protocol_version_mismatch"}))
                    continue

                if connection_session_id is None:
                    connection_session_id = envelope.session_id
                elif envelope.session_id != connection_session_id:
                    logger.warning(f"Session replay rejected: {envelope.session_id} != {connection_session_id}")
                    await websocket.send(json.dumps({"error": "session_mismatch"}))
                    continue
                
                if envelope.sequence_number <= self._highest_seen_sequence:
                    logger.warning(f"Stale/Duplicate sequence rejected: {envelope.sequence_number}")
                    await websocket.send(json.dumps({"error": "stale_sequence"}))
                    continue
                
                if envelope.geometry_version != self._canonical_geometry_version:
                    logger.warning(f"Stale geometry version rejected: {envelope.geometry_version} != {self._canonical_geometry_version}")
                    await websocket.send(json.dumps({"error": "stale_geometry_version"}))
                    continue

                self._highest_seen_sequence = envelope.sequence_number

                handler = self._handlers.get(envelope.message_type)
                if handler:
                    try:
                        response_envelope = await handler(envelope)
                        if response_envelope:
                            await websocket.send(response_envelope.model_dump_json())
                    except Exception as e:
                        logger.error(f"Handler error: {e}")
                        await websocket.send(json.dumps({"error": "handler_error"}))
                else:
                    logger.warning(f"No handler for message_type: {envelope.message_type}")
                    await websocket.send(json.dumps({"error": "unknown_message_type"}))

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._active_connections.discard(websocket)

class UnityIpcClient:
    """A mock client representing Unity for testing the transport boundary."""
    def __init__(self, uri: str):
        self.uri = uri
        self.websocket = None
        self._session_id = uuid4()
        self._sequence_number = 1
        self._geometry_version = 1

    def set_geometry_version(self, version: int):
        self._geometry_version = version

    async def connect(self):
        self.websocket = await websockets.connect(self.uri)

    async def disconnect(self):
        if self.websocket:
            await self.websocket.close()

    async def send_message(self, message_type: str, payload: Dict[str, Any], correlation_id: UUID) -> AnatomyIpcEnvelope:
        envelope = AnatomyIpcEnvelope(
            session_id=self._session_id,
            correlation_id=correlation_id,
            sequence_number=self._sequence_number,
            geometry_version=self._geometry_version,
            message_type=message_type,
            payload=payload
        )
        self._sequence_number += 1
        assert self.websocket is not None
        await self.websocket.send(envelope.model_dump_json())
        return envelope

    async def send_raw(self, data: str):
        assert self.websocket is not None
        await self.websocket.send(data)

    async def receive_response(self, timeout: float = 5.0, expected_correlation_id: Optional[UUID] = None) -> Any:
        assert self.websocket is not None
        message = await asyncio.wait_for(self.websocket.recv(), timeout=timeout)
        try:
            env = AnatomyIpcEnvelope.model_validate_json(message)
            if expected_correlation_id and env.correlation_id != expected_correlation_id:
                raise ValueError("correlation_mismatch")
            return env
        except ValidationError:
            return json.loads(message)
