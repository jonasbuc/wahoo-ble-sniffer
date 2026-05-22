using System.Collections;
using System.Collections.Generic;
using System.IO;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.UI;

/// <summary>
/// Pre-session dashboard: shows system-readiness checks, the current
/// participant ID, and — new in this revision — a compact manual-override
/// row that lets the operator choose a different participant before starting.
///
/// ── Inspector wiring ──────────────────────────────────────────────────────
///   pulseSender            → scene PulseSender
///   dashboardCanvas        → root canvas GameObject
///   headSetCheckImg        → headset status image
///   allLogsCheckImg        → log files status image
///   rpiCheckImg            → RPI API status image
///   currentIdText          → TMP_Text that shows the active participant ID
///   startBridgeButton      → button that launches START_ALL.bat
///   startSessionButtonObj  → GameObject wrapping the start-session button
///
///   — new fields (override row) —
///   overrideIdInput        → TMP_InputField for the operator to type an ID
///   confirmOverrideButton  → Button that commits the typed ID
///   overrideStatusText     → (optional) small TMP_Text for feedback
///   questionnaireApiUrl    → base URL for the questionnaire service (default 8090)
///   dbSender               → (optional) if DBSender is also in the scene it
///                             receives the same override so both stay in sync
/// </summary>
public class Dashboard : MonoBehaviour
{
    // ── existing fields — do not reorder (serialised) ──────────────
    [SerializeField] private PulseSender pulseSender;
    public GameObject dashboardCanvas;

    [Header("Images")]
    [SerializeField] private Image headSetCheckImg;
    [SerializeField] private Image allLogsCheckImg;
    [SerializeField] private Image rpiCheckImg;

    [Header("Logs")]
    private string bikeDataLogPath;
    private string arduinoLogPath;
    private string headTransformLogPath;
    private string scenarioLogPath;
    private string fenceLogPath;
    private string pulseLogPath;

    [SerializeField] private TMPro.TMP_Text currentIdText;
    [SerializeField] private Button startBridgeButton;

    [Header("Filepath to SQLite databases")]
    [SerializeField] private string sqliteAnalysticsDBFile;
    [SerializeField] private string sqliteQuestionareDBFile;

    [SerializeField] private GameObject startSessionButtonObj;

    private float timeSinceLastCheck;
    public float TimeBetweenChecks;

    private Color final;     // kept for serialisation compatibility
    private Image[] allChecks;
    private string[] filePaths;
    private string rpiAPI = "https://10.200.130.36:5001/api/cardata";

    // ── new: manual participant-ID override row ─────────────────────
    [Header("Manual ID override (new — small row below current ID)")]

    [Tooltip("Assign an InputField placed directly below currentIdText in the Canvas.")]
    [SerializeField] private TMPro.TMP_InputField overrideIdInput;

    [Tooltip("Small confirm button next to the input field.")]
    [SerializeField] private Button confirmOverrideButton;

    [Tooltip("(Optional) Small label for override feedback, e.g. 'Override active'.")]
    [SerializeField] private TMPro.TMP_Text overrideStatusText;

    [Tooltip("Base URL for the questionnaire service — used to fetch registered participant IDs.")]
    [SerializeField] private string questionnaireApiUrl = "http://127.0.0.1:8090";

    [Tooltip("(Optional) Assign DBSender if it exists in the scene so it stays in sync with PulseSender.")]
    [SerializeField] private DBSender dbSender;

    // tracks whether the operator has applied a manual override
    private bool _overrideActive = false;

    // ── original private state (unchanged) ─────────────────────────
    private float rpiCheckCD = 5;
    private float timesinceLastRPICheck;
    private bool hasStarted;

    // ── lifecycle ──────────────────────────────────────────────────

    void Start()
    {
        dashboardCanvas.SetActive(true);
        rpiCheckImg.color = Color.red;

        allChecks    = new Image[2];
        allChecks[0] = headSetCheckImg;
        allChecks[1] = allLogsCheckImg;

        for (int i = 0; i < allChecks.Length; i++)
            allChecks[i].color = Color.red;

        timeSinceLastCheck = 0;
        final              = Color.green;
        startSessionButtonObj.SetActive(false);
        bikeDataLogPath      = Application.dataPath + "/CARLogs/bikeData.txt";
        headTransformLogPath = Application.dataPath + "/CARLogs/headTransform.txt";
        arduinoLogPath       = Application.dataPath + "/CARLogs/arduino.txt";
        scenarioLogPath      = Application.dataPath + "/CARLogs/scenario.txt";
        fenceLogPath         = Application.dataPath + "/CARLogs/fence.txt";
        pulseLogPath         = Application.dataPath + "/CARLogs/pulse.txt";

        filePaths    = new string[6];
        filePaths[0] = bikeDataLogPath;
        filePaths[1] = headTransformLogPath;
        filePaths[2] = arduinoLogPath;
        filePaths[3] = scenarioLogPath;
        filePaths[4] = fenceLogPath;
        filePaths[5] = pulseLogPath;

        hasStarted = false;

        // Wire the confirm button (can also be done via Inspector OnClick).
        if (confirmOverrideButton != null)
            confirmOverrideButton.onClick.AddListener(ConfirmOverride);

        // Populate the input placeholder with available IDs from questionnaire API.
        StartCoroutine(FetchAvailableParticipantIds());

        SetOverrideStatus("");

        // Show the correct initial state immediately on frame 0 so there is
        // never a blank/stale frame before Update fires.
        UpdateIdDisplay();
        AllCheck();
    }

    void Update()
    {
        if (hasStarted) return;

        // ── existing tick logic (unchanged) ────────────────────────
        if (Time.time - timeSinceLastCheck > TimeBetweenChecks)
        {
            timeSinceLastCheck = Time.time;
            AllCheck();
        }
        if (Time.time - timesinceLastRPICheck > rpiCheckCD)
        {
            timesinceLastRPICheck = Time.time;
            StartCoroutine(CheckRPICoroutine());
        }

        // ── new: keep currentIdText truthful ───────────────────────
        UpdateIdDisplay();
    }

    // ── checks (original, unchanged) ──────────────────────────────

    void AllCheck()
    {
        // Guard: if required references are missing, log once and bail out.
        if (headSetCheckImg == null || allLogsCheckImg == null)
        {
            Debug.LogWarning("[Dashboard] AllCheck: one or more check images are not wired in the Inspector.");
            return;
        }

        if (OVRManager.isHmdPresent)
            headSetCheckImg.color = OVRPlugin.userPresent ? Color.green : Color.red;

        bool allFilesExists = true;
        for (int i = 0; i < filePaths.Length; i++)
        {
            if (!File.Exists(filePaths[i]))
            {
                allFilesExists = false;
                break;
            }
        }
        allLogsCheckImg.color = allFilesExists ? Color.green : Color.red;

        // Valid if PulseSender has resolved an ID (auto or manual override).
        // If pulseSender is not wired the button stays hidden — fail safe.
        bool gotParticipantID = pulseSender != null && pulseSender.ParticipantId != "PENDING";

        if (startSessionButtonObj != null)
            startSessionButtonObj.SetActive(
                headSetCheckImg.color == Color.green &&
                allLogsCheckImg.color == Color.green &&
                gotParticipantID);
    }

    public static bool startedSim = false;

    public void StartSim()
    {
        startedSim = true;
        dashboardCanvas.SetActive(false);
        hasStarted = true;

        // Lock the override row so the ID cannot change mid-session.
        if (overrideIdInput != null)   overrideIdInput.interactable   = false;
        if (confirmOverrideButton != null) confirmOverrideButton.interactable = false;
    }

    public IEnumerator CheckRPICoroutine()
    {
        using (UnityWebRequest webRequest = new UnityWebRequest(rpiAPI + "/id/1", "GET"))
        {
            webRequest.certificateHandler = new BypassCertificate();
            webRequest.disposeCertificateHandlerOnDispose = true;

            yield return webRequest.SendWebRequest();

            rpiCheckImg.color = webRequest.result == UnityWebRequest.Result.Success
                ? Color.green
                : Color.red;
        }
    }

    public void StartPulseBridge()
    {
        System.Diagnostics.Process.Start(
            @"D:\CarProjektDiverse\WAHOOV2\wahoo-ble-sniffer\starters\START_ALL.bat");
    }

    // ── colour palette ─────────────────────────────────────────────
    // Centralised so adjusting one value updates the whole dashboard.
    private static readonly Color ColOk       = new Color(0.18f, 0.80f, 0.44f); // green
    private static readonly Color ColPending  = new Color(1.00f, 0.76f, 0.03f); // amber
    private static readonly Color ColError    = new Color(0.93f, 0.25f, 0.25f); // red
    private static readonly Color ColManual   = new Color(0.40f, 0.60f, 1.00f); // blue
    private static readonly Color ColLocked   = new Color(0.60f, 0.60f, 0.60f); // grey
    private static readonly Color ColNeutral  = new Color(0.85f, 0.85f, 0.85f); // off-white

    // ── new: ID display ────────────────────────────────────────────

    private string _lastDisplayedId = null;  // track transitions to trigger AllCheck

    private void UpdateIdDisplay()
    {
        if (currentIdText == null) return;
        if (pulseSender == null)
        {
            currentIdText.text  = "ID: (PulseSender not wired)";
            currentIdText.color = ColError;
            return;
        }

        string id = pulseSender.ParticipantId; // "PENDING" or resolved value

        if (_overrideActive)
        {
            currentIdText.text  = $"ID: {id}  [manual]";
            currentIdText.color = ColManual;          // blue — manual choice active
        }
        else if (id == "PENDING")
        {
            currentIdText.text  = "ID: PENDING (auto-linking…)";
            currentIdText.color = ColPending;         // amber — waiting
        }
        else
        {
            currentIdText.text  = $"ID: {id}";
            currentIdText.color = ColOk;              // green — ready
        }

        // When auto-link fires (PENDING → resolved) we immediately re-run
        // AllCheck so the start button activates without waiting for the next
        // TimeBetweenChecks interval.
        if (_lastDisplayedId == "PENDING" && id != "PENDING")
            AllCheck();

        _lastDisplayedId = id;
    }

    // ── new: manual override ───────────────────────────────────────

    /// <summary>
    /// Called when the operator presses the confirm button next to the
    /// input field.  Validates the typed ID, applies it to PulseSender
    /// (and optionally DBSender), and updates the UI.
    /// </summary>
    public void ConfirmOverride()
    {
        if (hasStarted)
        {
            SetOverrideStatus("Session already started — ID locked.", ColLocked);
            return;
        }

        string raw = overrideIdInput != null ? overrideIdInput.text.Trim() : "";

        if (string.IsNullOrEmpty(raw))
        {
            SetOverrideStatus("⚠ Enter a participant ID first.", ColPending);
            return;
        }

        // Enforce integer-only (participant IDs are positive whole numbers).
        if (!int.TryParse(raw, out int numericId) || numericId < 1)
        {
            SetOverrideStatus("⚠ ID must be a positive integer (e.g. 1, 2, 3).", ColError);
            return;
        }

        string pid = numericId.ToString();

        // Guard: PulseSender must be wired for the override to have any effect.
        if (pulseSender == null)
        {
            SetOverrideStatus("⚠ PulseSender not wired — cannot apply override.", ColError);
            Debug.LogError("[Dashboard] ConfirmOverride: pulseSender is not assigned in the Inspector.");
            return;
        }

        // Apply to PulseSender — stops auto-polling, rewrites pulse.txt header.
        pulseSender.SetParticipantIdManually(pid);

        // Keep DBSender in sync if present in the scene.
        if (dbSender != null)
            dbSender.SetParticipantIdManually(pid);

        _overrideActive = true;
        SetOverrideStatus($"✓ Override active: {pid}", ColOk);

        Debug.Log($"[Dashboard] Participant ID manually set to: {pid}");

        // Immediately re-run the readiness check so the start button activates
        // without waiting for the next tick.
        AllCheck();
    }

    /// <summary>Updates the small override-status label (safe if null).</summary>
    private void SetOverrideStatus(string msg, Color? colour = null)
    {
        if (overrideStatusText == null) return;
        overrideStatusText.text  = msg;
        overrideStatusText.color = colour ?? ColNeutral;
    }

    // ── new: populate input placeholder from questionnaire API ─────

    /// <summary>
    /// Fetches registered participant IDs from the questionnaire service and
    /// uses them to populate the InputField placeholder text so the operator
    /// knows which IDs are valid.  Non-fatal — a failure leaves the placeholder
    /// as the default hint text.
    /// </summary>
    private IEnumerator FetchAvailableParticipantIds()
    {
        string url = $"{questionnaireApiUrl}/api/participants";
        using (UnityWebRequest req = UnityWebRequest.Get(url))
        {
            req.timeout = 5;
            yield return req.SendWebRequest();

            if (req.result != UnityWebRequest.Result.Success)
            {
                Debug.Log($"[Dashboard] Could not fetch participants from questionnaire API ({req.error}) — placeholder left as default hint.");
                // Degrade gracefully: set a static hint so the operator knows
                // the field is editable even without a live service.
                if (overrideIdInput != null)
                {
                    var ph = overrideIdInput.placeholder as TMPro.TMP_Text;
                    if (ph != null)
                        ph.text = "Enter participant ID (e.g. 1)";
                }
                yield break;
            }

            // Parse the JSON array [ {...}, {...} ] without a heavy library.
            // We only need the participant_id strings for the placeholder hint.
            var ids = new List<string>();
            string json = req.downloadHandler.text;
            int searchFrom = 0;
            while (true)
            {
                string pid = ExtractNextParticipantId(json, ref searchFrom);
                if (pid == null) break;
                ids.Add(pid);
            }

            if (ids.Count > 0 && overrideIdInput != null)
            {
                // Put the list into the placeholder so the operator can see
                // registered IDs at a glance.  e.g. "Available: 1, 2, 3"
                string hint = "Available: " + string.Join(", ", ids);

                // TMP_InputField.placeholder is a Graphic component — cast safely.
                var ph = overrideIdInput.placeholder as TMPro.TMP_Text;
                if (ph != null)
                    ph.text = hint;
                else
                    Debug.LogWarning("[Dashboard] overrideIdInput placeholder is not a TMP_Text — cannot set hint.");
            }
        }
    }

    // ── tiny JSON helpers ──────────────────────────────────────────

    /// <summary>
    /// Scans forward through <paramref name="json"/> extracting successive
    /// "participant_id" values.  <paramref name="pos"/> is updated on each
    /// call so iteration works correctly in a while loop.
    /// </summary>
    private static string ExtractNextParticipantId(string json, ref int pos)
    {
        const string key = "\"participant_id\"";
        int ki = json.IndexOf(key, pos);
        if (ki < 0) return null;
        int colon = json.IndexOf(':', ki + key.Length);
        if (colon < 0) { pos = json.Length; return null; }
        int start = colon + 1;
        while (start < json.Length && json[start] == ' ') start++;
        if (start >= json.Length) { pos = json.Length; return null; }

        string value;
        if (json[start] == '"')
        {
            int end = json.IndexOf('"', start + 1);
            if (end < 0) { pos = json.Length; return null; }
            value = json.Substring(start + 1, end - start - 1);
            pos   = end + 1;
        }
        else
        {
            int valEnd = json.IndexOfAny(new[] { ',', '}', ']' }, start);
            if (valEnd < 0) valEnd = json.Length;
            value = json.Substring(start, valEnd - start).Trim();
            pos   = valEnd;
        }

        return string.IsNullOrEmpty(value) || value == "null" ? null : value;
    }
}
