Session-historik og runtime session management (Unity)
=====================================================

Denne fil beskriver hvordan du aktiverer og bruger den nye session-historik og runtime session-start/stop i Unity.

Kort:
- Loggeren skriver `Logs/sessions_history.ndjson` med én JSON-per-linje: {display_id, session_id, subject, started_unix_ms, ended_unix_ms, dir}.
- `SessionManagerUI` kan indlæse denne fil og vise rækker (Resume / Stop / Open).
- Resume opretter en ny `session_id` for samme `display_id` (Option A — anbefalet).

Vigtige filer
-------------
- `UnityIntegration/Assets/VrsLogging/VrsSessionLogger.cs` (logger + history persistence)
- `UnityIntegration/VrsSessionLogger.cs` (kopi)
- `UnityIntegration/Assets/VrsLogging/SessionManagerUI.cs` (UI loader og callbacks)
- `UnityIntegration/Assets/VrsLogging/SessionHistoryRow.cs` (row controller)
- `Logs/sessions_history.ndjson` (oprettes automatisk når en session startes)

Opsætning i Unity Editor
------------------------
1) Opret container
   - I din UI Canvas: Create Empty → navngiv `HistoryContainer`.
   - Tilføj `Vertical Layout Group` og `Content Size Fitter` (Vertical Fit = Preferred Size).

2) Opret row-prefab
   - Opret GameObject `HistoryRowTemplate`.
   - Tilføj børn:
     - `Text` (DisplayId) → bind til `SessionHistoryRow.displayIdText`
     - `Text` (Subject) → bind til `SessionHistoryRow.subjectText`
     - `Text` (Times) → bind til `SessionHistoryRow.timesText`
     - `Button` (Resume) → bind til `SessionHistoryRow.resumeButton`
     - `Button` (Stop) → bind til `SessionHistoryRow.stopButton`
     - `Button` (Open) → bind til `SessionHistoryRow.openButton`
   - Tilføj `SessionHistoryRow` komponent og sæt referencer.
   - Lav prefab af template og fjern/disable template i Hierarchy.

3) Wire `SessionManagerUI`
   - På det GameObject der har `SessionManagerUI` script:
     - `logger` → træk `VrsSessionLogger` komponent
     - `subjectInput` → InputField
     - `newSessionButton` → Button
     - `stopSessionButton` → Button
     - `currentSessionLabel` → Text
     - `historyContainer` → `HistoryContainer` fra step 1
     - `rowPrefab` → prefab fra step 2

4) Test (Play mode)
   - Tryk Play.
   - Start en ny session via UI. Tjek `Logs/` og `sessions_history.ndjson`.
   - Stop; `ended_unix_ms` opdateres.
   - Resume fra historik starter en ny session (nyt session_id) for samme display_id.

Fejlsøgning
-----------
- NullReferenceException: sørg for at `rowPrefab` og `historyContainer` er sat i `SessionManagerUI`.
- Hvis Open ikke virker på macOS: brug `Application.OpenURL("file://" + dir)` i callback.
- Hvis `sessions_history.ndjson` mangler: start en session (logger opretter filen).

Design-note
-----------
Resume = Option A (nyt session_id for samme display_id) valgt som standard for robusthed og dataintegritet.

Commit og branch
----------------
Ændringer er committet og pushed til branch `analysis/quick-start`.

Kontakt
-------
Sig til hvis du vil have at jeg også ændrer `Application.OpenURL`-adfærden eller genererer et eksempel-prefab JSON/inspektor-skema.
