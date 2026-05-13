using System.IO;
using UnityEngine;
using System.Collections;
using System.Collections.Generic;
using System.Threading;
using System.Collections.Concurrent;
using UnityEngine.Networking;

/// <summary>
/// Unified data logger for the VR cycling session.
///
/// Logs written to Application.dataPath/CARLogs/:
///   bikeData.txt      — steering angle | gear | speed  (1 Hz)
///   headTransform.txt — head quaternion + position      (1 Hz)
///   arduino.txt       — brake channels + UTC timestamp  (1 Hz)
///   pulse.txt         — participant_id (line 1) + unix_ms|bpm (1 Hz)
///
/// Pulse samples are also forwarded live to the external research API:
///   Body: { "UserId": <participant_id as int>, "Pulse": <bpm> }
///
/// External API URLs:
///   RPI:    https://10.200.130.36:5001/api/cardatasqlite
///   Laptop: https://10.200.130.98:5001/api/car/logbikedata
/// </summary>
public class DBSender : MonoBehaviour {

    // ── Inspector — bike / head / arduino ──────────────────────────

    [SerializeField] private Transform headTransform;
    [SerializeField] private Transform bikeHandleTransform;
    public ArduinoSerialReader arduinoSerialReader;

    // ── Inspector — pulse ──────────────────────────────────────────

    /// <summary>Assign the scene's WahooWsClient in the Inspector.</summary>
    [SerializeField] private WahooWsClient wahooWsClient;

    /// <summary>Assign TelemetryPublisher in the Inspector to read SessionId.</summary>
    [SerializeField] private LiveAnalytics.TelemetryPublisher telemetryPublisher;

    [Tooltip("Base URL of the local analytics API.")]
    [SerializeField] private string analyticsApiUrl = "http://127.0.0.1:8080";

    /// <summary>
    /// External research API for live pulse forwarding (self-signed TLS).
    ///   RPI:    https://10.200.130.36:5001/api/cardatasqlite
    ///   Laptop: https://10.200.130.98:5001/api/car/logbikedata
    /// Leave empty to disable.
    /// </summary>
    [Tooltip("External research API URL. Leave empty to disable live pulse forwarding.")]
    [SerializeField] private string externalApiUrl = "https://10.200.130.98:5001/api/car/logbikedata";

    // ── file paths ─────────────────────────────────────────────────

    private string bikeDataLog;
    private string headTransformLog;
    private string arduinoLog;
    private string pulseLog;

    // ── bike / head / arduino queues ───────────────────────────────

    private ConcurrentQueue<string> logQueue           = new ConcurrentQueue<string>();
    private ConcurrentQueue<string> headTransformQueue = new ConcurrentQueue<string>();
    private ConcurrentQueue<string> arduinoQueue       = new ConcurrentQueue<string>();

    // ── pulse state ────────────────────────────────────────────────

    private ConcurrentQueue<string> _pulseQueue = new ConcurrentQueue<string>();
    private volatile int _latestHeartRate = 0;
    private string _sessionId     = "";
    private string _participantId = "";   // empty until analytics API resolves it

    // ── shared threading ───────────────────────────────────────────

    private Thread loggingThread;
    private bool   isRunning = true;
    private float  timeSinceLastLog;

    private float speed => arduinoSerialReader != null ? arduinoSerialReader.speed : 0f;

    // ── lifecycle ──────────────────────────────────────────────────

    void Start() {
        string logDir = Path.Combine(Application.dataPath, "CARLogs");
        Directory.CreateDirectory(logDir);

        bikeDataLog      = Path.Combine(logDir, "bikeData.txt");
        headTransformLog = Path.Combine(logDir, "headTransform.txt");
        arduinoLog       = Path.Combine(logDir, "arduino.txt");
        pulseLog         = Path.Combine(logDir, "pulse.txt");

        // Wipe pulse log so each session starts clean (other logs append).
        File.WriteAllText(pulseLog, "");

        // Subscribe to Wahoo HR events.
        if (wahooWsClient != null)
            wahooWsClient.OnHeartRate += OnHeartRateReceived;
        else
            Debug.LogWarning("DBSender: WahooWsClient not assigned — pulse will not be logged.");

        // Single background thread handles all four log files.
        loggingThread = new Thread(WriteToFileLoop) { IsBackground = true };
        loggingThread.Start();

        // Wait one frame so TelemetryPublisher.Start() has set SessionId.
        StartCoroutine(InitPulseSession());
    }

    // ── session bootstrap (pulse) ──────────────────────────────────

    private IEnumerator InitPulseSession() {
        yield return null; // one frame delay

        _sessionId = telemetryPublisher != null ? telemetryPublisher.SessionId : "";
        if (string.IsNullOrEmpty(_sessionId))
            Debug.LogWarning("DBSender: SessionId not available from TelemetryPublisher.");

        WriteHeader("PENDING");
        StartCoroutine(PollParticipantId());
    }

    private IEnumerator PollParticipantId() {
        // Poll schedule: every 5 s x 6 → every 10 s x 3 → every 30 s x 4
        float[] delays = { 5f, 5f, 5f, 5f, 5f, 5f,
                           10f, 10f, 10f,
                           30f, 30f, 30f, 30f };

        foreach (float delay in delays) {
            yield return new WaitForSeconds(delay);

            if (!string.IsNullOrEmpty(_participantId)) yield break;

            yield return StartCoroutine(FetchParticipantId());

            if (!string.IsNullOrEmpty(_participantId)) {
                Debug.Log($"DBSender: participant resolved → {_participantId}");
                RewriteHeader(_participantId);
                yield break;
            }
        }

        Debug.LogWarning($"DBSender: participant_id never resolved for session {_sessionId}. Header left as PENDING.");
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
            }
        }
    }

    // ── pulse header helpers ───────────────────────────────────────

    private void WriteHeader(string value) {
        try {
            string existing = File.ReadAllText(pulseLog);
            File.WriteAllText(pulseLog, value + "\n" + existing);
        }
        catch (System.Exception e) {
            Debug.LogWarning($"DBSender: WriteHeader failed — {e.Message}");
        }
    }

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

    // ── Update — all sensors at 1 Hz ──────────────────────────────

    void Update() {
        if (Time.time - timeSinceLastLog > 1f) {
            timeSinceLastLog = Time.time;

            // ── bike / head / arduino ──────────────────────────────
            if (bikeHandleTransform != null) {
                float bikeHandleRotationY = bikeHandleTransform.rotation.y;
                string dataLineBikedata = $"\n\r|{Mathf.Round(bikeHandleRotationY * Mathf.Rad2Deg)}|{1}|{speed}";
                logQueue.Enqueue(dataLineBikedata);
            }

            if (headTransform != null) {
                Quaternion headRot = headTransform.rotation;
                Vector3    headPos = headTransform.position;
                string dataLineHeadTransform = $"\n\r|{headRot.x}|{headRot.y}|{headRot.z}|{headRot.w}|{headPos.x}|{headPos.y}|{headPos.z}";
                headTransformQueue.Enqueue(dataLineHeadTransform);
            }

            if (arduinoSerialReader != null) {
                string dataLineArduino = $"\n\r{arduinoSerialReader.leftBrakeInd}|{arduinoSerialReader.rightBrakeInd}|{System.DateTime.UtcNow}";
                arduinoQueue.Enqueue(dataLineArduino);
            }

            // ── pulse ──────────────────────────────────────────────
            int hr = _latestHeartRate;
            if (hr > 0) {
                long unixMs = System.DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                _pulseQueue.Enqueue($"{unixMs}|{hr}");

                // Forward live to external research API once participant is known.
                if (!string.IsNullOrEmpty(externalApiUrl) && !string.IsNullOrEmpty(_participantId))
                    StartCoroutine(PostToExternalApi(hr));
            }
        }
    }

    // ── external pulse API ─────────────────────────────────────────

    /// POST { "UserId": <participant_id as int>, "Pulse": <bpm> }
    private IEnumerator PostToExternalApi(int bpm) {
        if (!int.TryParse(_participantId, out int userId)) {
            Debug.LogWarning($"DBSender: participant_id '{_participantId}' is not an integer — skipping external POST.");
            yield break;
        }

        string json = $"{{\"UserId\":{userId},\"Pulse\":{bpm}}}";
        byte[] bodyBytes = System.Text.Encoding.UTF8.GetBytes(json);

        using (UnityWebRequest req = new UnityWebRequest(externalApiUrl, "POST")) {
            req.uploadHandler      = new UploadHandlerRaw(bodyBytes);
            req.downloadHandler    = new DownloadHandlerBuffer();
            req.SetRequestHeader("Content-Type", "application/json");
            req.timeout            = 5;
            req.certificateHandler = new AcceptAllCertificatesDB(); // self-signed TLS

            yield return req.SendWebRequest();

            if (req.result != UnityWebRequest.Result.Success)
                Debug.LogWarning($"DBSender: external POST failed — {req.error}");
        }
    }

    // ── background file writer ─────────────────────────────────────

    private void WriteToFileLoop() {
        while (isRunning) {
            // bike data
            if (logQueue.TryDequeue(out string bikeLine)) {
                try {
                    using (StreamWriter w = new StreamWriter(bikeDataLog, true))
                        w.WriteLine(bikeLine);
                }
                catch (System.Exception e) {
                    System.Console.WriteLine($"File Write Error (bike data): {e.Message}");
                }
            }
            else { Thread.Sleep(100); }

            // head transform
            if (headTransformQueue.TryDequeue(out string headLine)) {
                try {
                    using (StreamWriter w = new StreamWriter(headTransformLog, true))
                        w.WriteLine(headLine);
                }
                catch (System.Exception e) {
                    System.Console.WriteLine($"File Write Error (head transform): {e.Message}");
                }
            }
            else { Thread.Sleep(100); }

            // arduino
            if (arduinoQueue.TryDequeue(out string ardLine)) {
                try {
                    using (StreamWriter w = new StreamWriter(arduinoLog, true))
                        w.WriteLine(ardLine);
                }
                catch (System.Exception e) {
                    System.Console.WriteLine($"File Write Error (arduino): {e.Message}");
                }
            }
            else { Thread.Sleep(100); }

            // pulse
            if (_pulseQueue.TryDequeue(out string pulseLine)) {
                try {
                    using (StreamWriter w = new StreamWriter(pulseLog, append: true))
                        w.WriteLine(pulseLine);
                }
                catch (System.Exception e) {
                    System.Console.WriteLine($"File Write Error (pulse): {e.Message}");
                }
            }
            else { Thread.Sleep(100); }
        }
    }

    // ── teardown ───────────────────────────────────────────────────

    private void OnApplicationQuit() {
        isRunning = false;
        if (wahooWsClient != null)
            wahooWsClient.OnHeartRate -= OnHeartRateReceived;
        if (loggingThread != null && loggingThread.IsAlive)
            loggingThread.Join(500);
    }

    private void OnDestroy() {
        isRunning = false;
        if (wahooWsClient != null)
            wahooWsClient.OnHeartRate -= OnHeartRateReceived;
    }

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
public class AcceptAllCertificatesDB : CertificateHandler {
    protected override bool ValidateCertificate(byte[] certificateData) => true;
}
