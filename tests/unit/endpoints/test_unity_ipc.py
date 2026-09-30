import pytest
import asyncio
import json
from uuid import uuid4
import websockets
from holomed.devices.endpoints.unity_ipc import UnityIpcServer, UnityIpcClient, AnatomyIpcEnvelope, PROTOCOL_VERSION

import pytest_asyncio

@pytest_asyncio.fixture
async def ipc_server(unused_tcp_port):
    server = UnityIpcServer("127.0.0.1", unused_tcp_port)
    
    async def echo_handler(envelope: AnatomyIpcEnvelope):
        return AnatomyIpcEnvelope(
            session_id=envelope.session_id,
            correlation_id=envelope.correlation_id,
            sequence_number=envelope.sequence_number,
            geometry_version=envelope.geometry_version,
            message_type=f"{envelope.message_type}_ack",
            payload={"status": "received"}
        )
        
    server.register_handler("test_msg", echo_handler)
    
    await server.start()
    yield server
    await server.stop()

@pytest_asyncio.fixture
async def ipc_client(ipc_server):
    client = UnityIpcClient(f"ws://127.0.0.1:{ipc_server.port}")
    await client.connect()
    yield client
    await client.disconnect()

@pytest.mark.asyncio
async def test_a_valid_envelope(ipc_client):
    correlation_id = uuid4()
    await ipc_client.send_message("test_msg", {"data": 123}, correlation_id)
    response = await ipc_client.receive_response()
    
    assert isinstance(response, AnatomyIpcEnvelope)
    assert response.message_type == "test_msg_ack"
    assert response.correlation_id == correlation_id
    assert response.payload["status"] == "received"

@pytest.mark.asyncio
async def test_b_wrong_protocol_version(ipc_client):
    env = AnatomyIpcEnvelope(
        protocol_version="99.0",
        session_id=uuid4(),
        correlation_id=uuid4(),
        sequence_number=99,
        geometry_version=1,
        message_type="test_msg",
        payload={}
    )
    await ipc_client.send_raw(env.model_dump_json())
    response = await ipc_client.receive_response()
    assert isinstance(response, dict)
    assert response.get("error") == "protocol_version_mismatch"

@pytest.mark.asyncio
async def test_c_malformed_json(ipc_client):
    await ipc_client.send_raw("{ bad_json: true ")
    response = await ipc_client.receive_response()
    assert isinstance(response, dict)
    assert response.get("error") == "malformed_json"

@pytest.mark.asyncio
async def test_d_missing_required_field(ipc_client):
    data = {
        "message_type": "test_msg",
        "payload": {}
    }
    await ipc_client.send_raw(json.dumps(data))
    response = await ipc_client.receive_response()
    assert isinstance(response, dict)
    assert response.get("error") == "malformed_envelope"

@pytest.mark.asyncio
async def test_e_duplicate_message(ipc_client):
    env = await ipc_client.send_message("test_msg", {"data": 1}, uuid4())
    ack = await ipc_client.receive_response()
    assert isinstance(ack, AnatomyIpcEnvelope)
    
    await ipc_client.send_raw(env.model_dump_json())
    response = await ipc_client.receive_response()
    assert isinstance(response, dict)
    assert response.get("error") == "stale_sequence"

@pytest.mark.asyncio
async def test_f_stale_sequence(ipc_client):
    ipc_client._sequence_number = 5
    await ipc_client.send_message("test_msg", {}, uuid4())
    await ipc_client.receive_response()
    
    ipc_client._sequence_number = 4
    await ipc_client.send_message("test_msg", {}, uuid4())
    response = await ipc_client.receive_response()
    assert isinstance(response, dict)
    assert response.get("error") == "stale_sequence"

@pytest.mark.asyncio
async def test_g_stale_geometry_version(ipc_server, ipc_client):
    ipc_server.set_geometry_version(5)
    
    ipc_client.set_geometry_version(1)
    await ipc_client.send_message("test_msg", {}, uuid4())
    
    response = await ipc_client.receive_response()
    assert isinstance(response, dict)
    assert response.get("error") == "stale_geometry_version"

@pytest.mark.asyncio
async def test_h_correlation_mismatch(ipc_client):
    correlation_id = uuid4()
    await ipc_client.send_message("test_msg", {}, correlation_id)
    wrong_id = uuid4()
    with pytest.raises(ValueError, match="correlation_mismatch"):
        await ipc_client.receive_response(expected_correlation_id=wrong_id)

@pytest.mark.asyncio
async def test_i_timeout(ipc_server, ipc_client):
    async def slow_handler(env):
        await asyncio.sleep(0.5)
        return env
    ipc_server.register_handler("slow", slow_handler)
    
    await ipc_client.send_message("slow", {}, uuid4())
    with pytest.raises(asyncio.TimeoutError):
        await ipc_client.receive_response(timeout=0.1)

@pytest.mark.asyncio
async def test_j_disconnect_reconnect_k(ipc_server):
    client1 = UnityIpcClient(f"ws://127.0.0.1:{ipc_server.port}")
    await client1.connect()
    
    await client1.send_message("test_msg", {}, uuid4())
    resp1 = await client1.receive_response()
    assert resp1.message_type == "test_msg_ack"
    
    await client1.disconnect()
    
    client2 = UnityIpcClient(f"ws://127.0.0.1:{ipc_server.port}")
    await client2.connect()
    await client2.send_message("test_msg", {}, uuid4())
    resp2 = await client2.receive_response()
    assert resp2.message_type == "test_msg_ack"
    await client2.disconnect()

@pytest.mark.asyncio
async def test_l_clean_shutdown(unused_tcp_port):
    server = UnityIpcServer("127.0.0.1", unused_tcp_port)
    await server.start()
    
    client = UnityIpcClient(f"ws://127.0.0.1:{unused_tcp_port}")
    await client.connect()
    
    await server.stop()
    
    with pytest.raises(websockets.exceptions.ConnectionClosed):
        await client.send_message("test_msg", {}, uuid4())
        await client.receive_response()

@pytest.mark.asyncio
async def test_performance_latency(ipc_client):
    import time
    import statistics
    
    latencies_ms = []
    
    # Warmup
    for _ in range(5):
        await ipc_client.send_message("test_msg", {}, uuid4())
        await ipc_client.receive_response()
        
    for _ in range(100):
        start = time.perf_counter()
        await ipc_client.send_message("test_msg", {}, uuid4())
        await ipc_client.receive_response()
        end = time.perf_counter()
        latencies_ms.append((end - start) * 1000)
        
    min_lat = min(latencies_ms)
    median_lat = statistics.median(latencies_ms)
    p95_lat = sorted(latencies_ms)[int(len(latencies_ms)*0.95)]
    
    print(f"\\nPerformance [100 samples]: Min: {min_lat:.3f}ms | Median: {median_lat:.3f}ms | P95: {p95_lat:.3f}ms")
    
    # CI latency bound is generous to avoid flakes, but real measured local target is < 5ms
    assert median_lat < 50.0

@pytest.mark.asyncio
async def test_p_csharp_interop_contract():
    # Verify the JSON payload exactly matches the fields in AnatomyIpcClient.cs
    env = AnatomyIpcEnvelope(
        session_id=uuid4(),
        correlation_id=uuid4(),
        sequence_number=1,
        geometry_version=1,
        message_type="test",
        payload={"data": 123}
    )
    json_str = env.model_dump_json()
    data = json.loads(json_str)
    
    expected_fields = {
        "protocol_version", "message_id", "session_id", "correlation_id",
        "sequence_number", "geometry_version", "message_type", "payload", "timestamp"
    }
    
    assert set(data.keys()) == expected_fields, f"Missing or extra fields in JSON mapping: {data.keys()}"

@pytest.mark.asyncio
async def test_m_old_session_replay(ipc_client):
    # Valid first message establishes session
    await ipc_client.send_message("test_msg", {}, uuid4())
    await ipc_client.receive_response()
    
    # Try sending with a different session ID
    env = AnatomyIpcEnvelope(
        session_id=uuid4(), # spoofed/replayed session
        correlation_id=uuid4(),
        sequence_number=999,
        geometry_version=1,
        message_type="test_msg",
        payload={}
    )
    await ipc_client.send_raw(env.model_dump_json())
    response = await ipc_client.receive_response()
    assert isinstance(response, dict)
    assert response.get("error") == "session_mismatch"

@pytest.mark.asyncio
async def test_n_geometry_version_authority_spoof(ipc_server, ipc_client):
    ipc_server.set_geometry_version(1)
    
    # Unity attempts to spoof canonical geometry_version to N+1
    ipc_client.set_geometry_version(2)
    await ipc_client.send_message("test_msg", {}, uuid4())
    
    response = await ipc_client.receive_response()
    assert isinstance(response, dict)
    assert response.get("error") == "stale_geometry_version"

@pytest.mark.asyncio
async def test_o_authority_via_ipc_attempt():
    # IPC Envelopes must inherently lack authority fields (no execution capabilities)
    env = AnatomyIpcEnvelope(
        session_id=uuid4(),
        correlation_id=uuid4(),
        sequence_number=1,
        geometry_version=1,
        message_type="slice_request",
        payload={"command_nonce": "xyz", "capability": "fake"}
    )
    # The envelope itself does not contain capability or admission fields at the top level
    assert not hasattr(env, "capability")
    assert not hasattr(env, "execution_signature")
    assert not hasattr(env, "admission_secret")
    # Authority is NOT resolved here.
