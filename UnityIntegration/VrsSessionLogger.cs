using System;
using System.Buffers;
using System.Collections.Concurrent;
using System.IO;
using System.Text;
using UnityEngine;

namespace VrsLogging
{
    public class VrsSessionLogger : MonoBehaviour
    {
        public string logBasePath = "Logs";
        public ulong sessionId = 0;

        // sampling rates
        public float headHz = 120f;
        public float bikeHz = 50f;

        VrsFileWriterFixed headWriter;
        VrsFileWriterFixed bikeWriter;
        VrsFileWriterFixed hrWriter;
        VrsFileWriterEvents eventsWriter;

        uint headSeq = 0;
        uint bikeSeq = 0;
        uint hrSeq = 0;
        uint eventSeq = 0;

        string sessionDir;
        float headAcc = 0f;
        float bikeAcc = 0f;

        public float LastHr { get; private set; } = 0f;

        public string SessionDir => sessionDir;

        public long HeadQueueCount => 0; // not exposed for now

        void Start()
        {
            if (sessionId == 0) sessionId = (ulong)DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            sessionDir = Path.Combine(logBasePath, $"session_{sessionId}");
            Directory.CreateDirectory(sessionDir);

            headWriter = new VrsFileWriterFixed(Path.Combine(sessionDir, "headpose.vrsf"), VrsFormats.StreamHeadpose, sessionId, VrsFormats.HeadposeRecordSize);
            bikeWriter = new VrsFileWriterFixed(Path.Combine(sessionDir, "bike.vrsf"), VrsFormats.StreamBike, sessionId, VrsFormats.BikeRecordSize);
            hrWriter = new VrsFileWriterFixed(Path.Combine(sessionDir, "hr.vrsf"), VrsFormats.StreamHr, sessionId, VrsFormats.HrRecordSize);
            eventsWriter = new VrsFileWriterEvents(Path.Combine(sessionDir, "events.vrsf"), VrsFormats.StreamEvents, sessionId);

            WriteManifest();
        }

        void OnDestroy()
        {
            StopWriters();
        }

        void OnApplicationQuit()
        {
            StopWriters();
        }

        void StopWriters()
        {
            try { headWriter?.Dispose(); } catch { }
            try { bikeWriter?.Dispose(); } catch { }
            try { hrWriter?.Dispose(); } catch { }
            try { eventsWriter?.Dispose(); } catch { }
        }

        /// <summary>
        /// Stop current writers and start a new session with the given id.
        /// This allows creating a new test subject/session at runtime without restarting Unity.
        /// </summary>
        public void StartNewSession(ulong newSessionId, string subjectLabel = null)
        {
            try
            {
                // Stop current writers to flush files
                StopWriters();

                // reset sequence counters
                headSeq = bikeSeq = hrSeq = eventSeq = 0;

                // set new session id and directory
                sessionId = newSessionId;
                sessionDir = Path.Combine(logBasePath, $"session_{sessionId}");
                Directory.CreateDirectory(sessionDir);

                // recreate writers
                headWriter = new VrsFileWriterFixed(Path.Combine(sessionDir, "headpose.vrsf"), VrsFormats.StreamHeadpose, sessionId, VrsFormats.HeadposeRecordSize);
                bikeWriter = new VrsFileWriterFixed(Path.Combine(sessionDir, "bike.vrsf"), VrsFormats.StreamBike, sessionId, VrsFormats.BikeRecordSize);
                hrWriter = new VrsFileWriterFixed(Path.Combine(sessionDir, "hr.vrsf"), VrsFormats.StreamHr, sessionId, VrsFormats.HrRecordSize);
                eventsWriter = new VrsFileWriterEvents(Path.Combine(sessionDir, "events.vrsf"), VrsFormats.StreamEvents, sessionId);

                // write manifest including optional subject label
                var manifest = new
                {
                    session_id = sessionId,
                    started_unix_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                    files = new[] { "headpose.vrsf", "bike.vrsf", "hr.vrsf", "events.vrsf" },
                    record_sizes = new { headpose = VrsFormats.HeadposeRecordSize, bike = VrsFormats.BikeRecordSize, hr = VrsFormats.HrRecordSize },
                    expected_hz = new { headpose = headHz, bike = bikeHz },
                    subject = subjectLabel
                };
                var json = JsonUtility.ToJson(manifest);
                File.WriteAllText(Path.Combine(sessionDir, "manifest.json"), json);

                Debug.Log($"[VrsSessionLogger] Started new session {sessionId} (subject={subjectLabel}) at {sessionDir}");
            }
            catch (Exception ex)
            {
                Debug.LogError($"StartNewSession error: {ex}");
            }
        }

        void Update()
        {
            float dt = Time.deltaTime;
            headAcc += dt;
            bikeAcc += dt;

            float headInterval = 1f / headHz;
            while (headAcc >= headInterval)
            {
                SampleHead();
                headAcc -= headInterval;
            }

            float bikeInterval = 1f / bikeHz;
            while (bikeAcc >= bikeInterval)
            {
                SampleBike();
                bikeAcc -= bikeInterval;
            }
        }

        void SampleHead()
        {
            var rt = Camera.main != null ? Camera.main.transform : this.transform;
            var pos = rt.position;
            var rot = rt.rotation;
            float t = Time.time;
            var buf = ArrayPool<byte>.Shared.Rent(VrsFormats.HeadposeRecordSize);
            try
            {
                VrsFormats.WriteHeadposeRecord(buf.AsSpan(), headSeq++, t, new UnityEngine.Vector3(pos.x, pos.y, pos.z), rot);
                headWriter.Enqueue(buf);
            }
            catch (Exception ex)
            {
                ArrayPool<byte>.Shared.Return(buf);
                Debug.LogError($"SampleHead error: {ex}");
            }
        }

        void SampleBike()
        {
            // Placeholder accesses; user should wire actual data sources or delegates
            float speed = 0f;
            float steering = 0f;
            byte bf = 0;
            byte br = 0;
            float t = Time.time;
            var buf = ArrayPool<byte>.Shared.Rent(VrsFormats.BikeRecordSize);
            try
            {
                VrsFormats.WriteBikeRecord(buf.AsSpan(), bikeSeq++, t, speed, steering, bf, br);
                bikeWriter.Enqueue(buf);
            }
            catch (Exception ex)
            {
                ArrayPool<byte>.Shared.Return(buf);
                Debug.LogError($"SampleBike error: {ex}");
            }
        }

        public void LogHr(float hrBpm)
        {
            LastHr = hrBpm;
            float t = Time.time;
            var buf = ArrayPool<byte>.Shared.Rent(VrsFormats.HrRecordSize);
            try
            {
                VrsFormats.WriteHrRecord(buf.AsSpan(), hrSeq++, t, hrBpm);
                hrWriter.Enqueue(buf);
            }
            catch (Exception ex)
            {
                ArrayPool<byte>.Shared.Return(buf);
                Debug.LogError($"LogHr error: {ex}");
            }
        }

        public void LogEvent(object payload)
        {
            string json = JsonUtility.ToJson(payload);
            LogEventRaw(json);
        }

        public void LogEventRaw(string json)
        {
            float t = Time.time;
            var utf8 = Encoding.UTF8.GetBytes(json);
            int recSize = 4 + 4 + 4 + utf8.Length;
            var buf = ArrayPool<byte>.Shared.Rent(recSize);
            try
            {
                int written = VrsFormats.WriteEventRecord(buf.AsSpan(), eventSeq++, t, json);
                // copy to right-sized array for enqueue to avoid returning larger arrays to pool
                var copy = ArrayPool<byte>.Shared.Rent(written);
                Buffer.BlockCopy(buf, 0, copy, 0, written);
                ArrayPool<byte>.Shared.Return(buf);
                eventsWriter.Enqueue(copy);
            }
            catch (Exception ex)
            {
                ArrayPool<byte>.Shared.Return(buf);
                Debug.LogError($"LogEventRaw error: {ex}");
            }
        }

        void WriteManifest()
        {
            var manifest = new
            {
                session_id = sessionId,
                started_unix_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                files = new[] { "headpose.vrsf", "bike.vrsf", "hr.vrsf", "events.vrsf" },
                record_sizes = new { headpose = VrsFormats.HeadposeRecordSize, bike = VrsFormats.BikeRecordSize, hr = VrsFormats.HrRecordSize },
                expected_hz = new { headpose = headHz, bike = bikeHz }
            };
            var json = JsonUtility.ToJson(manifest);
            File.WriteAllText(Path.Combine(sessionDir, "manifest.json"), json);
        }
    }
}
