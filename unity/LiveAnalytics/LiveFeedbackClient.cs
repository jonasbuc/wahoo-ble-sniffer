using System;
using System.Collections;
using System.Collections.Concurrent;
using System.Text;
using UnityEngine;

#if UNITY_WEBGL && !UNITY_EDITOR
// WebGL uses a JS-based WebSocket plugin – not implemented here.
#else
using System.Net.WebSockets;
using System.Threading;
using System.Threading.Tasks;
#endif

namespace LiveAnalytics
{
    /// <summary>
    /// Manages a single WebSocket connection to the analytics server.
    /// Provides <see cref="Send"/> to enqueue outgoing JSON messages and
    /// dispatches incoming <see cref="LiveFeedback"/> messages via
    /// <see cref="OnFeedback"/>.
    ///
    /// Reconnects automatically with exponential back-off when the
    /// connection drops.
    /// </summary>
    public class LiveFeedbackClient : MonoBehaviour
    {
        /// <summary>Raised on the main thread when a feedback message arrives.</summary>
        public event Action<LiveFeedback> OnFeedback;

        /// <summary>True when the WebSocket is in the Open state.</summary>
        public bool IsConnected
        {
            get
            {
#if UNITY_WEBGL && !UNITY_EDITOR
                return false;
#else
                return _ws != null && _ws.State == WebSocketState.Open;
#endif
            }
        }

        // ── configuration (set via Initialise) ──────────────────────────
        private TelemetryConfig _config;
        private string _sessionId;

        // ── internal state ──────────────────────────────────────────────
#if !(UNITY_WEBGL && !UNITY_EDITOR)
        private ClientWebSocket _ws;
        private CancellationTokenSource _cts;
        private readonly ConcurrentQueue<string> _outgoing = new ConcurrentQueue<string>();
        private readonly ConcurrentQueue<string> _incoming = new ConcurrentQueue<string>();
        private bool _connecting;
        private float _reconnectDelay;
        private float _reconnectTimer;
#endif

        // ── public API ──────────────────────────────────────────────────

        /// <summary>
        /// Must be called before the client can connect.
        /// Sets the configuration and starts the background connection loop.
        /// </summary>
        public void Initialise(TelemetryConfig config, string sessionId)
        {
            _config = config;
            _sessionId = sessionId;
#if !(UNITY_WEBGL && !UNITY_EDITOR)
            _reconnectDelay = _config.reconnectBaseSec;
            StartCoroutine(ConnectionLoop());
#endif
        }

        /// <summary>Enqueue a JSON message for sending (non-blocking).</summary>
        public void Send(string json)
        {
#if !(UNITY_WEBGL && !UNITY_EDITOR)
            _outgoing.Enqueue(json);
#endif
        }

        // ── lifecycle ───────────────────────────────────────────────────

        void Update()
        {
#if !(UNITY_WEBGL && !UNITY_EDITOR)
            // Dispatch incoming feedback on the main thread
            while (_incoming.TryDequeue(out string msg))
            {
                try
                {
                    var fb = JsonUtility.FromJson<LiveFeedback>(msg);
                    OnFeedback?.Invoke(fb);
                }
                catch (Exception ex)
                {
                    Debug.LogWarning($"[LiveAnalytics] Bad feedback JSON: {ex.Message}");
                }
            }
#endif
        }

        void OnDestroy()
        {
#if !(UNITY_WEBGL && !UNITY_EDITOR)
            _cts?.Cancel();
            try { _ws?.Dispose(); } catch { }
#endif
        }

        // ── connection loop (coroutine) ─────────────────────────────────
#if !(UNITY_WEBGL && !UNITY_EDITOR)
        private IEnumerator ConnectionLoop()
        {
            while (true)
            {
                if (!IsConnected && !_connecting)
                {
                    _connecting = true;
                    Task connectTask = ConnectAsync();

                    // Wait for the connect task to finish without blocking
                    while (!connectTask.IsCompleted)
                        yield return null;

                    _connecting = false;

                    if (connectTask.IsFaulted)
                    {
                        Debug.LogWarning($"[LiveAnalytics] WS connect failed: {connectTask.Exception?.InnerException?.Message}");
                        _reconnectTimer = _reconnectDelay;
                        _reconnectDelay = Mathf.Min(_reconnectDelay * 2f, _config.reconnectMaxSec);

                        while (_reconnectTimer > 0f)
                        {
                            _reconnectTimer -= Time.deltaTime;
                            yield return null;
                        }
                        continue;
                    }

                    // Reset back-off on success
                    _reconnectDelay = _config.reconnectBaseSec;

                    // Start send/recv loops
                    Task sendLoop = SendLoopAsync();
                    Task recvLoop = ReceiveLoopAsync();

                    while (!sendLoop.IsCompleted && !recvLoop.IsCompleted)
                        yield return null;

                    // Connection lost – loop will reconnect
                    Debug.LogWarning("[LiveAnalytics] WS connection closed – will reconnect.");
                }
                yield return null;
            }
        }

        private async Task ConnectAsync()
        {
            _cts?.Cancel();
            _cts = new CancellationTokenSource();
            _ws = new ClientWebSocket();

            string uri = _config.WsUri;
            Debug.Log($"[LiveAnalytics] Connecting to {uri}");
            await _ws.ConnectAsync(new Uri(uri), _cts.Token);
            Debug.Log("[LiveAnalytics] WS connected.");
        }

        private async Task SendLoopAsync()
        {
            try
            {
                while (_ws.State == WebSocketState.Open && !_cts.Token.IsCancellationRequested)
                {
                    if (_outgoing.TryDequeue(out string json))
                    {
                        byte[] data = Encoding.UTF8.GetBytes(json);
                        await _ws.SendAsync(
                            new ArraySegment<byte>(data),
                            WebSocketMessageType.Text,
                            endOfMessage: true,
                            cancellationToken: _cts.Token);
                    }
                    else
                    {
                        await Task.Delay(5, _cts.Token); // prevent busy spin
                    }
                }
            }
            catch (OperationCanceledException) { }
            catch (Exception ex)
            {
                Debug.LogWarning($"[LiveAnalytics] Send error: {ex.Message}");
            }
        }

        private async Task ReceiveLoopAsync()
        {
            var buf = new byte[4096];
            try
            {
                while (_ws.State == WebSocketState.Open && !_cts.Token.IsCancellationRequested)
                {
                    var result = await _ws.ReceiveAsync(new ArraySegment<byte>(buf), _cts.Token);
                    if (result.MessageType == WebSocketMessageType.Close)
                        break;

                    string msg = Encoding.UTF8.GetString(buf, 0, result.Count);
                    _incoming.Enqueue(msg);
                }
            }
            catch (OperationCanceledException) { }
            catch (Exception ex)
            {
                Debug.LogWarning($"[LiveAnalytics] Recv error: {ex.Message}");
            }
        }
#endif
    }
}
