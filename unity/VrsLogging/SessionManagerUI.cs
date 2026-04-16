using UnityEngine;
using UnityEngine.UI;
using System;
using System.IO;
using System.Collections.Generic;

namespace VrsLogging
{
    /// <summary>
    /// Simple UI controller for creating and stopping recording sessions.
    ///
    /// Workflow
    /// --------
    /// 1. Operator types a subject prefix into <see cref="subjectInput"/>.
    /// 2. Clicks "New Session" → <see cref="OnNewSessionClicked"/>:
    ///    a. Calls <c>logger.GetNextDisplayId(prefix)</c> to get e.g. "ALICE-003".
    ///    b. Calls <c>logger.StartNewSession(sid, displayId, subject)</c> which
    ///       creates the session directory, starts the four VRSF writers, and writes
    ///       <c>manifest.json</c>.
    ///    c. Logs a <c>subject_start</c> JSON event via <c>logger.LogEvent</c>.
    ///    d. Refreshes the history list.
    /// 3. Clicks "Stop Session" → flushes + closes writers, writes end timestamp.
    ///
    /// History panel
    /// -------------
    /// <see cref="LoadHistory"/> reads <c>sessions_history.ndjson</c> (one JSON
    /// object per line), instantiates a <c>rowPrefab</c> per entry into
    /// <see cref="historyContainer"/>, and wires the resume/stop/open callbacks on
    /// the <see cref="SessionHistoryRow"/> component.
    /// </summary>
    public class SessionManagerUI : MonoBehaviour
    {
        public VrsSessionLogger logger;
        public InputField subjectInput;
        public Button newSessionButton;
        public Button stopSessionButton;
        public Text currentSessionLabel;
        // UI for session history
        public Transform historyContainer; // assign a Vertical Layout Group container
        public GameObject rowPrefab; // assign a prefab that has SessionHistoryRow component

        private string currentDisplayId = null;

        [Serializable]
        private class HistoryEntry
        {
            public string display_id;
            public ulong session_id;
            public string subject;
            public long started_unix_ms;
            public long ended_unix_ms;
            public string dir;
        }

        

        public void OnNewSessionClicked()
        {
            if (logger == null)
            {
                Debug.LogError("SessionManagerUI: logger reference not set");
                return;
            }

            // create display id using prefix and auto-increment, then start session
            string subject = subjectInput != null ? subjectInput.text : "SUBJ";
            // ask logger to compute display id and start session - logger will create session dir using display id
            string displayId = logger != null ? logger.GetNextDisplayId(subject) : null;
            if (string.IsNullOrEmpty(displayId)) displayId = subject + "-" + DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            ulong sid = (ulong)DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            // pass displayId and subjectLabel to StartNewSession
            logger.StartNewSession(sid, displayId, subject);
            currentDisplayId = displayId;
            if (currentSessionLabel != null) currentSessionLabel.text = $"Session: {displayId}";
            if (stopSessionButton != null) stopSessionButton.interactable = true;

            // write an event so the collector picks up the new-subject metadata
            var payload = new { event_type = "subject_start", session_id = sid, subject = subject, display_id = currentDisplayId, started_unix_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() };
            try
            {
                logger.LogEvent(payload);
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"Failed to log subject start event: {ex}");
            }

            Debug.Log($"SessionManagerUI: started session {sid} subject='{subject}'");
            // refresh history UI
            LoadHistory();
        }

        public void OnStopSessionClicked()
        {
            if (logger == null)
            {
                Debug.LogError("SessionManagerUI: logger reference not set");
                return;
            }
            try
            {
                logger.StopSession();
                if (currentSessionLabel != null) currentSessionLabel.text = "Session: (stopped)";
                if (stopSessionButton != null) stopSessionButton.interactable = false;
                Debug.Log("SessionManagerUI: stopped session");
                // refresh history UI
                LoadHistory();
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"OnStopSessionClicked failed: {ex}");
            }
        }

        void Awake()
        {
            // initial load of history will occur in Start after listeners wired
        }

        void Start()
        {
            // wire existing listeners as before
            if (newSessionButton != null)
            {
                newSessionButton.onClick.AddListener(OnNewSessionClicked);
            }
            if (stopSessionButton != null)
            {
                stopSessionButton.onClick.AddListener(OnStopSessionClicked);
                stopSessionButton.interactable = false;
            }

            // initial load of history
            LoadHistory();
        }

        /// <summary>
        /// Load sessions_history.ndjson from logger.logBasePath and populate the UI container.
        /// </summary>
        public void LoadHistory()
        {
            if (logger == null || historyContainer == null || rowPrefab == null) return;
            try
            {
                var historyPath = Path.Combine(logger.logBasePath, "sessions_history.ndjson");
                // clear existing
                for (int i = historyContainer.childCount - 1; i >= 0; i--) DestroyImmediate(historyContainer.GetChild(i).gameObject);
                if (!File.Exists(historyPath)) return;
                var lines = File.ReadAllLines(historyPath);
                var list = new List<HistoryEntry>();
                foreach (var ln in lines)
                {
                    if (string.IsNullOrWhiteSpace(ln)) continue;
                    try
                    {
                        var e = JsonUtility.FromJson<HistoryEntry>(ln);
                        if (e != null) list.Add(e);
                    }
                    catch { }
                }

                // populate UI (most recent last -> show recent at top)
                for (int i = list.Count - 1; i >= 0; i--)
                {
                    var e = list[i];
                    var go = Instantiate(rowPrefab, historyContainer);
                    var row = go.GetComponent<SessionHistoryRow>();
                    if (row == null)
                    {
                        // try to find text components directly as fallback
                        var t = go.GetComponentInChildren<Text>();
                        if (t != null) t.text = $"{e.display_id} / {e.subject}";
                        continue;
                    }

                    row.Setup(e.display_id, e.subject, e.session_id, e.started_unix_ms, e.ended_unix_ms, e.dir,
                        // resume callback: start a new session for same display id (Option A)
                        () => {
                            ulong nsid = (ulong)DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                            logger.StartNewSession(nsid, e.display_id, e.subject);
                            // log subject_start event
                            var payload = new { event_type = "subject_start", session_id = nsid, subject = e.subject, display_id = e.display_id, started_unix_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() };
                            try { logger.LogEvent(payload); } catch { }
                            LoadHistory();
                        },
                        // stop callback: stop current session
                        () => { logger.StopSession(); LoadHistory(); },
                        // open folder callback
                        () => { try { Application.OpenURL(e.dir); } catch { } }
                    );
                }
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"LoadHistory failed: {ex}");
            }
        }
    }
}
