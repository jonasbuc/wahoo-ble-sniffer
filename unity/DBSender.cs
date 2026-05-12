using System.IO;
using UnityEngine;
using System.Threading;
using System.Collections.Concurrent;

/// <summary>
/// Logs bike, head-transform, Arduino, and pulse (HR) data to local text files
/// at 1 Hz using a background writer thread.
///
/// Pulse data is received via WahooWsClient.OnHeartRate and written to:
///   Application.dataPath/CARLogs/pulse.txt
/// Each line:  |<unix_ms>|<heart_rate_bpm>
/// </summary>
public class DBSender : MonoBehaviour {

    // ── Inspector references ──────────────────────────────────────────
    [SerializeField] private Transform headTransform;
    [SerializeField] private Transform bikeHandleTransform;

    /// <summary>Assign the scene's WahooWsClient so we receive HR events.</summary>
    [SerializeField] private WahooWsClient wahooWsClient;

    public ArduinoSerialReader arduinoSerialReader;
    private float speed => arduinoSerialReader.speed;

    public bool shouldSend = false;

    // ── Log file paths (set in Start) ─────────────────────────────────
    private string bikeDataLog;
    private string headTransformLog;
    private string arduinoLog;
    private string pulseLog;

    // ── Per-channel queues ────────────────────────────────────────────
    private ConcurrentQueue<string> logQueue           = new ConcurrentQueue<string>();
    private ConcurrentQueue<string> headTransformQueue = new ConcurrentQueue<string>();
    private ConcurrentQueue<string> arduinoQueue       = new ConcurrentQueue<string>();
    private ConcurrentQueue<string> pulseQueue         = new ConcurrentQueue<string>();

    // ── Latest cached HR (written by main thread via OnHeartRate event) ─
    // Volatile so the write from the event handler is immediately visible
    // when the Update() loop reads it one frame later.
    private volatile int _latestHeartRate = 0;

    // ── Background thread ─────────────────────────────────────────────
    private Thread loggingThread;
    private bool isRunning = true;

    private float timeSinceLastLog;

    // ─────────────────────────────────────────────────────────────────

    void Start() {
        string logDir = Application.dataPath + "/CARLogs";
        Directory.CreateDirectory(logDir);   // safe if already exists

        bikeDataLog      = logDir + "/bikeData.txt";
        headTransformLog = logDir + "/headTransform.txt";
        arduinoLog       = logDir + "/arduino.txt";
        pulseLog         = logDir + "/pulse.txt";

        // Subscribe to HR events from the Wahoo bridge.
        if (wahooWsClient != null)
            wahooWsClient.OnHeartRate += OnHeartRateReceived;
        else
            Debug.LogWarning("DBSender: WahooWsClient not assigned — pulse will not be logged.");

        loggingThread = new Thread(WriteToFileLoop);
        loggingThread.IsBackground = true;
        loggingThread.Start();
    }

    // ── HR event handler (main thread) ───────────────────────────────

    private void OnHeartRateReceived(int hr) {
        _latestHeartRate = hr;
    }

    // ── 1 Hz sampling ────────────────────────────────────────────────

    void Update() {
        if (Time.time - timeSinceLastLog > 1f) {
            timeSinceLastLog = Time.time;

            // Capture all values on the main thread before handing to queues.
            float bikeHandleRotationY = bikeHandleTransform.rotation.y;
            Quaternion headRot = headTransform.rotation;
            Vector3 headPos    = headTransform.position;
            float currentSpeed = speed;
            int heartRate      = _latestHeartRate;
            long unixMs        = System.DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

            logQueue.Enqueue(
                $"\n\r|{Mathf.Round(bikeHandleRotationY * Mathf.Rad2Deg)}|{1}|{currentSpeed}");

            headTransformQueue.Enqueue(
                $"\n\r|{headRot.x}|{headRot.y}|{headRot.z}|{headRot.w}|{headPos.x}|{headPos.y}|{headPos.z}");

            arduinoQueue.Enqueue(
                $"\n\r{arduinoSerialReader.leftBrakeInd}|{arduinoSerialReader.rightBrakeInd}|{System.DateTime.UtcNow}");

            // Only log pulse when we have a real reading (> 0).
            if (heartRate > 0)
                pulseQueue.Enqueue($"\n\r|{unixMs}|{heartRate}");
        }
    }

    // ── Background file-writer ────────────────────────────────────────

    private void WriteToFileLoop() {
        while (isRunning) {
            bool didWork = false;

            if (logQueue.TryDequeue(out string line)) {
                WriteAppend(bikeDataLog, line, "bike data");
                didWork = true;
            }

            if (headTransformQueue.TryDequeue(out string head)) {
                WriteAppend(headTransformLog, head, "head transform");
                didWork = true;
            }

            if (arduinoQueue.TryDequeue(out string ard)) {
                WriteAppend(arduinoLog, ard, "arduino");
                didWork = true;
            }

            if (pulseQueue.TryDequeue(out string pulse)) {
                WriteAppend(pulseLog, pulse, "pulse");
                didWork = true;
            }

            if (!didWork)
                Thread.Sleep(100);
        }
    }

    private static void WriteAppend(string path, string content, string channel) {
        try {
            using (StreamWriter writer = new StreamWriter(path, append: true))
                writer.WriteLine(content);
        }
        catch (System.Exception e) {
            System.Console.WriteLine($"File Write Error ({channel}): {e.Message}");
        }
    }

    // ─────────────────────────────────────────────────────────────────

    private void OnApplicationQuit() {
        isRunning = false;

        if (wahooWsClient != null)
            wahooWsClient.OnHeartRate -= OnHeartRateReceived;

        if (loggingThread != null && loggingThread.IsAlive)
            loggingThread.Join(500);
    }

    private void OnDestroy() {
        // Guard against scene unload before OnApplicationQuit.
        isRunning = false;

        if (wahooWsClient != null)
            wahooWsClient.OnHeartRate -= OnHeartRateReceived;
    }
}
