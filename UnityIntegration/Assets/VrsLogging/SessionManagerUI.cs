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

        void Start()
        {
            if (newSessionButton != null)
            {
                newSessionButton.onClick.AddListener(OnNewSessionClicked);
            }
        }

        public void OnNewSessionClicked()
        {
            if (logger == null)
            {
                Debug.LogError("SessionManagerUI: logger reference not set");
                return;
            }

            // create a timestamp-based session id
            ulong sid = (ulong)DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            string subject = subjectInput != null ? subjectInput.text : null;
            logger.StartNewSession(sid, subject);

            // write an event so the collector picks up the new-subject metadata
            var payload = new { event_type = "subject_start", session_id = sid, subject = subject, started_unix_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() };
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
    }
}
