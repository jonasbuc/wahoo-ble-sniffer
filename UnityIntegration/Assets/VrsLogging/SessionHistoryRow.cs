using System;
using UnityEngine;
using UnityEngine.UI;

namespace VrsLogging
{
    /// <summary>
    /// Small helper attached to a row prefab used by SessionManagerUI to render a history entry.
    /// Expected prefab content: three Texts (display/subject/times) and three Buttons (resume/stop/open).
    /// </summary>
    public class SessionHistoryRow : MonoBehaviour
    {
        public Text displayIdText;
        public Text subjectText;
        public Text timesText;

        public Button resumeButton;
        public Button stopButton;
        public Button openButton;

        private ulong sessionId;

        public void Setup(string displayId, string subject, ulong sessId, long startedUnixMs, long endedUnixMs, string dir, Action onResume, Action onStop, Action onOpen)
        {
            sessionId = sessId;
            if (displayIdText != null) displayIdText.text = displayId ?? "";
            if (subjectText != null) subjectText.text = subject ?? "";

            string started = startedUnixMs > 0 ? UnixMsToLocal(startedUnixMs) : "-";
            string ended = endedUnixMs > 0 ? UnixMsToLocal(endedUnixMs) : "(open)";
            if (timesText != null) timesText.text = $"{started} → {ended}";

            if (resumeButton != null)
            {
                resumeButton.onClick.RemoveAllListeners();
                if (onResume != null) resumeButton.onClick.AddListener(() => onResume());
            }
            if (stopButton != null)
            {
                stopButton.onClick.RemoveAllListeners();
                if (onStop != null) stopButton.onClick.AddListener(() => onStop());
            }
            if (openButton != null)
            {
                openButton.onClick.RemoveAllListeners();
                if (onOpen != null) openButton.onClick.AddListener(() => onOpen());
            }
        }

        private string UnixMsToLocal(long ms)
        {
            try
            {
                var dt = DateTimeOffset.FromUnixTimeMilliseconds(ms).ToLocalTime();
                return dt.ToString("yyyy-MM-dd HH:mm:ss");
            }
            catch
            {
                return "?";
            }
        }
    }
}
