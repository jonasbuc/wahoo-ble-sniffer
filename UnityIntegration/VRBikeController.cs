using UnityEngine;

/// <summary>
/// Example VR bike controller using real KICKR SNAP data
/// Attach this to your VR bike GameObject with a Rigidbody
/// </summary>
[RequireComponent(typeof(Rigidbody))]
public class VRBikeController : MonoBehaviour
{
    [Header("References")]
    [SerializeField] private WahooBLEManager wahooBLE;
    [SerializeField] private Transform bikeModel; // Optional: for visual rotation
    [SerializeField] private Transform frontWheel;
    [SerializeField] private Transform rearWheel;

    [Header("Physics Settings")]
    [SerializeField] private float maxSpeed = 50f; // km/h
    [SerializeField] private float acceleration = 2f;
    [SerializeField] private float deceleration = 3f;
    [SerializeField] private float gravityMultiplier = 1f;
    
    [Header("Wheel Animation")]
    [SerializeField] private bool animateWheels = true;
    [SerializeField] private float wheelRadius = 0.35f; // meters

    [Header("Audio (Optional)")]
    [SerializeField] private AudioSource chainAudio;
    [SerializeField] private float minCadenceForSound = 20f;

    private Rigidbody rb;
    private float currentSpeedKmh = 0f;
    private float wheelRotation = 0f;

    void Start()
    {
        rb = GetComponent<Rigidbody>();

        // Find WahooBLEManager if not assigned
        if (wahooBLE == null)
        {
            wahooBLE = FindObjectOfType<WahooBLEManager>();
            
            if (wahooBLE == null)
            {
                Debug.LogError("[VRBike] WahooBLEManager not found! Please assign it or add to scene.");
                enabled = false;
                return;
            }
        }

        // Subscribe to events
        wahooBLE.OnDataReceived += OnCyclingDataReceived;
        wahooBLE.OnKickrConnected += OnWahooConnected;
        wahooBLE.OnKickrDisconnected += OnWahooDisconnected;

        Debug.Log("[VRBike] Controller initialized");
    }

    void FixedUpdate()
    {
        if (wahooBLE == null || !wahooBLE.IsKickrConnected)
        {
            // No data, gradually slow down
            currentSpeedKmh = Mathf.Lerp(currentSpeedKmh, 0f, deceleration * Time.fixedDeltaTime);
        }
        else
        {
            // Use real KICKR data for target speed
            float targetSpeedKmh = wahooBLE.Speed;
            
            // Clamp to max speed
            targetSpeedKmh = Mathf.Min(targetSpeedKmh, maxSpeed);
            
            // Smoothly interpolate current speed
            if (targetSpeedKmh > currentSpeedKmh)
            {
                currentSpeedKmh = Mathf.Lerp(currentSpeedKmh, targetSpeedKmh, acceleration * Time.fixedDeltaTime);
            }
            else
            {
                currentSpeedKmh = Mathf.Lerp(currentSpeedKmh, targetSpeedKmh, deceleration * Time.fixedDeltaTime);
            }
        }

        // Apply physics movement
        ApplyMovement();

        // Animate wheels
        if (animateWheels)
        {
            AnimateWheels();
        }

        // Update audio
        UpdateAudio();
    }

    private void ApplyMovement()
    {
        // Convert km/h to m/s
        float speedMetersPerSec = currentSpeedKmh / 3.6f;

        // Move bike forward based on current speed
        Vector3 velocity = transform.forward * speedMetersPerSec;
        
        // Preserve vertical velocity for gravity/jumping
        velocity.y = rb.velocity.y;

        rb.velocity = velocity;

        // Apply custom gravity
        if (!IsGrounded())
        {
            rb.AddForce(Physics.gravity * gravityMultiplier, ForceMode.Acceleration);
        }
    }

    private void AnimateWheels()
    {
        if (currentSpeedKmh < 0.1f) return;

        // Calculate wheel rotation based on speed
        // rotation (deg/s) = (speed m/s) / (2 * PI * radius) * 360
        float speedMetersPerSec = currentSpeedKmh / 3.6f;
        float rotationSpeed = (speedMetersPerSec / (2f * Mathf.PI * wheelRadius)) * 360f;
        
        wheelRotation += rotationSpeed * Time.fixedDeltaTime;
        wheelRotation = wheelRotation % 360f;

        // Apply rotation to wheels
        if (frontWheel != null)
        {
            frontWheel.localRotation = Quaternion.Euler(wheelRotation, 0f, 0f);
        }
        
        if (rearWheel != null)
        {
            rearWheel.localRotation = Quaternion.Euler(wheelRotation, 0f, 0f);
        }
    }

    private void UpdateAudio()
    {
        if (chainAudio == null || wahooBLE == null) return;

        // Play chain sound based on cadence
        if (wahooBLE.Cadence >= minCadenceForSound)
        {
            if (!chainAudio.isPlaying)
            {
                chainAudio.Play();
            }
            
            // Adjust pitch based on cadence (60rpm = 1.0 pitch)
            float pitchFactor = wahooBLE.Cadence / 60f;
            chainAudio.pitch = Mathf.Clamp(pitchFactor, 0.5f, 2f);
            
            // Adjust volume based on power
            float volumeFactor = Mathf.Clamp01(wahooBLE.Power / 200f);
            chainAudio.volume = Mathf.Lerp(0.3f, 1f, volumeFactor);
        }
        else
        {
            if (chainAudio.isPlaying)
            {
                chainAudio.Stop();
            }
        }
    }

    private bool IsGrounded()
    {
        // Simple ground check - raycast downward
        float rayDistance = 1.1f;
        return Physics.Raycast(transform.position, Vector3.down, rayDistance);
    }

    // Event handlers
    private void OnCyclingDataReceived(WahooBLEManager.CyclingData data)
    {
        // Custom logic when new data arrives
        // For example, trigger haptic feedback based on power
        if (data.power > 250)
        {
            // High power - could trigger strong vibration
        }
    }

    private void OnWahooConnected()
    {
        Debug.Log("[VRBike] KICKR connected - real data active!");
    }

    private void OnWahooDisconnected()
    {
        Debug.LogWarning("[VRBike] KICKR disconnected - coasting...");
    }

    void OnDestroy()
    {
        if (wahooBLE != null)
        {
            wahooBLE.OnDataReceived -= OnCyclingDataReceived;
            wahooBLE.OnKickrConnected -= OnWahooConnected;
            wahooBLE.OnKickrDisconnected -= OnWahooDisconnected;
        }
    }

    // Public methods for UI/debugging
    public float GetCurrentSpeed() => currentSpeedKmh;
    public float GetCurrentPower() => wahooBLE?.Power ?? 0f;
    public float GetCurrentCadence() => wahooBLE?.Cadence ?? 0f;
    public int GetCurrentHeartRate() => wahooBLE?.HeartRate ?? 0;
    public bool IsDataActive() => wahooBLE != null && wahooBLE.IsKickrConnected;

#if UNITY_EDITOR
    void OnGUI()
    {
        // Debug overlay in editor
        if (!Application.isPlaying) return;

        GUIStyle style = new GUIStyle();
        style.fontSize = 20;
        style.normal.textColor = Color.white;
        
        string status = wahooBLE?.IsKickrConnected == true ? "CONNECTED" : "DISCONNECTED";
        GUI.Label(new Rect(10, 10, 400, 30), $"Status: {status}", style);
        GUI.Label(new Rect(10, 40, 400, 30), $"Speed: {GetCurrentSpeed():F1} km/h", style);
        GUI.Label(new Rect(10, 70, 400, 30), $"Power: {GetCurrentPower():F0} W", style);
        GUI.Label(new Rect(10, 100, 400, 30), $"Cadence: {GetCurrentCadence():F0} rpm", style);
        GUI.Label(new Rect(10, 130, 400, 30), $"Heart Rate: {GetCurrentHeartRate()} bpm", style);
    }
#endif
}
