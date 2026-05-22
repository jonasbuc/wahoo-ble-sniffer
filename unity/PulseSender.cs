using System.IO;
using UnityEngine;
using System.Collections;
using System.Threading;
using System.Collections.Concurrent;
using UnityEngine.Networking;

/// <summary>
/// Logs pulse (heart-rate) data from the Wahoo bridge to a local text file
/// and forwards each sample live to the external research API.
///
/// Output: Application.dataPath/CARLogs/pulse.txt
///
/// File structure:
///   <participant_id>          ← line 1: resolved from analytics API (or "PENDING")
///   <unix_ms>|<bpm>
///   <unix_ms>|<bpm>
///   ...
///
/// Use this script when DBSender (bike/head/arduino) is already in the scene.
/// If you want everything in one component, use the merged DBSender instead.
/// </summary>
public class PulseSender : MonoBehaviour {

    // ── Inspector ──────────────────────────────────────────────────

    /// <summary>Assign the scene's WahooWsClient in the Inspector.</summary>
    [SerializeField] private WahooWsClient wahooWsClient;

    /// <summary>Assign TelemetryPublisher in the Inspector to read SessionId.</summary>
    [SerializeField] private LiveAnalytics.TelemetryPublisher telemetryPublisher;

    [Tooltip("Base URL of the local analytics API.")]
    [SerializeField] private string analyticsApiUrl = "http://127.0.0.1:8080";

    /// <summary>
    /// External research API endpoint for live pulse forwarding.
    ///   RPI:    https://10.200.130.36:5001/api/cardatasqlite
    ///   Laptop: https://10.200.130.98:5001/api/car/logbikedata
    /// Leave empty to disable.
    /// </summary>
    [Tooltip("External research API URL. Leave empty to disable live forwarding.")]
    [SerializeField] private string externalApiUrl = "https://10.200.130.98:5001/api/car/logbikedata";

    // ── private state ──────────────────────────────────────────────

    private string _pulseLog;
    private string _sessionId     = "";
    private string _participantId = "";   // empty until analytics API resolves it

    // ── manual override ───────────────────────────────────────────
    /// <summary>True when the operator has manually chosen an ID from the
    /// Dashboard. Auto-polling is suppressed while this flag is set.</summary>
    private bool _manualOverride = false;

    private Coroutine _pollCoroutine;

    private ConcurrentQueue<string> _pulseQueue = new ConcurrentQueue<string>();
    private volatile int _latestHeartRate = 0;

    private Thread _loggingThread;
    private bool   _isRunning = true;
    private float  _timeSinceLastLog;

    // ── public read API ───────────────────────────────────────────
    /// <summary>
    /// The currently active participant ID.
    /// Returns <c>"PENDING"</c> while still waiting for auto-resolution.
    /// </summary>
    public string ParticipantId => string.IsNullOrEmpty(_participantId) ? "PENDING" : _participantId;

    /// <summary>True while the auto-link poll is still running.</summary>
    public bool IsPending => string.IsNullOrEmpty(_participantId);

    // ── lifecycle ──────────────────────────────────────────────────

    void Start() {
        string logDir = Path.Combine(Application.dataPath, "CARLogs");
        Directory.CreateDirectory(logDir);
        _pulseLog = Path.Combine(logDir, "pulse.txt");

        // Wipe the file so each session starts clean.
        File.WriteAllText(_pulseLog, "");

        if (wahooWsClient != null)
            wahooWsClient.OnHeartRate += OnHeartRateReceived;
        else
            Debug.LogWarning("PulseSender: WahooWsClient not assigned — pulse will not be logged.");

        _loggingThread = new Thread(WriteToFileLoop) { IsBackground = true };
        _loggingThread.Start();

        // Wait one frame so TelemetryPublisher.Start() has set SessionId.
        StartCoroutine(InitSession());
    }

    // ── session bootstrap ──────────────────────────────────────────

    private IEnumerator InitSession() {
        yield return null; // wait one frame

        _sessionId = telemetryPublisher != null ? telemetryPublisher.SessionId : "";
        if (string.IsNullOrEmpty(_sessionId))
            Debug.LogWarning("PulseSender: SessionId not available from TelemetryPublisher.");

        WriteHeader("PENDING");
        _pollCoroutine = StartCoroutine(PollParticipantId());
    }

    // ── manual override ────────────────────────────────────────────

    /// <summary>
    /// Called by Dashboard when the operator confirms a manual participant ID.
    /// Immediately overwrites the current ID, rewrites the pulse.txt header,
    /// and suppresses any further auto-polling so the choice is sticky.
    /// Passing an empty or whitespace string is a no-op (shows a warning).
    /// </summary>
    public void SetParticipantIdManually(string pid)
    {
        if (string.IsNullOrWhiteSpace(pid))
        {
            Debug.LogWarning("PulseSender.SetParticipantIdManually: empty ID ignored.");
            return;
        }
        if (_pollCoroutine != null)
        {
            StopCoroutine(_pollCoroutine);
            _pollCoroutine = null;
        }
        _manualOverride  = true;
        _participantId   = pid.Trim();
        RewriteHeader(_participantId);
        Debug.Log($"PulseSender: participant ID manually overridden → {_participantId}");
    }

    /// <summary>
    /// Clears a manual override and restarts auto-polling from the beginning.
    /// Only useful if the operator wants to revert to FIFO auto-assignment.
    /// </summary>
    public void ClearManualOverride()
    {
        _manualOverride  = false;
        _participantId   = "";
        WriteHeader("PENDING");
        _pollCoroutine   = StartCoroutine(PollParticipantId());
        Debug.Log("PulseSender: manual override cleared — auto-polling restarted.");
    }

    private IEnumerator PollParticipantId() {
        // Poll schedule: every 5 s for 30 s → every 10 s for 30 s → every 30 s × 4
        float[] delays = { 5f, 5f, 5f, 5f, 5f, 5f,
                           10f, 10f, 10f,
                           30f, 30f, 30f, 30f };

        foreach (float delay in delays) {
            yield return new WaitForSeconds(delay);

            // A manual override was applied while we were sleeping — stop polling.
            if (_manualOverride) yield break;

            if (!string.IsNullOrEmpty(_participantId)) yield break;

            yield return StartCoroutine(FetchParticipantId());

            if (!string.IsNullOrEmpty(_participantId)) {
                Debug.Log($"PulseSender: participant resolved → {_participantId}");
                RewriteHeader(_participantId);
                yield break;
            }
        }

        Debug.LogWarning($"PulseSender: participant_id never resolved for session {_sessionId}. Header left as PENDING.");
    }

    private IEnumerator FetchParticipantId() {
        if (string.IsNullOrEmpty(_sessionId)) yield break;

        string url = $"{analyticsApiUrl}/api/sessions/{_sessionId}";
        using (UnityWebRequest req = UnityWebRequest.Get(url)) {
            req.timeout = 5;
            yield return req.SendWebRequest();

            if (req.result == UnityWebRequest.Result.Success) {
                string pid = ExtractJsonString(req.downloadHandler.text, "participant_id");
                if (!string.IsNullOrEmpty(pid) && pid != "null")
                    _participantId = pid;
            } else {
                Debug.Log($"PulseSender: could not fetch participant_id from {url} — {req.result}: {req.error}");
            }
        }
    }

    // ── header helpers ─────────────────────────────────────────────

    private void WriteHeader(string value) {
        try {
            string existing = File.ReadAllText(_pulseLog);
            File.WriteAllText(_pulseLog, value + "\n" + existing);
        }
        catch (System.Exception e) {
            Debug.LogWarning($"PulseSender: WriteHeader failed — {e.Message}");
        }
    }

    private void RewriteHeader(string participantId) {
        try {
            string[] lines = File.ReadAllLines(_pulseLog);
            if (lines.Length > 0) lines[0] = participantId;
            File.WriteAllLines(_pulseLog, lines);
        }
        catch (System.Exception e) {
            Debug.LogWarning($"PulseSender: RewriteHeader failed — {e.Message}");
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

                if (!string.IsNullOrEmpty(externalApiUrl) && !string.IsNullOrEmpty(_participantId))
                    StartCoroutine(PostToExternalApi(hr));
            }
        }
    }

    // ── external API ───────────────────────────────────────────────

    /// POST { "UserId": <int>, "Pulse": <bpm> } to the research database.
    private IEnumerator PostToExternalApi(int bpm) {
        if (!int.TryParse(_participantId, out int userId)) {
            Debug.LogWarning($"PulseSender: participant_id '{_participantId}' is not an integer — skipping POST.");
            yield break;
        }

        string json = $"{{\"UserId\":{userId},\"Pulse\":{bpm}}}";
        byte[] bodyBytes = System.Text.Encoding.UTF8.GetBytes(json);

        using (UnityWebRequest req = new UnityWebRequest(externalApiUrl, "POST")) {
            req.uploadHandler      = new UploadHandlerRaw(bodyBytes);
            req.downloadHandler    = new DownloadHandlerBuffer();
            req.SetRequestHeader("Content-Type", "application/json");
            req.timeout            = 5;
            req.certificateHandler = new AcceptAllCertificatesPulse(); // self-signed TLS

            yield return req.SendWebRequest();

            if (req.result != UnityWebRequest.Result.Success)
                Debug.LogWarning($"PulseSender: external POST failed — {req.error}");
        }
    }

    // ── background file writer ─────────────────────────────────────

    private void WriteToFileLoop() {
        while (_isRunning) {
            if (_pulseQueue.TryDequeue(out string line)) {
                try {
                    using (StreamWriter w = new StreamWriter(_pulseLog, append: true))
                        w.WriteLine(line);
                }
                catch (System.Exception e) {
                    System.Console.WriteLine($"PulseSender write error: {e.Message}");
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
        int valEnd = json.IndexOfAny(new[] { ',', '}', ']' }, start);
        return valEnd < 0 ? json.Substring(start).Trim() : json.Substring(start, valEnd - start).Trim();
    }
}

/// <summary>
/// Bypasses TLS certificate validation for the self-signed cert on the
/// external research servers (10.200.130.36 / 10.200.130.98).
/// </summary>
public class AcceptAllCertificatesPulse : CertificateHandler {
    protected override bool ValidateCertificate(byte[] certificateData) => true;
}
