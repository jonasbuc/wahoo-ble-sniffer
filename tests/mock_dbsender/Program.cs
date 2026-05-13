/// <summary>
/// Standalone mock of DBSender.cs — no Unity required.
/// Tests: file creation, PENDING header, pulse line format,
///        participant_id resolution + header rewrite, JSON extraction,
///        full end-to-end flow with mock HTTP questionnaire + analytics servers.
/// Run with: dotnet run  (from tests/mock_dbsender/)
/// </summary>

using System;
using System.IO;
using System.Net;
using System.Text;
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

// ── Minimal in-process HTTP server (no external deps) ────────────────────────

/// <summary>
/// Tiny HttpListener wrapper that handles one route at a time.
/// Simulates both the questionnaire API and the analytics API.
/// </summary>
class MockHttpServer : IDisposable {

    private readonly HttpListener _listener;
    private readonly Thread _thread;
    private volatile bool _running = true;

    // Registered participants:  participant_id → session_id (null = unlinked)
    public Dictionary<string, string?> Participants { get; } = new();

    // Recorded external pulse POSTs:  (userId, bpm)
    public List<(int userId, int bpm)> ExternalPostsReceived { get; } = new();

    public int QuestionnaireCalls { get; private set; }
    public int AnalyticsCalls     { get; private set; }

    public MockHttpServer(string prefix) {
        _listener = new HttpListener();
        _listener.Prefixes.Add(prefix);
        _listener.Start();
        _thread = new Thread(Listen) { IsBackground = true };
        _thread.Start();
    }

    // Register a participant (unlinked — no session yet)
    public void AddParticipant(string participantId) =>
        Participants[participantId] = null;

    // Simulate the analytics FIFO auto-linker: link oldest unlinked to a session
    public void LinkParticipant(string participantId, string sessionId) =>
        Participants[participantId] = sessionId;

    private void Listen() {
        while (_running) {
            HttpListenerContext ctx;
            try { ctx = _listener.GetContext(); }
            catch { break; }

            ThreadPool.QueueUserWorkItem(_ => Handle(ctx));
        }
    }

    private void Handle(HttpListenerContext ctx) {
        string path   = ctx.Request.Url.AbsolutePath;
        string method = ctx.Request.HttpMethod;
        string body   = "";

        using (var sr = new StreamReader(ctx.Request.InputStream, Encoding.UTF8))
            body = sr.ReadToEnd();

        string resp   = "{}";
        int    status = 200;

        // ── Questionnaire API ─────────────────────────────────────────────────

        // POST /api/participants  →  register participant
        if (method == "POST" && path == "/api/participants") {
            QuestionnaireCalls++;
            string pid = ExtractField(body, "participant_id");
            if (pid != null) {
                if (!Participants.ContainsKey(pid)) Participants[pid] = null;
                resp = $"{{\"participant_id\":{pid},\"session_id\":null}}";
            } else {
                status = 422; resp = "{\"detail\":\"missing participant_id\"}";
            }
        }

        // GET /api/participants/oldest-unlinked  →  FIFO
        else if (method == "GET" && path == "/api/participants/oldest-unlinked") {
            QuestionnaireCalls++;
            string? oldest = null;
            foreach (var kv in Participants)
                if (kv.Value == null) { oldest = kv.Key; break; }

            if (oldest != null)
                resp = $"{{\"participant_id\":{oldest},\"session_id\":null}}";
            else
                { status = 404; resp = "{\"detail\":\"no unlinked participants\"}"; }
        }

        // ── Analytics API ─────────────────────────────────────────────────────

        // GET /api/sessions/{id}  →  return session detail with participant_id
        else if (method == "GET" && path.StartsWith("/api/sessions/")) {
            AnalyticsCalls++;
            string sessionId = path.Substring("/api/sessions/".Length).TrimEnd('/');
            // Find participant linked to this session
            string? pid = null;
            foreach (var kv in Participants)
                if (kv.Value == sessionId) { pid = kv.Key; break; }

            resp = pid != null
                ? $"{{\"session_id\":\"{sessionId}\",\"participant_id\":{pid}}}"
                : $"{{\"session_id\":\"{sessionId}\",\"participant_id\":null}}";
        }

        // ── External research API ─────────────────────────────────────────────

        // POST /api/car/logbikedata  →  receive live pulse
        else if (method == "POST" && (path == "/api/car/logbikedata" || path == "/api/cardatasqlite")) {
            string uStr = ExtractField(body, "UserId");
            string pStr = ExtractField(body, "Pulse");
            if (int.TryParse(uStr, out int uid) && int.TryParse(pStr, out int pulse))
                ExternalPostsReceived.Add((uid, pulse));
            resp = "{\"ok\":true}";
        }

        byte[] buf = Encoding.UTF8.GetBytes(resp);
        ctx.Response.StatusCode = status;
        ctx.Response.ContentType = "application/json";
        ctx.Response.ContentLength64 = buf.Length;
        ctx.Response.OutputStream.Write(buf, 0, buf.Length);
        ctx.Response.OutputStream.Close();
    }

    // Tiny field extractor — handles "key":"val" and "key":123
    private static string? ExtractField(string json, string key) {
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
        return valEnd < 0 ? json.Substring(start).Trim()
                          : json.Substring(start, valEnd - start).Trim();
    }

    public void Dispose() {
        _running = false;
        try { _listener.Stop(); } catch { }
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

        // ── T7: End-to-end — questionnaire register → analytics link → pulse file ─
        Console.WriteLine("\nT7  End-to-end: questionnaire register → participant resolves → pulse logged");

        string sessionId = "1778600000000";   // 13-digit unix-ms session id
        string participantId = "7";           // integer participant id

        // Start mock servers on loopback (choose ports unlikely to clash)
        using var srv = new MockHttpServer("http://127.0.0.1:19080/");

        // 1. Researcher registers participant in questionnaire UI (before headset goes on)
        {
            var req = System.Net.WebRequest.Create("http://127.0.0.1:19080/api/participants");
            req.Method = "POST";
            req.ContentType = "application/json";
            byte[] b = Encoding.UTF8.GetBytes($"{{\"participant_id\":{participantId},\"display_name\":\"Jonas\"}}");
            req.ContentLength = b.Length;
            using (var s = req.GetRequestStream()) s.Write(b, 0, b.Length);
            using var res = (HttpWebResponse)req.GetResponse();
            Assert(res.StatusCode == HttpStatusCode.OK, "T7.1 — POST /api/participants returns 200");
            Assert(srv.Participants.ContainsKey(participantId), "T7.2 — participant stored in mock server");
        }

        // 2. Verify questionnaire has an oldest-unlinked entry (as analytics FIFO would see it)
        {
            var req = System.Net.WebRequest.Create("http://127.0.0.1:19080/api/participants/oldest-unlinked");
            using var res = (HttpWebResponse)req.GetResponse();
            using var sr  = new StreamReader(res.GetResponseStream(), Encoding.UTF8);
            string body   = sr.ReadToEnd();
            string pid    = DBSenderCore.ExtractJsonString(body, "participant_id");
            Assert(res.StatusCode == HttpStatusCode.OK, "T7.3 — GET /api/participants/oldest-unlinked returns 200");
            Assert(pid == participantId, $"T7.4 — oldest-unlinked participant_id = {participantId} (got \"{pid}\")");
        }

        // 3. Analytics FIFO auto-linker runs: link participant → session
        srv.LinkParticipant(participantId, sessionId);

        // 4. DBSenderCore polls GET /api/sessions/{id} and gets participant_id back
        {
            var req = System.Net.WebRequest.Create($"http://127.0.0.1:19080/api/sessions/{sessionId}");
            using var res = (HttpWebResponse)req.GetResponse();
            using var sr  = new StreamReader(res.GetResponseStream(), Encoding.UTF8);
            string body   = sr.ReadToEnd();
            string pid    = DBSenderCore.ExtractJsonString(body, "participant_id");
            Assert(res.StatusCode == HttpStatusCode.OK, "T7.5 — GET /api/sessions/{id} returns 200");
            Assert(pid == participantId, $"T7.6 — session response contains participant_id = {participantId} (got \"{pid}\")");
        }

        // 5. DBSenderCore resolves participant, enqueues pulse, rewrites header
        string e2eLog = Path.Combine(Path.GetTempPath(), $"pulse_e2e_{Guid.NewGuid():N}.txt");
        var e2eSender = new DBSenderCore(e2eLog, sessionId);

        // Simulate a few HR samples arriving before participant resolves
        e2eSender.EnqueuePulse(68);
        e2eSender.EnqueuePulse(70);
        Thread.Sleep(200);

        // Participant resolves (as PollParticipantId would do on success)
        e2eSender.ResolveParticipant(participantId);
        Thread.Sleep(100);

        // More pulse samples after resolution
        e2eSender.EnqueuePulse(72);
        e2eSender.EnqueuePulse(74);
        e2eSender.EnqueuePulse(73);
        Thread.Sleep(200);

        e2eSender.Stop();
        string[] e2eLines = File.ReadAllLines(e2eLog);

        Assert(e2eLines.Length >= 1,                  "T7.7  — pulse file has content");
        Assert(e2eLines[0] == participantId,           $"T7.8  — line 1 = participant_id \"{participantId}\" (got \"{e2eLines[0]}\")");

        int e2ePulseCount = e2eLines.Length - 1;
        Assert(e2ePulseCount == 5,                     $"T7.9  — 5 pulse lines written (got {e2ePulseCount})");

        // Verify every pulse line is valid unix_ms|bpm
        bool allValid = true;
        foreach (var pl in e2eLines[1..]) {
            string[] parts = pl.Split('|');
            if (parts.Length != 2 || parts[0].Length < 13 || !int.TryParse(parts[1], out int bpm) || bpm <= 0)
                allValid = false;
        }
        Assert(allValid, "T7.10 — all pulse lines match unix_ms|bpm format");

        // Spot-check the BPM values appear in order (68, 70, 72, 74, 73)
        int[] expectedBpms = { 68, 70, 72, 74, 73 };
        bool bpmsMatch = true;
        for (int i = 0; i < expectedBpms.Length; i++) {
            string[] parts = e2eLines[i + 1].Split('|');
            if (!int.TryParse(parts[1], out int got) || got != expectedBpms[i])
                bpmsMatch = false;
        }
        Assert(bpmsMatch, $"T7.11 — BPM values in order: {string.Join(", ", expectedBpms)}");

        Console.WriteLine($"\n       File preview ({e2eLines.Length} lines):");
        foreach (var l in e2eLines) Console.WriteLine($"         {l}");

        File.Delete(e2eLog);

        // ── cleanup ───────────────────────────────────────────────────────────
        File.Delete(tmpLog);

        // ── summary ───────────────────────────────────────────────────────────
        Console.WriteLine($"\n=== Results: {_pass} passed, {_fail} failed ===\n");
        Environment.Exit(_fail > 0 ? 1 : 0);
    }
}
