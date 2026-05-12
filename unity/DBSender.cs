using System.IO;
using UnityEngine;
using System.Collections;
using System.Threading;
using System.Collections.Concurrent;
using UnityEngine.Networking;

/// <summary>
/// Logs pulse (heart-rate) data from the Wahoo bridge to a local text file.
///
/// Output: Application.dataPath/CARLogs/pulse.txt
///
/// File structure:
///   <participant_id>          ← line 1: resolved from analytics API
///   <unix_ms>|<bpm>
///   <unix_ms>|<bpm>
///   ...
///
/// participant_id is fetched from GET /api/sessions/{session_id} and polled
/// every 5 s until the questionnaire links a participant to the session.
/// The header line is written (or rewritten) as soon as it resolves.
/// </summary>
public class DBSender : MonoBehaviour {

    /// <summary>Assign the scene's WahooWsClient in the Inspector.</summary>
    [SerializeField] private WahooWsClient wahooWsClient;

    /// <summary>Assign TelemetryPublisher in the Inspector to read SessionId.</summary>
    [SerializeField] private LiveAnalytics.TelemetryPublisher telemetryPublisher;

    [Tooltip("Base URL of the analytics API.")]
    [SerializeField] private string analyticsApiUrl = "http://127.0.0.1:8080";

    private string pulseLog;
    private string _sessionId  = "";
    private string _participantId = "";   // empty until the server resolves it

    private ConcurrentQueue<string> _pulseQueue = new ConcurrentQueue<string>();

    private volatile int _latestHeartRate = 0;

    private Thread _loggingThread;
    private bool   _isRunning = true;

    private float _timeSinceLastLog;

    // ─────────────────────────────────────────────────────────────────

    void Start() {
        string logDir = Path.Combine(Application.dataPath, "CARLogs");
        Directory.CreateDirectory(logDir);
        pulseLog = Path.Combine(logDir, "pulse.txt");

        // Wipe the file so this session starts clean.
        File.WriteAllText(pulseLog, "");

        if (wahooWsClient != null)
            wahooWsClient.OnHeartRate += OnHeartRateReceived;
        else
            Debug.LogWarning("DBSender: WahooWsClient not assigned — pulse will not be logged.");

        _loggingThread = new Thread(WriteToFileLoop) { IsBackground = true };
        _loggingThread.Start();

        // One-frame delay so TelemetryPublisher.Start() has set SessionId.
        StartCoroutine(InitSession());
    }

    // ── session bootstrap ──────────────────────────────────────────

    private IEnumerator InitSession() {
        yield return null; // wait one frame

        _sessionId = telemetryPublisher != null ? telemetryPublisher.SessionId : "";
        if (string.IsNullOrEmpty(_sessionId))
            Debug.LogWarning("DBSender: SessionId not available from TelemetryPublisher.");

        // Reserve line 1 as a placeholder; will be replaced once participant resolves.
        WriteHeader("PENDING");

        // Poll until participant_id is known.
        StartCoroutine(PollParticipantId());
    }

    private IEnumerator PollParticipantId() {
        float[] delays = { 5f, 5f, 5f, 5f, 5f, 5f,   // first 30 s: every 5 s
                           10f, 10f, 10f,              // next 30 s: every 10 s
                           30f, 30f, 30f, 30f };       // then every 30 s

        foreach (float delay in delays) {
            yield return new WaitForSeconds(delay);

            if (!string.IsNullOrEmpty(_participantId)) yield break; // already resolved

            yield return StartCoroutine(FetchParticipantId());

            if (!string.IsNullOrEmpty(_participantId)) {
                Debug.Log($"DBSender: participant resolved → {_participantId}");
                RewriteHeader(_participantId);
                yield break;
            }
        }

        // Give up — leave header as PENDING so log is still usable with session_id.
        Debug.LogWarning($"DBSender: participant_id never resolved for session {_sessionId}. Header left as PENDING.");
    }

    private IEnumerator FetchParticipantId() {
        if (string.IsNullOrEmpty(_sessionId)) yield break;

        string url = $"{analyticsApiUrl}/api/sessions/{_sessionId}";
        using (UnityWebRequest req = UnityWebRequest.Get(url)) {
            req.timeout = 5;
            yield return req.SendWebRequest();

            if (req.result == UnityWebRequest.Result.Success) {
                // Minimal JSON parse — avoid a full JSON library dependency.
                string body = req.downloadHandler.text;
                string pid  = ExtractJsonString(body, "participant_id");
                if (!string.IsNullOrEmpty(pid) && pid != "null")
                    _participantId = pid;
            }
        }
    }

    // ── header helpers ─────────────────────────────────────────────

    /// Write participant_id (or placeholder) as the first line of the file,
    /// followed by any pulse lines already queued in the buffer.
    private void WriteHeader(string value) {
        try {
            // The queue hasn't been written yet at this point, so just prepend.
            string existing = File.ReadAllText(pulseLog);
            File.WriteAllText(pulseLog, value + "\n" + existing);
        }
        catch (System.Exception e) {
            Debug.LogWarning($"DBSender: WriteHeader failed — {e.Message}");
        }
    }

    /// Replace only line 1 of the file with the resolved participant_id.
    private void RewriteHeader(string participantId) {
        try {
            string[] lines = File.ReadAllLines(pulseLog);
            if (lines.Length > 0) lines[0] = participantId;
            File.WriteAllLines(pulseLog, lines);
        }
        catch (System.Exception e) {
            Debug.LogWarning($"DBSender: RewriteHeader failed — {e.Message}");
        }
    }

    // ── HR sampling ────────────────────────────────────────────────

    private void OnHeartRateReceived(int hr) {
        _latestHeartRate = hr;
    }

    void Update() {
        if (Time.time - _timeSinceLastLog > 1f) {
            _timeSinceLastLog = Time.time;

            int hr = _latestHeartRate;
            if (hr > 0) {
                long unixMs = System.DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                _pulseQueue.Enqueue($"{unixMs}|{hr}");
            }
        }
    }

    // ── background file writer ─────────────────────────────────────

    private void WriteToFileLoop() {
        while (_isRunning) {
            if (_pulseQueue.TryDequeue(out string line)) {
                try {
                    using (StreamWriter w = new StreamWriter(pulseLog, append: true))
                        w.WriteLine(line);
                }
                catch (System.Exception e) {
                    System.Console.WriteLine($"DBSender WriteError: {e.Message}");
                }
            }
            else {
                Thread.Sleep(100);
            }
        }
    }

    // ── teardown ───────────────────────────────────────────────────

    private void Cleanup() {
        _isRunning = false;
        if (wahooWsClient != null)
            wahooWsClient.OnHeartRate -= OnHeartRateReceived;
        if (_loggingThread != null && _loggingThread.IsAlive)
            _loggingThread.Join(500);
    }

    private void OnApplicationQuit() => Cleanup();
    private void OnDestroy()         => Cleanup();

    // ── tiny JSON helper ───────────────────────────────────────────

    /// Extracts the string value of a key from flat JSON without extra libs.
    /// Handles both string values ("key":"val") and null ("key":null).
    private static string ExtractJsonString(string json, string key) {
        string search = $"\"{key}\"";
        int ki = json.IndexOf(search);
        if (ki < 0) return null;
        int colon = json.IndexOf(':', ki + search.Length);
        if (colon < 0) return null;
        int start = colon + 1;
        while (start < json.Length && json[start] == ' ') start++;
        if (start >= json.Length) return null;
        if (json[start] == '"') {
            int end = json.IndexOf('"', start + 1);
            return end < 0 ? null : json.Substring(start + 1, end - start - 1);
        }
        // null or number
        int valEnd = json.IndexOfAny(new[]{ ',', '}', ']' }, start);
        return valEnd < 0 ? json.Substring(start).Trim() : json.Substring(start, valEnd - start).Trim();
    }
}