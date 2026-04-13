using System;
using UnityEngine;

namespace LiveAnalytics
{
    /// <summary>
    /// Pydantic-compatible telemetry payload models.
    /// Serialised to JSON and sent over WebSocket to the analytics server.
    /// Field names use snake_case to match the Python Pydantic schemas.
    /// </summary>

    // ── individual telemetry record ──────────────────────────────────────
    [Serializable]
    public class TelemetryRecord
    {
        /// <summary>Unique session identifier (unix-ms at session start).</summary>
        public string session_id;

        /// <summary>UTC unix epoch in milliseconds at sampling time.</summary>
        public long unix_ms;

        /// <summary><c>Time.time</c> value at sampling time (seconds since scene load).</summary>
        public float unity_time;

        /// <summary>Optional scenario tag (e.g. "intersection_A").</summary>
        public string scenario_id;

        /// <summary>Optional trigger tag set by gameplay events.</summary>
        public string trigger_id;

        /// <summary>Current bike speed in m/s.</summary>
        public float speed;

        /// <summary>Steering angle in degrees.</summary>
        public float steering_angle;

        /// <summary>Front brake pressure 0-255.</summary>
        public int brake_front;

        /// <summary>Rear brake pressure 0-255.</summary>
        public int brake_rear;

        /// <summary>Heart rate in BPM from BLE HR monitor.</summary>
        public float heart_rate;

        // Head position
        public float head_pos_x;
        public float head_pos_y;
        public float head_pos_z;

        // Head rotation (quaternion)
        public float head_rot_x;
        public float head_rot_y;
        public float head_rot_z;
        public float head_rot_w;

        /// <summary>Record type discriminator: "gameplay" or "headpose".</summary>
        public string record_type;
    }

    // ── batched message envelope ─────────────────────────────────────────
    [Serializable]
    public class TelemetryBatch
    {
        /// <summary>Array of telemetry records in this batch.</summary>
        public TelemetryRecord[] records;

        /// <summary>Number of records in this batch.</summary>
        public int count;

        /// <summary>ISO-8601 UTC timestamp when batch was created.</summary>
        public string sent_at;
    }

    // ── live feedback message from server ────────────────────────────────
    [Serializable]
    public class LiveFeedback
    {
        /// <summary>Current stress score (0–100).</summary>
        public float stress_score;

        /// <summary>Current risk score (0–100).</summary>
        public float risk_score;

        /// <summary>Optional event tag for triggering in-sim reactions.</summary>
        public string event_tag;

        /// <summary>Freeform message for debug overlay.</summary>
        public string message;
    }
}
