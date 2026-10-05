using System;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Net.WebSockets;
using UnityEngine;
// Dummy JSON classes for demonstration since we don't have a specific JSON library enforced
// Typically we would use Newtonsoft.Json or JsonUtility

namespace HoloMedAI.IPC
{
    [Serializable]
    public class AnatomyIpcEnvelope
    {
        public string protocol_version = "1.0";
        public string message_id;
        public string session_id;
        public string correlation_id;
        public int sequence_number;
        public int geometry_version;
        public string message_type;
        public string payload; // Typically parsed as a JObject or dictionary
        public string timestamp;
    }

    /// <summary>
    /// F1 Unity IPC Boundary: The real C# WebSocket client implementation.
    /// Manages the connection, sequence, and geometry version state against the Python canonical server.
    /// </summary>
    public class AnatomyIpcClient : MonoBehaviour
    {
        [SerializeField] private string serverUrl = "ws://127.0.0.1:8765";
        private ClientWebSocket webSocket;
        private CancellationTokenSource cancellationTokenSource;

        private string currentSessionId;
        private int sequenceNumber = 1;
        private int lastGeometryVersion = 1;

        public async Task ConnectAsync()
        {
            if (webSocket != null && webSocket.State == WebSocketState.Open) return;

            webSocket = new ClientWebSocket();
            cancellationTokenSource = new CancellationTokenSource();
            currentSessionId = Guid.NewGuid().ToString();
            sequenceNumber = 1; // Reset sequence on new session

            try
            {
                await webSocket.ConnectAsync(new Uri(serverUrl), cancellationTokenSource.Token);
                Debug.Log($"[HoloMedAI] Connected to Canonical Engine at {serverUrl} with Session {currentSessionId}");
                _ = ReceiveLoop();
            }
            catch (Exception ex)
            {
                Debug.LogError($"[HoloMedAI] Connection failed: {ex.Message}");
            }
        }

        public async Task DisconnectAsync()
        {
            if (webSocket != null)
            {
                cancellationTokenSource?.Cancel();
                if (webSocket.State == WebSocketState.Open || webSocket.State == WebSocketState.CloseReceived)
                {
                    await webSocket.CloseAsync(WebSocketCloseStatus.NormalClosure, "Client disconnecting", CancellationToken.None);
                }
                webSocket.Dispose();
                webSocket = null;
            }
        }

        public async Task SendMessageAsync(string messageType, string payloadJson, string correlationId)
        {
            if (webSocket == null || webSocket.State != WebSocketState.Open)
            {
                Debug.LogWarning("[HoloMedAI] Cannot send message, WebSocket not open.");
                return;
            }

            var envelope = new AnatomyIpcEnvelope
            {
                protocol_version = "1.0",
                message_id = Guid.NewGuid().ToString(),
                session_id = currentSessionId,
                correlation_id = correlationId,
                sequence_number = sequenceNumber++,
                geometry_version = lastGeometryVersion,
                message_type = messageType,
                payload = payloadJson,
                timestamp = DateTime.UtcNow.ToString("o")
            };

            string json = JsonUtility.ToJson(envelope); // Or Newtonsoft JsonConvert
            var buffer = Encoding.UTF8.GetBytes(json);

            await webSocket.SendAsync(new ArraySegment<byte>(buffer), WebSocketMessageType.Text, true, cancellationTokenSource.Token);
        }

        private async Task ReceiveLoop()
        {
            var buffer = new byte[1024 * 1024 * 5]; // 5MB buffer for mesh payloads
            
            while (webSocket.State == WebSocketState.Open && !cancellationTokenSource.IsCancellationRequested)
            {
                try
                {
                    var result = await webSocket.ReceiveAsync(new ArraySegment<byte>(buffer), cancellationTokenSource.Token);
                    if (result.MessageType == WebSocketMessageType.Close)
                    {
                        await DisconnectAsync();
                        break;
                    }

                    string jsonResponse = Encoding.UTF8.GetString(buffer, 0, result.Count);
                    // Decode Envelope
                    AnatomyIpcEnvelope envelope = JsonUtility.FromJson<AnatomyIpcEnvelope>(jsonResponse);
                    
                    if (envelope != null && envelope.geometry_version > lastGeometryVersion)
                    {
                        // Update to the new canonical geometry version authorized by Python
                        lastGeometryVersion = envelope.geometry_version;
                    }

                    HandleMessage(jsonResponse);
                }
                catch (OperationCanceledException)
                {
                    break;
                }
                catch (Exception ex)
                {
                    Debug.LogError($"[HoloMedAI] Receive error: {ex.Message}");
                    break;
                }
            }
        }

        private void HandleMessage(string json)
        {
            // Route message based on message_type to specific handlers
            // E.g., GeometryUpdateHandler, ErrorHandler
            Debug.Log($"[HoloMedAI] Received: {json}");

            // M49 Phase F: Minimal Visible Anatomy Response Integration
            if (json.Contains("\"message_type\":\"physical_command\"") && json.Contains("\"action\":\"sys.input.interact\""))
            {
                bool isPressed = json.Contains("\"state\":\"pressed\"");
                
                // Minimal visual response: Highlight the Heart GameObject
                GameObject heart = GameObject.Find("Heart");
                if (heart != null)
                {
                    Renderer renderer = heart.GetComponent<Renderer>();
                    if (renderer != null)
                    {
                        // Visually highlight when pinched/grasped, return to white when released
                        renderer.material.color = isPressed ? Color.red : Color.white;
                        Debug.Log($"[HoloMedAI] Anatomy Response: Heart highlighted {isPressed}");
                    }
                }
                else
                {
                    Debug.LogWarning("[HoloMedAI] Anatomy Response: Heart GameObject not found in scene.");
                }
            }
        }

        private void OnDestroy()
        {
            _ = DisconnectAsync();
        }
    }
}
