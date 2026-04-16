using System;
using System.Collections;
using System.Collections.Generic;
using System.Text;
using UnityEngine;

namespace LiveAnalytics
{
    /// <summary>
    /// Unity MonoBehaviour that samples gameplay and headpose telemetry at
    /// configurable rates, batches the data, and sends it over WebSocket
    /// to the Python analytics server.
    ///
    /// Attach this component to any persistent GameObject.  Assign the
    /// <see cref="config"/> ScriptableObject in the Inspector (or it will
    /// create sensible defaults at runtime).
    ///
    /// The publisher is additive – it does NOT modify existing VRSF logging
    /// or gameplay controllers.
    /// </summary>
    public class TelemetryPublisher : MonoBehaviour
    {
        [Header("Configuration")]
        [Tooltip("Reference to a TelemetryConfig ScriptableObject.")]
        public TelemetryConfig config;

        [Header("External data sources (wire in Inspector)")]
        [Tooltip("Current heart rate from BLE HR monitor.")]
        public float externalHeartRate;

        [Tooltip("Current bike speed in m/s.")]
        public float externalSpeed;

        [Tooltip("Current steering angle in degrees.")]
        public float externalSteeringAngle;

        [Tooltip("Front brake value 0-255.")]
        public int externalBrakeFront;

        [Tooltip("Rear brake value 0-255.")]
        public int externalBrakeRear;

        [Tooltip("Current trigger id (set by gameplay events, empty if none).")]
        public string currentTriggerId = "";

        // ── internal state ───────────────────────────────────────────────
        private string _sessionId;
        private TelemetryBuffer _buffer;
        private LiveFeedbackClient _wsClient;
        private float _gameplayAcc;
        private float _headposeAcc;
        private readonly List<TelemetryRecord> _batchScratch = new List<TelemetryRecord>(64);

        /// <summary>Session identifier (unix-ms string).</summary>
        public string SessionId => _sessionId;

        // ── lifecycle ────────────────────────────────────────────────────

        void Awake()
        {
            if (config == null)
            {
                config = ScriptableObject.CreateInstance<TelemetryConfig>();
                Debug.LogWarning("[LiveAnalytics] No TelemetryConfig assigned – using defaults.");
            }
        }

        void Start()
        {
            _sessionId = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds().ToString();
            _buffer = new TelemetryBuffer(1024);

            _wsClient = gameObject.GetComponent<LiveFeedbackClient>();
            if (_wsClient == null)
                _wsClient = gameObject.AddComponent<LiveFeedbackClient>();

            _wsClient.Initialise(config, _sessionId);

            Debug.Log($"[LiveAnalytics] TelemetryPublisher started – session {_sessionId}");
        }

        void Update()
        {
            float dt = Time.deltaTime;

            // ── gameplay sampling ────────────────────────────────────────
            _gameplayAcc += dt;
            float gpInterval = 1f / config.gameplayHz;
            while (_gameplayAcc >= gpInterval)
            {
                EnqueueGameplay();
                _gameplayAcc -= gpInterval;
            }

            // ── headpose sampling ────────────────────────────────────────
            _headposeAcc += dt;
            float hpInterval = 1f / config.headposeHz;
            while (_headposeAcc >= hpInterval)
            {
                EnqueueHeadpose();
                _headposeAcc -= hpInterval;
            }

            // ── flush buffer to WebSocket ────────────────────────────────
            FlushBuffer();
        }

        void OnDestroy()
        {
            // Flush remaining records
            FlushBuffer();
        }

        // ── sampling helpers ─────────────────────────────────────────────

        private TelemetryRecord MakeBaseRecord(string recordType)
        {
            var r = new TelemetryRecord
            {
                session_id = _sessionId,
                unix_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                unity_time = Time.time,
                scenario_id = config != null ? config.scenarioId : "",
                trigger_id = currentTriggerId ?? "",
                record_type = recordType,
            };
            return r;
        }

        private void EnqueueGameplay()
        {
            var r = MakeBaseRecord("gameplay");
            r.speed = externalSpeed;
            r.steering_angle = externalSteeringAngle;
            r.brake_front = externalBrakeFront;
            r.brake_rear = externalBrakeRear;
            r.heart_rate = externalHeartRate;

            // Also capture head for gameplay records
            if (Camera.main != null)
            {
                var t = Camera.main.transform;
                r.head_pos_x = t.position.x;
                r.head_pos_y = t.position.y;
                r.head_pos_z = t.position.z;
                r.head_rot_x = t.rotation.x;
                r.head_rot_y = t.rotation.y;
                r.head_rot_z = t.rotation.z;
                r.head_rot_w = t.rotation.w;
            }

            _buffer.Enqueue(r);
        }

        private void EnqueueHeadpose()
        {
            var r = MakeBaseRecord("headpose");

            if (Camera.main != null)
            {
                var t = Camera.main.transform;
                r.head_pos_x = t.position.x;
                r.head_pos_y = t.position.y;
                r.head_pos_z = t.position.z;
                r.head_rot_x = t.rotation.x;
                r.head_rot_y = t.rotation.y;
                r.head_rot_z = t.rotation.z;
                r.head_rot_w = t.rotation.w;
            }

            _buffer.Enqueue(r);
        }

        // ── batch flush ──────────────────────────────────────────────────

        private void FlushBuffer()
        {
            if (_buffer.Count == 0) return;
            if (_wsClient == null || !_wsClient.IsConnected) return;

            _batchScratch.Clear();
            int n = _buffer.DequeueBatch(_batchScratch, config.maxBatchSize);
            if (n == 0) return;

            var batch = new TelemetryBatch
            {
                records = _batchScratch.ToArray(),
                count = n,
                sent_at = DateTime.UtcNow.ToString("o"),
            };

            string json = JsonUtility.ToJson(batch);
            _wsClient.Send(json);
        }
    }
}
