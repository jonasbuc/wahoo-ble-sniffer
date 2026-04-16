using System;
using System.Collections.Concurrent;
using System.IO;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;

// Lightweight WebSocket client for Unity that connects to the bike bridge,
// decodes binary frames (di: timestamp + hr) and forwards JSON trigger
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

    // ── Public API: send trigger events from Unity → bridge → GUI ───────

    /// <summary>
    /// Send a named trigger event to the bridge server.  The bridge relays
    /// the event JSON to all other connected clients (including the GUI),
    /// which draws an orange vertical marker on the HR graph.
    ///
    /// Call this from any Unity trigger zone, collision handler, or game
    /// event.  Example:
    /// <code>
    ///   wsClient.SendEvent("checkpoint_1");
    ///   wsClient.SendEvent("lap_complete", extraJson: "{\"lap\":3}");
    /// </code>
    /// </summary>
    /// <param name="eventName">Short event label (e.g. "spawn", "hall_hit", "lap_3")</param>
    /// <param name="extraJson">Optional extra JSON fields merged into the message</param>
    public void SendEvent(string eventName, string extraJson = null)
    {
        if (_ws == null || _ws.State != WebSocketState.Open)
        {
            Debug.LogWarning("WahooWsClient: cannot send event — not connected");
            return;
        }

        // Build minimal JSON:  {"event":"<name>","source":"unity","timestamp":<epoch>}
        double epoch = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0;
        string json;
        if (!string.IsNullOrEmpty(extraJson))
        {
            // Merge: strip outer braces from extraJson and append
            var extra = extraJson.Trim();
            if (extra.StartsWith("{")) extra = extra.Substring(1);
            if (extra.EndsWith("}"))   extra = extra.Substring(0, extra.Length - 1);
            json = $"{{\"event\":\"{eventName}\",\"source\":\"unity\",\"timestamp\":{epoch},{extra}}}";
        }
        else
        {
            json = $"{{\"event\":\"{eventName}\",\"source\":\"unity\",\"timestamp\":{epoch}}}";
        }

        var bytes = Encoding.UTF8.GetBytes(json);
        var segment = new ArraySegment<byte>(bytes);

        // Fire-and-forget send on the background thread
        _ = Task.Run(async () =>
        {
            try
            {
                if (_ws != null && _ws.State == WebSocketState.Open)
                {
                    await _ws.SendAsync(segment, WebSocketMessageType.Text, true, CancellationToken.None);
                    Debug.Log($"WahooWsClient: sent event '{eventName}'");
                }
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"WahooWsClient: failed to send event: {ex.Message}");
            }
        });
    }

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
        // Expecting Python struct.pack('di') = 8 + 4 = 12 bytes
        // d = double timestamp (8 bytes), i = int32 hr (4 bytes)
        if (bytes == null || bytes.Length < 12) return;

        try
        {
            double timestamp = BitConverter.ToDouble(bytes, 0);
            int hr = BitConverter.ToInt32(bytes, 8);

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
