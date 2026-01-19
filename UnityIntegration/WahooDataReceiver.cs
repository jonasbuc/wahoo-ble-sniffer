using UnityEngine;
using System;
using System.Collections;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

/// <summary>
/// Receives live cycling data from Wahoo devices via WebSocket
/// Uses Unity's built-in .NET WebSocket (no external dependencies!)
/// Attach this to a GameObject in your Unity scene
/// </summary>
public class WahooDataReceiver : MonoBehaviour
{
    [Header("Connection Settings")]
    [SerializeField] private string serverUrl = "ws://localhost:8765";
    [SerializeField] private bool autoConnect = true;
    [SerializeField] private float reconnectDelay = 3f;

    [Header("Current Data (Read-Only)")]
    [SerializeField] private float currentPower = 0f;      // Watts
    [SerializeField] private float currentCadence = 0f;    // RPM
    [SerializeField] private float currentSpeed = 0f;      // km/h
    [SerializeField] private int currentHeartRate = 0;     // BPM

    [Header("Smoothing")]
    [SerializeField] private bool enableSmoothing = true;
    [SerializeField] private float smoothingFactor = 0.3f; // 0 = no smoothing, 1 = max smoothing

    // Public properties for bike controller
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
            cancellationTokenSource = new CancellationTokenSource();

            await webSocket.ConnectAsync(new Uri(serverUrl), cancellationTokenSource.Token);

            isConnected = true;
            Debug.Log("[WahooData] ✓ Connected to Wahoo bridge!");
            OnConnected?.Invoke();

            // Start receiving messages
            _ = ReceiveLoop();
        }
        catch (Exception e)
        {
            Debug.LogError($"[WahooData] Connection failed: {e.Message}");
            isConnected = false;

            if (!isReconnecting && gameObject.activeInHierarchy)
            {
                StartCoroutine(ReconnectAfterDelay());
            }
        }
    }

    public async void Disconnect()
    {
        isReconnecting = false;
        StopAllCoroutines();

        if (webSocket != null && isConnected)
        {
            try
            {
                cancellationTokenSource?.Cancel();
                await webSocket.CloseAsync(WebSocketCloseStatus.NormalClosure, "Client closing", CancellationToken.None);
            }
            catch (Exception e)
            {
                Debug.LogWarning($"[WahooData] Error during disconnect: {e.Message}");
            }
            finally
            {
                webSocket?.Dispose();
                webSocket = null;
                isConnected = false;
            }
        }

        Debug.Log("[WahooData] Disconnected");
    }

    private async Task ReceiveLoop()
    {
        var buffer = new byte[4096];

        try
        {
            while (webSocket.State == WebSocketState.Open && !cancellationTokenSource.Token.IsCancellationRequested)
            {
                var result = await webSocket.ReceiveAsync(new ArraySegment<byte>(buffer), cancellationTokenSource.Token);

                if (result.MessageType == WebSocketMessageType.Text)
                {
                    var message = Encoding.UTF8.GetString(buffer, 0, result.Count);
                    ProcessMessage(message);
                }
                else if (result.MessageType == WebSocketMessageType.Close)
                {
                    Debug.LogWarning("[WahooData] Server closed connection");
                    break;
                }
            }
        }
        catch (OperationCanceledException)
        {
            Debug.Log("[WahooData] Receive loop cancelled");
        }
        catch (Exception e)
        {
            Debug.LogError($"[WahooData] Receive error: {e.Message}");
        }
        finally
        {
            if (isConnected)
            {
                isConnected = false;
                OnDisconnected?.Invoke();

                if (!isReconnecting && gameObject.activeInHierarchy)
                {
                    StartCoroutine(ReconnectAfterDelay());
                }
            }
        }
    }

    private IEnumerator ReconnectAfterDelay()
    {
        isReconnecting = true;
        Debug.Log($"[WahooData] Reconnecting in {reconnectDelay} seconds...");

        yield return new WaitForSeconds(reconnectDelay);

        if (gameObject.activeInHierarchy)
        {
            isReconnecting = false;
            Connect();
        }
    }

    private void ProcessMessage(string message)
    {
        try
        {
            var data = JsonUtility.FromJson<CyclingData>(message);

            if (data != null)
            {
                // Update current values
                currentPower = data.power;
                currentCadence = data.cadence;
                currentSpeed = data.speed;
                currentHeartRate = data.heart_rate;

                // Initialize smoothed values on first data
                if (smoothedPower == 0f && currentPower > 0f)
                {
                    smoothedPower = currentPower;
                    smoothedCadence = currentCadence;
                    smoothedSpeed = currentSpeed;
                }

                // Trigger event
                OnDataReceived?.Invoke(data);

                // Debug log (can be disabled in production)
                if (Time.frameCount % 60 == 0) // Log every ~1 second at 60 FPS
                {
                    Debug.Log($"[WahooData] Power: {Power:F0}W | Cadence: {Cadence:F0}rpm | Speed: {Speed:F1}km/h | HR: {HeartRate}bpm");
                }
            }
        }
        catch (Exception e)
        {
            Debug.LogError($"[WahooData] Failed to parse message: {e.Message}\nMessage: {message}");
        }
    }

    void OnApplicationQuit()
    {
        Disconnect();
    }

    void OnDestroy()
    {
        Disconnect();
    }

    // Helper methods for bike physics

    /// <summary>
    /// Get resistance force based on current power and speed
    /// F = P / v (where P is in watts, v is in m/s)
    /// </summary>
    public float GetResistanceForce()
    {
        if (Speed <= 0) return 0f;
        
        float speedMetersPerSec = Speed / 3.6f; // km/h to m/s
        return Power / speedMetersPerSec;
    }

    /// <summary>
    /// Get normalized power (0-1 range) for difficulty scaling
    /// Based on typical cycling power: 0W = 0, 300W = 1
    /// </summary>
    public float GetNormalizedPower(float maxPower = 300f)
    {
        return Mathf.Clamp01(Power / maxPower);
    }

    /// <summary>
    /// Check if rider is actively pedaling
    /// </summary>
    public bool IsPedaling()
    {
        return Power > 10f || Cadence > 10f;
    }
}
