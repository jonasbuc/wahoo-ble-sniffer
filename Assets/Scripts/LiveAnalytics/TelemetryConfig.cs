using UnityEngine;

namespace LiveAnalytics
{
    /// <summary>
    /// Centralised configuration for the live-analytics telemetry publisher.
    /// Attach to a GameObject or reference from <see cref="TelemetryPublisher"/>.
    /// All values can be overridden in the Inspector.
    /// </summary>
    [CreateAssetMenu(fileName = "TelemetryConfig", menuName = "LiveAnalytics/TelemetryConfig")]
    public class TelemetryConfig : ScriptableObject
    {
        [Header("WebSocket server")]
        [Tooltip("Hostname or IP of the analytics ingest server.")]
        public string serverHost = "127.0.0.1";

        [Tooltip("WebSocket port for the ingest endpoint.")]
        public int serverPort = 8765;

        [Tooltip("WebSocket path for the ingest endpoint.")]
        public string wsPath = "/ws/ingest";

        [Header("Sampling rates (Hz)")]
        [Tooltip("How often gameplay telemetry is sent (speed, steering, brakes, HR).")]
        [Range(1f, 60f)]
        public float gameplayHz = 20f;

        [Tooltip("How often headpose telemetry is sent.")]
        [Range(1f, 120f)]
        public float headposeHz = 20f;

        [Header("Batching")]
        [Tooltip("Maximum number of telemetry records batched into a single WebSocket message.")]
        [Range(1, 100)]
        public int maxBatchSize = 10;

        [Tooltip("Maximum seconds to hold a batch before flushing, even if not full.")]
        [Range(0.01f, 2f)]
        public float maxBatchAgeSec = 0.25f;

        [Header("Reconnection")]
        [Tooltip("Base delay in seconds before attempting to reconnect after a drop.")]
        [Range(0.5f, 10f)]
        public float reconnectBaseSec = 1f;

        [Tooltip("Maximum delay in seconds between reconnect attempts (exponential back-off cap).")]
        [Range(2f, 60f)]
        public float reconnectMaxSec = 15f;

        [Header("Session")]
        [Tooltip("Optional scenario identifier sent with every packet.")]
        public string scenarioId = "";

        /// <summary>Full WebSocket URI built from the current settings.</summary>
        public string WsUri => $"ws://{serverHost}:{serverPort}{wsPath}";
    }
}
