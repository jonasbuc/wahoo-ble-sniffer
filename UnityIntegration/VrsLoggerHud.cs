using UnityEngine;

namespace VrsLogging
{
    public class VrsLoggerHud : MonoBehaviour
    {
        public VrsSessionLogger sessionLogger;
        public bool visible = false;
        void Update()
        {
            if (Input.GetKeyDown(KeyCode.F1)) visible = !visible;
        }

        void OnGUI()
        {
            if (!visible || sessionLogger == null) return;
            GUILayout.BeginArea(new Rect(10,10,420,300), "VRS Log");
            GUILayout.Label($"Session: {sessionLogger.sessionId} ({sessionLogger.SessionDir})");
            GUILayout.Label($"Last HR: {sessionLogger.LastHr:F1}");
            GUILayout.Label($"Head rate: {sessionLogger.headHz} Hz");
            GUILayout.Label($"Bike rate: {sessionLogger.bikeHz} Hz");
            GUILayout.Label($"Head total (approx): {sessionLogger.HeadQueueCount}");
            GUILayout.EndArea();
        }
    }
}
