using UnityEngine;
using UnityEngine.UI;
using System;

namespace VrsLogging
{
    /// <summary>
    /// Simple UI helper to start new test-subject sessions at runtime.
    /// Attach to a Canvas and wire the InputField and Button in the inspector.
    /// </summary>
    public class SessionManagerUI : MonoBehaviour
    {
        public VrsSessionLogger logger;
        public InputField subjectInput;
        public Button newSessionButton;
        public Button stopSessionButton;
        public Text currentSessionLabel;

        private string currentDisplayId = null;

        void Start()
        {
            if (newSessionButton != null)
            {
                newSessionButton.onClick.AddListener(OnNewSessionClicked);
            }
            if (stopSessionButton != null)
            {
                stopSessionButton.onClick.AddListener(OnStopSessionClicked);
                stopSessionButton.interactable = false;
            }
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
            string displayId = logger != null ? logger.GetType().GetMethod("GetNextDisplayId", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance).Invoke(logger, new object[] { subject }) as string : null;
            if (string.IsNullOrEmpty(displayId)) displayId = subject + "-" + DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            ulong sid = (ulong)DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            logger.StartNewSession(sid, subject);
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
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"OnStopSessionClicked failed: {ex}");
            }
        }
    }
}
