using System.IO;
using UnityEngine;
using System.Threading;
using System.Collections.Concurrent;

/// <summary>
/// Logs pulse (heart-rate) data from the Wahoo bridge to a local text file.
/// All other sensor logging (bike, head-transform, Arduino) is handled elsewhere.
///
/// Output: Application.dataPath/CARLogs/pulse.txt
/// Format: |<unix_ms>|<heart_rate_bpm>
/// </summary>
public class DBSender : MonoBehaviour {

    /// <summary>Assign the scene's WahooWsClient in the Inspector.</summary>
    [SerializeField] private WahooWsClient wahooWsClient;

    private string pulseLog;

    private ConcurrentQueue<string> pulseQueue = new ConcurrentQueue<string>();

    // Cached HR value — written on main thread via OnHeartRate event,
    // read in Update(). volatile ensures immediate cross-thread visibility.
    private volatile int _latestHeartRate = 0;

    private Thread loggingThread;
    private bool isRunning = true;

    private float timeSinceLastLog;

    // ─────────────────────────────────────────────────────────────────

    void Start() {
        string logDir = Application.dataPath + "/CARLogs";
        Directory.CreateDirectory(logDir);
        pulseLog = logDir + "/pulse.txt";

        if (wahooWsClient != null)
            wahooWsClient.OnHeartRate += OnHeartRateReceived;
        else
            Debug.LogWarning("DBSender: WahooWsClient not assigned — pulse will not be logged.");

        loggingThread = new Thread(WriteToFileLoop);
        loggingThread.IsBackground = true;
        loggingThread.Start();
    }

    private void OnHeartRateReceived(int hr) {
        _latestHeartRate = hr;
    }

    void Update() {
        if (Time.time - timeSinceLastLog > 1f) {
            timeSinceLastLog = Time.time;

            int heartRate = _latestHeartRate;
            if (heartRate > 0) {
                long unixMs = System.DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                pulseQueue.Enqueue($"|{unixMs}|{heartRate}");
            }
        }
    }

    private void WriteToFileLoop() {
        while (isRunning) {
            if (pulseQueue.TryDequeue(out string line)) {
                try {
                    using (StreamWriter writer = new StreamWriter(pulseLog, append: true))
                        writer.WriteLine(line);
                }
                catch (System.Exception e) {
                    System.Console.WriteLine($"File Write Error (pulse): {e.Message}");
                }
            }
            else {
                Thread.Sleep(100);
            }
        }
    }

    private void Cleanup() {
        isRunning = false;
        if (wahooWsClient != null)
            wahooWsClient.OnHeartRate -= OnHeartRateReceived;
        if (loggingThread != null && loggingThread.IsAlive)
            loggingThread.Join(500);
    }

    private void OnApplicationQuit() => Cleanup();
    private void OnDestroy()         => Cleanup();
}
