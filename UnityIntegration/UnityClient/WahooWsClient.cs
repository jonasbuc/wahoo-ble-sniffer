using System;
using System.Collections.Concurrent;
using System.IO;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;

// Lightweight WebSocket client for Unity that connects to the Wahoo bridge,
// decodes binary frames (dfffi) for heart-rate and forwards JSON trigger
// events as C# events/delegates on the main thread.
// NOTE: This uses System.Net.WebSockets.ClientWebSocket which is available
// when scripting runtime supports .NET 4.x Equivalent. If your Unity
// target/platform doesn't support ClientWebSocket, consider using
// websocket-sharp or the UnityWebRequest-based WebSocket packages.

public class WahooWsClient : MonoBehaviour
{
    [Tooltip("WebSocket URI of the bridge (prefer 127.0.0.1 for local)")]
    public string uri = "ws://127.0.0.1:8765";

    // Events consumers can subscribe to
    public event Action<int> OnHeartRate;          // current HR value
    public event Action<string> OnTriggerReceived; // trigger event name or raw

    // Internal
    private ClientWebSocket _ws;
    private CancellationTokenSource _cts;
    private readonly ConcurrentQueue<Action> _mainThreadQueue = new ConcurrentQueue<Action>();

    private void Start()
    {
        _cts = new CancellationTokenSource();
        // Fire-and-forget the connect loop
        _ = Task.Run(() => ConnectLoop(_cts.Token));
    }

    private async Task ConnectLoop(CancellationToken token)
    {
        while (!token.IsCancellationRequested)
        {
            try
            {
                _ws = new ClientWebSocket();
                await _ws.ConnectAsync(new Uri(uri), token);
                Debug.Log("WahooWsClient: connected to " + uri);
                await ReceiveLoop(_ws, token);
            }
            catch (Exception ex)
            {
                Debug.LogWarning("WahooWsClient: connection failed: " + ex.Message);
            }

            // Wait before retry
            await Task.Delay(2000, token).ContinueWith(_ => { });
        }
    }

    private async Task ReceiveLoop(ClientWebSocket ws, CancellationToken token)
    {
        var buffer = new byte[8192];
        var seg = new ArraySegment<byte>(buffer);

        try
        {
            while (ws.State == WebSocketState.Open && !token.IsCancellationRequested)
            {
                using (var ms = new MemoryStream())
                {
                    WebSocketReceiveResult result;
                    do
                    {
                        result = await ws.ReceiveAsync(seg, token);
                        if (result.Count > 0)
                            ms.Write(buffer, 0, result.Count);
                    } while (!result.EndOfMessage && !token.IsCancellationRequested);

                    if (result.MessageType == WebSocketMessageType.Close)
                    {
                        await ws.CloseAsync(WebSocketCloseStatus.NormalClosure, "", token);
                        break;
                    }

                    ms.Seek(0, SeekOrigin.Begin);
                    if (result.MessageType == WebSocketMessageType.Text)
                    {
                        using (var sr = new StreamReader(ms, Encoding.UTF8))
                        {
                            var text = await sr.ReadToEndAsync();
                            HandleTextMessage(text);
                        }
                    }
                    else if (result.MessageType == WebSocketMessageType.Binary)
                    {
                        var bytes = ms.ToArray();
                        HandleBinaryMessage(bytes);
                    }
                }
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception ex)
        {
            Debug.LogWarning("WahooWsClient: receive loop error: " + ex.Message);
        }
    }

    private void HandleTextMessage(string text)
    {
        if (string.IsNullOrEmpty(text)) return;

        // Try to parse JSON for known fields. Use Unity's JsonUtility for a
        // small POCO. We deliberately keep the shape minimal.
        try
        {
            // Handshake messages contain protocol/version/modes
            if (text.Contains("protocol") || text.Contains("version"))
            {
                Debug.Log("WahooWsClient: handshake: " + text);
                return;
            }

            // Event messages should be JSON like {"event":"hall_hit", ...}
            var evt = JsonUtility.FromJson<WsEvent>(text);
            if (!string.IsNullOrEmpty(evt.@event))
            {
                var name = evt.@event;
                // Enqueue invocation on main thread
                _mainThreadQueue.Enqueue(() => OnTriggerReceived?.Invoke(name));
                return;
            }

            // Some producers may send heart_rate as JSON
            if (text.Contains("heart_rate") || text.Contains("hr"))
            {
                var hrObj = JsonUtility.FromJson<HeartRateJson>(text);
                if (hrObj != null && hrObj.heart_rate != 0)
                {
                    var hr = hrObj.heart_rate;
                    _mainThreadQueue.Enqueue(() => OnHeartRate?.Invoke(hr));
                    return;
                }
            }
        }
        catch (Exception ex)
        {
            Debug.LogWarning("WahooWsClient: failed to parse JSON text: " + ex.Message);
        }

        // Fallback: treat whole text as a raw trigger
        _mainThreadQueue.Enqueue(() => OnTriggerReceived?.Invoke(text));
    }

    private void HandleBinaryMessage(byte[] bytes)
    {
        // Expecting Python struct.pack('dfffi') = 8 + 4 + 4 + 4 + 4 = 24 bytes
        if (bytes == null || bytes.Length < 24) return;

        try
        {
            // Little-endian expected on most machines; Python struct default is native.
            bool isLittle = BitConverter.IsLittleEndian;

            double timestamp = BitConverter.ToDouble(bytes, 0);
            float power = BitConverter.ToSingle(bytes, 8);
            float cadence = BitConverter.ToSingle(bytes, 12);
            float speed = BitConverter.ToSingle(bytes, 16);
            int hr = BitConverter.ToInt32(bytes, 20);

            // Enqueue main-thread invocation
            _mainThreadQueue.Enqueue(() => OnHeartRate?.Invoke(hr));
        }
        catch (Exception ex)
        {
            Debug.LogWarning("WahooWsClient: failed to decode binary frame: " + ex.Message);
        }
    }

    private void Update()
    {
        // Execute pending main-thread actions
        while (_mainThreadQueue.TryDequeue(out var action))
        {
            try { action(); } catch (Exception ex) { Debug.LogException(ex); }
        }
    }

    private async void OnDestroy()
    {
        try
        {
            _cts?.Cancel();
            if (_ws != null && (_ws.State == WebSocketState.Open || _ws.State == WebSocketState.CloseReceived))
            {
                await _ws.CloseAsync(WebSocketCloseStatus.NormalClosure, "bye", CancellationToken.None);
            }
        }
        catch (Exception) { }
    }

    [Serializable]
    private class WsEvent
    {
        // 'event' is a valid JSON key; use @event to map it in C#
        public string @event;
        public string raw;
        public string source;
        public double timestamp;
    }

    [Serializable]
    private class HeartRateJson
    {
        public int heart_rate;
    }
}
