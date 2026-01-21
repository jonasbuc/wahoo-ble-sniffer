using UnityEngine;

/// <summary>
/// Controls bike/player movement using Wahoo KICKR data
/// Attach this to your bike/player GameObject
/// REQUIRES: WahooDataReceiver component in the scene
/// </summary>
public class BikeMovementController : MonoBehaviour
{
    [Header("References")]
    [SerializeField] private WahooDataReceiver wahooReceiver;
    
    [Header("Movement Settings")]
    [SerializeField] private float speedMultiplier = 0.5f;  // How fast to move (adjust this!)
    [SerializeField] private bool useRigidbody = false;     // Use physics or transform movement?
    [SerializeField] private Transform bikeModel;           // Optional: rotate wheels based on speed
    
    [Header("Wheel Rotation (Optional)")]
    [SerializeField] private Transform frontWheel;
    [SerializeField] private Transform rearWheel;
    [SerializeField] private float wheelRotationMultiplier = 100f;
    
    [Header("Debug")]
    [SerializeField] private bool showDebugInfo = true;
    
    // Components
    private Rigidbody rb;
    private CharacterController characterController;
    
    // State
    private float currentSpeed = 0f;  // km/h from KICKR
    private float currentCadence = 0f;
    private float currentPower = 0f;

    void Start()
    {
        // Auto-find WahooDataReceiver if not assigned
        if (wahooReceiver == null)
        {
            wahooReceiver = FindObjectOfType<WahooDataReceiver>();
            if (wahooReceiver == null)
            {
                Debug.LogError("[BikeMovement] No WahooDataReceiver found! Add one to the scene.");
                enabled = false;
                return;
            }
        }
        
        // Subscribe to data updates
        wahooReceiver.OnDataReceived += OnWahooDataReceived;
        
        // Get components
        rb = GetComponent<Rigidbody>();
        characterController = GetComponent<CharacterController>();
        
        Debug.Log("[BikeMovement] ✓ Ready! Waiting for KICKR data...");
    }

    void OnDestroy()
    {
        // Unsubscribe to prevent memory leaks
        if (wahooReceiver != null)
        {
            wahooReceiver.OnDataReceived -= OnWahooDataReceived;
        }
    }

    /// <summary>
    /// Called every time new data arrives from KICKR
    /// </summary>
    private void OnWahooDataReceived(WahooDataReceiver.CyclingData data)
    {
        currentSpeed = data.speed;      // km/h
        currentCadence = data.cadence;  // RPM
        currentPower = data.power;      // Watts
        
        if (showDebugInfo)
        {
            Debug.Log($"[BikeMovement] Speed: {currentSpeed:F1} km/h | Cadence: {currentCadence:F0} rpm | Power: {currentPower} W");
        }
    }

    void Update()
    {
        // Use the smoothed values from receiver for smooth movement
        float smoothSpeed = wahooReceiver.Speed;  // Already smoothed!
        
        // Convert km/h to Unity units per second
        // km/h → m/s: divide by 3.6
        // Then multiply by your world scale
        float moveSpeed = (smoothSpeed / 3.6f) * speedMultiplier;
        
        // Move forward based on speed
        MoveForward(moveSpeed);
        
        // Rotate wheels (optional visual effect)
        RotateWheels(smoothSpeed);
        
        // Debug display
        if (showDebugInfo && Time.frameCount % 30 == 0)
        {
            Debug.Log($"[BikeMovement] Moving at {moveSpeed:F2} m/s (from {smoothSpeed:F1} km/h)");
        }
    }

    /// <summary>
    /// Move the bike forward
    /// </summary>
    private void MoveForward(float speed)
    {
        if (speed <= 0.01f)
        {
            // Stopped - no movement
            return;
        }
        
        // METHOD 1: Transform movement (simple, works always)
        if (!useRigidbody && characterController == null)
        {
            // Move forward in local space
            transform.position += transform.forward * speed * Time.deltaTime;
        }
        
        // METHOD 2: CharacterController (if you have one)
        else if (characterController != null)
        {
            Vector3 moveDirection = transform.forward * speed;
            characterController.SimpleMove(moveDirection);
        }
        
        // METHOD 3: Rigidbody physics (if you have physics)
        else if (useRigidbody && rb != null)
        {
            Vector3 velocity = transform.forward * speed;
            velocity.y = rb.velocity.y;  // Preserve gravity
            rb.velocity = velocity;
        }
    }

    /// <summary>
    /// Rotate wheels based on speed (visual effect)
    /// </summary>
    private void RotateWheels(float speedKmh)
    {
        if (frontWheel == null && rearWheel == null)
            return;
        
        // Calculate rotation based on speed
        float rotationSpeed = speedKmh * wheelRotationMultiplier * Time.deltaTime;
        
        if (frontWheel != null)
        {
            frontWheel.Rotate(rotationSpeed, 0f, 0f, Space.Self);
        }
        
        if (rearWheel != null)
        {
            rearWheel.Rotate(rotationSpeed, 0f, 0f, Space.Self);
        }
    }

    /// <summary>
    /// Optional: Use power to simulate resistance/difficulty
    /// </summary>
    public float GetCurrentPower()
    {
        return currentPower;
    }

    /// <summary>
    /// Optional: Use cadence for animations
    /// </summary>
    public float GetCurrentCadence()
    {
        return currentCadence;
    }

    void OnGUI()
    {
        if (!showDebugInfo) return;
        
        // Simple on-screen display
        GUI.color = Color.white;
        GUIStyle style = new GUIStyle();
        style.fontSize = 24;
        style.normal.textColor = Color.white;
        
        GUI.Label(new Rect(10, 10, 400, 30), $"Speed: {currentSpeed:F1} km/h", style);
        GUI.Label(new Rect(10, 40, 400, 30), $"Cadence: {currentCadence:F0} rpm", style);
        GUI.Label(new Rect(10, 70, 400, 30), $"Power: {currentPower} W", style);
        
        // Connection status
        if (wahooReceiver != null)
        {
            GUI.color = wahooReceiver.IsConnected ? Color.green : Color.red;
            GUI.Label(new Rect(10, 100, 400, 30), 
                wahooReceiver.IsConnected ? "● Connected" : "● Disconnected", style);
        }
    }
}
