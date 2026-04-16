using UnityEngine;

/// <summary>
/// Attach to any trigger collider that spawns cars (or other obstacles).
/// When the player's bike enters the trigger zone, a timestamped event
/// is sent to the Wahoo Bridge via <see cref="WahooWsClient.SendEvent"/>,
/// which draws an orange vertical marker on the GUI's HR graph.
///
/// Setup:
///   1. Add a Collider (Box/Sphere/etc.) to the spawn zone and tick "Is Trigger".
///   2. Attach this script to the same GameObject.
///   3. Drag the scene's WahooWsClient into the <see cref="wsClient"/> field.
///   4. Optionally set <see cref="eventLabel"/> to a custom name (e.g. "zone_A").
///   5. The player bike must have a Rigidbody (or CharacterController) for
///      OnTriggerEnter to fire.
/// </summary>
public class SpawnZoneTrigger : MonoBehaviour
{
    [Tooltip("Reference to the WahooWsClient in the scene.")]
    [SerializeField] private WahooWsClient wsClient;

    [Tooltip("Event label shown on the GUI graph (e.g. 'spawn', 'zone_A').")]
    [SerializeField] private string eventLabel = "car_spawn";

    [Tooltip("Only trigger on objects with this tag. Leave empty to trigger on anything.")]
    [SerializeField] private string playerTag = "Player";

    [Tooltip("Minimum seconds between triggers (prevents rapid re-fires).")]
    [SerializeField] private float cooldown = 2.0f;

    private float _lastTriggerTime = -999f;

    private void OnTriggerEnter(Collider other)
    {
        // Filter by tag if set
        if (!string.IsNullOrEmpty(playerTag) && !other.CompareTag(playerTag))
            return;

        // Cooldown to avoid spamming on repeated collisions
        if (Time.time - _lastTriggerTime < cooldown)
            return;

        _lastTriggerTime = Time.time;

        // Send event to bridge → GUI graph
        if (wsClient != null)
        {
            wsClient.SendEvent(eventLabel);
            Debug.Log($"SpawnZoneTrigger: sent '{eventLabel}' event to bridge");
        }
        else
        {
            Debug.LogWarning("SpawnZoneTrigger: wsClient not assigned!");
        }
    }
}
