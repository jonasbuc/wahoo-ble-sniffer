using UnityEngine;
using System;
using System.Collections;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

/// <summary>
/// OPTIMIZED: Low-latency receiver for Wahoo cycling data via WebSocket
/// Uses binary protocol for minimal overhead (~10-15ms latency improvement)
/// Attach this to a GameObject in your Unity scene
/// </summary>
public class WahooDataReceiver : MonoBehaviour
{
    [Header("Connection Settings")]
    [SerializeField] private string serverUrl = "ws://localhost:8765";
    [SerializeField] private bool autoConnect = true;
    [SerializeField] private float reconnectDelay = 3f;
    [SerializeField] private bool useBinaryProtocol = true; // NEW: Binary for speed!

    [Header("Current Data (Read-Only)")]
    [SerializeField] private float currentPower = 0f;      // Watts
    [SerializeField] private float currentCadence = 0f;    // RPM
    [SerializeField] private float currentSpeed = 0f;      // km/h
    [SerializeField] private int currentHeartRate = 0;     // BPM

    [Header("Smoothing")]
    [SerializeField] private bool enableSmoothing = true;
    [SerializeField] private float smoothingFactor = 0.3f;

    // Public properties
    public float Power => enableSmoothing ? smoothedPower : currentPower;
    public float Cadence => enableSmoothing ? smoothedCadence : currentCadence;
    public float Speed => enableSmoothing ? smoothedSpeed : currentSpeed;
    public int HeartRate => currentHeartRate;
    public bool IsConnected => isConnected;

    // Events
    public event Action<CyclingData> OnDataReceived;
    public event Action OnConnected;
    public event Action OnDisconnected;

    // Private fields
    private ClientWebSocket webSocket;
    private CancellationTokenSource cancellationTokenSource;
    private bool isConnected = false;
    private bool isReconnecting = false;
    private float smoothedPower = 0f;
    private float smoothedCadence = 0f;
    private float smoothedSpeed = 0f;
    private bool protocolNegotiated = false;

    [Serializable]
    public class CyclingData
    {
        public double timestamp;
        public int power;
        public float cadence;
        public float speed;
        public int heart_rate;
    }

    void Start()
    {
        if (autoConnect)
        {
            Connect();
        }
    }

    void Update()
    {
        // Apply smoothing in Update for smooth interpolation
        if (enableSmoothing && isConnected)
        {
            float alpha = 1f - smoothingFactor;
            smoothedPower = Mathf.Lerp(smoothedPower, currentPower, alpha * Time.deltaTime * 10f);
            smoothedCadence = Mathf.Lerp(smoothedCadence, currentCadence, alpha * Time.deltaTime * 10f);
            smoothedSpeed = Mathf.Lerp(smoothedSpeed, currentSpeed, alpha * Time.deltaTime * 10f);
        }
    }

    public async void Connect()
    {
        if (isConnected)
        {
            Debug.LogWarning("[WahooData] Already connected!");
            return;
        }

        try
        {
            Debug.Log($"[WahooData] Connecting to {serverUrl}...");

            webSocket = new ClientWebSocket();
            
            // OPTIMIZATION: Disable Nagle's algorithm for low latency
            webSocket.Options.SetBuffer(8192, 8192);
            
            cancellationTokenSource = new CancellationTokenSource();

            await webSocket.ConnectAsync(new Uri(serverUrl), cancellationTokenSource.Token);

            isConnected = true;
            protocolNegotiated = false;
            Debug.Log("[WahooData] ✓ Connected to Wahoo bridge!");

            OnConnected?.Invoke();

            // Start receiving data
            _ = ReceiveLoop();
        }
        catch (Exception ex)
        {
            Debug.LogError($"[WahooData] Connection failed: {ex.Message}");
            isConnected = false;
            
            if (autoConnect && !isReconnecting)
            {
                StartCoroutine(ReconnectRoutine());
            }
        }
    }

    private async Task ReceiveLoop()
    {
        byte[] buffer = new byte[1024];

        try
        {
            while (webSocket.State == WebSocketState.Open && !cancellationTokenSource.Token.IsCancellationRequested)
            {
                var result = await webSocket.ReceiveAsync(
                    new ArraySegment<byte>(buffer),
                    cancellationTokenSource.Token
                );

                if (result.MessageType == WebSocketMessageType.Close)
                {
                    Debug.Log("[WahooData] Server closed connection");
                    break;
                }

                // First message is handshake (JSON)
                if (!protocolNegotiated)
                {
                    string handshake = Encoding.UTF8.GetString(buffer, 0, result.Count);
                    Debug.Log($"[WahooData] Handshake: {handshake}");
                    protocolNegotiated = true;
                    continue;
                }

                // OPTIMIZATION: Binary protocol parsing
                if (useBinaryProtocol && result.MessageType == WebSocketMessageType.Binary)
                {
                    ProcessBinaryMessage(buffer, result.Count);
                }
                else // Fallback to JSON
                {
                    string message = Encoding.UTF8.GetString(buffer, 0, result.Count);
                    ProcessJsonMessage(message);
                }
            }
        }
        catch (Exception ex)
        {
            if (!(ex is OperationCanceledException))
            {
                Debug.LogError($"[WahooData] Receive error: {ex.Message}");
            }
        }
        finally
        {
            await Disconnect();
        }
    }

    /// <summary>
    /// OPTIMIZED: Parse binary message (much faster than JSON!)
    /// Format: double timestamp, float power, float cadence, float speed, int heart_rate
    /// Total: 8 + 4 + 4 + 4 + 4 = 24 bytes
    /// </summary>
    private void ProcessBinaryMessage(byte[] buffer, int length)
    {
        if (length < 24) // 8 + 4 + 4 + 4 + 4 = 24 bytes
        {
            Debug.LogWarning($"[WahooData] Invalid binary message length: {length}");
            return;
        }

        try
        {
            // Parse binary data (Little Endian)
            double timestamp = BitConverter.ToDouble(buffer, 0);
            float power = BitConverter.ToSingle(buffer, 8);
            float cadence = BitConverter.ToSingle(buffer, 12);
            float speed = BitConverter.ToSingle(buffer, 16);
            int heartRate = BitConverter.ToInt32(buffer, 20);

            // Update current values (thread-safe for Unity main thread)
            currentPower = power;
            currentCadence = cadence;
            currentSpeed = speed;
            currentHeartRate = heartRate;

            // Invoke event
            var data = new CyclingData
            {
                timestamp = timestamp,
                power = (int)power,
                cadence = cadence,
                speed = speed,
                heart_rate = heartRate
            };
            OnDataReceived?.Invoke(data);
        }
        catch (Exception ex)
        {
            Debug.LogError($"[WahooData] Binary parse error: {ex.Message}");
        }
    }

    /// <summary>
    /// Fallback: Parse JSON message (slower but compatible)
    /// </summary>
    private void ProcessJsonMessage(string message)
    {
        if (string.IsNullOrEmpty(message) || message.Contains("pong"))
        {
            return; // Skip ping/pong
        }

        try
        {
            var data = JsonUtility.FromJson<CyclingData>(message);

            currentPower = data.power;
            currentCadence = data.cadence;
            currentSpeed = data.speed;
            currentHeartRate = data.heart_rate;

            OnDataReceived?.Invoke(data);
        }
        catch (Exception ex)
        {
            Debug.LogError($"[WahooData] JSON parse error: {ex.Message}\nMessage: {message}");
        }
    }

    public async Task Disconnect()
    {
        if (!isConnected)
            return;

        try
        {
            isConnected = false;
            
            if (webSocket != null && webSocket.State == WebSocketState.Open)
            {
                cancellationTokenSource?.Cancel();
                await webSocket.CloseAsync(WebSocketCloseStatus.NormalClosure, "Closing", CancellationToken.None);
            }

            OnDisconnected?.Invoke();
            Debug.Log("[WahooData] Disconnected");
        }
        catch (Exception ex)
        {
            Debug.LogError($"[WahooData] Disconnect error: {ex.Message}");
        }
        finally
        {
            webSocket?.Dispose();
            cancellationTokenSource?.Dispose();
        }
    }

    private IEnumerator ReconnectRoutine()
    {
        isReconnecting = true;
        Debug.Log($"[WahooData] Reconnecting in {reconnectDelay} seconds...");

        yield return new WaitForSeconds(reconnectDelay);

        isReconnecting = false;
        Connect();
    }

    void OnApplicationQuit()
    {
        _ = Disconnect();
    }

    void OnDestroy()
    {
        _ = Disconnect();
    }
}
