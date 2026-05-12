/// <summary>
/// Standalone mock of DBSender.cs — no Unity required.
/// Tests: file creation, PENDING header, pulse line format,
///        participant_id resolution + header rewrite, JSON extraction.
/// Run with: dotnet run  (from tests/mock_dbsender/)
/// </summary>

using System;
using System.IO;
using System.Threading;
using System.Collections.Generic;
using System.Collections.Concurrent;

// ── Stripped-down DBSender logic (no MonoBehaviour / UnityWebRequest) ──────

class DBSenderCore {

    public string PulseLog { get; }

    private string _sessionId;
    private string _participantId = "";
    private ConcurrentQueue<string> _queue = new ConcurrentQueue<string>();
    private bool _running = true;
    private Thread _writer;

    public DBSenderCore(string logPath, string sessionId) {
        PulseLog   = logPath;
        _sessionId = sessionId;

        File.WriteAllText(PulseLog, "");          // clean slate
        WriteHeader("PENDING");

        _writer = new Thread(WriteLoop) { IsBackground = true };
        _writer.Start();
    }

    // Called once participant_id is resolved (simulates PollParticipantId success)
    public void ResolveParticipant(string participantId) {
        _participantId = participantId;
        RewriteHeader(participantId);
    }

    // Called every 1 s with a HR value (simulates Update() + queue)
    public void EnqueuePulse(int bpm) {
        long unixMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        _queue.Enqueue($"{unixMs}|{bpm}");
    }

    public void Stop() {
        _running = false;
        _writer.Join(1000);
    }

    // ── header helpers (copied verbatim from DBSender.cs) ───────────────────

    private void WriteHeader(string value) {
        string existing = File.ReadAllText(PulseLog);
        File.WriteAllText(PulseLog, value + "\n" + existing);
    }

    private void RewriteHeader(string participantId) {
        string[] lines = File.ReadAllLines(PulseLog);
        if (lines.Length > 0) lines[0] = participantId;
        File.WriteAllLines(PulseLog, lines);
    }

    // ── background writer (copied verbatim) ─────────────────────────────────

    private void WriteLoop() {
        while (_running) {
            if (_queue.TryDequeue(out string line)) {
                using (StreamWriter w = new StreamWriter(PulseLog, append: true))
                    w.WriteLine(line);
            } else {
                Thread.Sleep(50);
            }
        }
    }

    // ── JSON helper (copied verbatim from DBSender.cs) ───────────────────────

    public static string ExtractJsonString(string json, string key) {
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
        int valEnd = json.IndexOfAny(new[]{ ',', '}', ']' }, start);
        return valEnd < 0 ? json.Substring(start).Trim()
                          : json.Substring(start, valEnd - start).Trim();
    }
}

// ── Test harness ─────────────────────────────────────────────────────────────

class Program {

    static int _pass = 0, _fail = 0;

    static void Assert(bool condition, string label) {
        if (condition) { Console.WriteLine($"  ✓  {label}"); _pass++; }
        else           { Console.WriteLine($"  ✗  {label}"); _fail++; }
    }

    static void Main() {
        string tmpLog = Path.Combine(Path.GetTempPath(), $"pulse_mock_{Guid.NewGuid():N}.txt");

        Console.WriteLine("\n=== DBSender mock run ===\n");

        // ── T1: JSON helper ───────────────────────────────────────────────────
        // participant_id is an integer in the DB — the API returns it as a JSON number.
        Console.WriteLine("T1  ExtractJsonString");
        Assert(DBSenderCore.ExtractJsonString(@"{""participant_id"":42}", "participant_id") == "42",
               "integer value extracted as string");
        Assert(DBSenderCore.ExtractJsonString(@"{""participant_id"":null}", "participant_id") == "null",
               "null value → \"null\"");
        Assert(DBSenderCore.ExtractJsonString(@"{""session_id"":""1234"",""participant_id"":7}", "participant_id") == "7",
               "second key in object (int)");
        Assert(DBSenderCore.ExtractJsonString(@"{""other"":""x""}", "participant_id") == null,
               "missing key → null");

        // ── T2: File created + PENDING header ────────────────────────────────
        Console.WriteLine("\nT2  Session start — PENDING header");
        var sender = new DBSenderCore(tmpLog, "SESSION_001");
        Thread.Sleep(100); // let writer thread settle
        string[] lines = File.ReadAllLines(tmpLog);
        Assert(lines.Length >= 1,       "file exists and has content");
        Assert(lines[0] == "PENDING",   "line 1 is PENDING");

        // ── T3: Pulse lines written in correct format ─────────────────────────
        Console.WriteLine("\nT3  Pulse line format  <unix_ms>|<bpm>");
        sender.EnqueuePulse(72);
        sender.EnqueuePulse(74);
        sender.EnqueuePulse(71);
        Thread.Sleep(300);  // give writer thread time to flush

        lines = File.ReadAllLines(tmpLog);
        // lines[0] = PENDING header; lines[1..] = pulse data
        int pulseLines = lines.Length - 1;
        Assert(pulseLines == 3, $"3 pulse lines written (got {pulseLines})");
        foreach (var pl in lines[1..]) {
            string[] parts = pl.Split('|');
            bool twoFields = parts.Length == 2;
            bool longMs    = twoFields && parts[0].Length >= 13;   // unix ms has 13+ digits
            bool validBpm  = twoFields && int.TryParse(parts[1], out int bpm) && bpm > 0;
            Assert(twoFields && longMs && validBpm, $"  line \"{pl}\" → unix_ms|bpm format OK");
        }

        // ── T4: Participant resolves → header rewritten ───────────────────────
        Console.WriteLine("\nT4  Participant resolution — header rewritten");
        sender.EnqueuePulse(73);
        Thread.Sleep(100);
        sender.ResolveParticipant("42");   // int stored as string in the file
        Thread.Sleep(100);
        lines = File.ReadAllLines(tmpLog);
        Assert(lines[0] == "42", $"line 1 rewritten to participant_id (got \"{lines[0]}\")");

        // ── T5: Pulse lines added AFTER resolve still have correct format ──────
        Console.WriteLine("\nT5  Pulse after resolve — format unchanged");
        sender.EnqueuePulse(77);
        Thread.Sleep(200);
        lines = File.ReadAllLines(tmpLog);
        string last = lines[lines.Length - 1];
        string[] lastParts = last.Split('|');
        Assert(lastParts.Length == 2 && lastParts[1] == "77",
               $"post-resolve pulse line OK → \"{last}\"");

        // ── T6: File content summary ──────────────────────────────────────────
        Console.WriteLine("\nT6  Final file content");
        sender.Stop();
        lines = File.ReadAllLines(tmpLog);
        Console.WriteLine($"       {lines.Length} lines total in {tmpLog}");
        Console.WriteLine("       ---- file ----");
        foreach (var l in lines) Console.WriteLine($"       {l}");
        Console.WriteLine("       -------------");
        Assert(lines[0] == "42", "participant_id (int) is first line");
        Assert(lines.Length >= 5,  "at least 5 lines (header + 4 pulse samples)");

        // ── cleanup ───────────────────────────────────────────────────────────
        File.Delete(tmpLog);

        // ── summary ───────────────────────────────────────────────────────────
        Console.WriteLine($"\n=== Results: {_pass} passed, {_fail} failed ===\n");
        Environment.Exit(_fail > 0 ? 1 : 0);
    }
}
