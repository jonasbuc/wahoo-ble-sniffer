using UnityEngine;
using System;
using System.Collections;
using System.Collections.Generic;

/// <summary>
/// Direct Bluetooth LE connection to Wahoo devices in Unity
/// Uses Bluetooth LE for iOS, tvOS and Android plugin by Shatalmic
/// Get it free: https://assetstore.unity.com/packages/tools/network/bluetooth-le-for-ios-tvos-and-android-26661
/// </summary>
public class WahooBLEManager : MonoBehaviour
{
    [Header("Device Settings")]
    [SerializeField] private string kickrNameFilter = "KICKR";
    [SerializeField] private string tickrNameFilter = "TICKR";
    [SerializeField] private bool autoConnect = true;
    [SerializeField] private float scanTimeout = 10f;

    [Header("Current Data (Read-Only)")]
    [SerializeField] private int currentPower = 0;         // Watts
    [SerializeField] private float currentCadence = 0f;    // RPM
    [SerializeField] private float currentSpeed = 0f;      // km/h
    [SerializeField] private int currentHeartRate = 0;     // BPM

    [Header("Smoothing")]
    [SerializeField] private bool enableSmoothing = true;
    [SerializeField] private float smoothingFactor = 0.3f;

    // Public properties
    public int Power => enableSmoothing ? (int)smoothedPower : currentPower;
    public float Cadence => enableSmoothing ? smoothedCadence : currentCadence;
    public float Speed => enableSmoothing ? smoothedSpeed : currentSpeed;
    public int HeartRate => currentHeartRate;
    public bool IsKickrConnected { get; private set; }
    public bool IsTickrConnected { get; private set; }

    // Events
    public event Action<CyclingData> OnDataReceived;
    public event Action OnKickrConnected;
    public event Action OnKickrDisconnected;
    public event Action OnTickrConnected;
    public event Action OnTickrDisconnected;

    // GATT Service UUIDs
    private const string CYCLING_POWER_SERVICE = "00001818-0000-1000-8000-00805f9b34fb";
    private const string CYCLING_POWER_MEASUREMENT = "00002a63-0000-1000-8000-00805f9b34fb";
    private const string HEART_RATE_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb";
    private const string HEART_RATE_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb";

    // Smoothing
    private float smoothedPower = 0f;
    private float smoothedCadence = 0f;
    private float smoothedSpeed = 0f;

    // BLE state
    private string kickrDeviceAddress;
    private string tickrDeviceAddress;
    private bool isScanning = false;

    [Serializable]
    public class CyclingData
    {
        public double timestamp;
        public int power;
        public float cadence;
        public float speed;
        public int heart_rate;
    }

    void Start()
    {
        Debug.Log("[WahooBLE] Initializing...");

        // Request permissions on Android (required from API level 31 / Android 12+)
        // BLE scanning and connecting are split into separate permissions:
        //   BLUETOOTH_SCAN    – allows scanning for nearby devices
        //   BLUETOOTH_CONNECT – allows connecting to paired/discovered devices
        // FineLocation is still required on older Android versions (< API 31) to
        // receive BLE advertisement results.
        #if UNITY_ANDROID
        if (!Permission.HasUserAuthorizedPermission(Permission.FineLocation))
        {
            Permission.RequestUserPermission(Permission.FineLocation);
        }
        if (!Permission.HasUserAuthorizedPermission("android.permission.BLUETOOTH_SCAN"))
        {
            Permission.RequestUserPermission("android.permission.BLUETOOTH_SCAN");
        }
        if (!Permission.HasUserAuthorizedPermission("android.permission.BLUETOOTH_CONNECT"))
        {
            Permission.RequestUserPermission("android.permission.BLUETOOTH_CONNECT");
        }
        #endif

        // Initialize BLE
        BluetoothLEHardwareInterface.Initialize(true, false, () => 
        {
            Debug.Log("[WahooBLE] ✓ BLE initialized");
            
            if (autoConnect)
            {
                ScanAndConnect();
            }
        }, (error) => 
        {
            Debug.LogError($"[WahooBLE] Initialize error: {error}");
        });
    }

    public void ScanAndConnect()
    {
        if (isScanning)
        {
            Debug.LogWarning("[WahooBLE] Already scanning!");
            return;
        }

        isScanning = true;
        Debug.Log($"[WahooBLE] Scanning for Wahoo devices ({scanTimeout}s)...");

        // Start scanning
        BluetoothLEHardwareInterface.ScanForPeripheralsWithServices(null, (address, name) =>
        {
            if (!string.IsNullOrEmpty(name))
            {
                // Check for trainer/power device
                if (name.ToUpper().Contains(kickrNameFilter.ToUpper()) && string.IsNullOrEmpty(kickrDeviceAddress))
                {
                    Debug.Log($"[WahooBLE] Found trainer/power device: {name} at {address}");
                    kickrDeviceAddress = address;
                    ConnectToKickr();
                }

                // Check for TICKR
                if (name.ToUpper().Contains(tickrNameFilter.ToUpper()) && string.IsNullOrEmpty(tickrDeviceAddress))
                {
                    Debug.Log($"[WahooBLE] Found TICKR: {name} at {address}");
                    tickrDeviceAddress = address;
                    ConnectToTickr();
                }

                // Stop scanning if both found
                if (!string.IsNullOrEmpty(kickrDeviceAddress) && !string.IsNullOrEmpty(tickrDeviceAddress))
                {
                    StopScan();
                }
            }
        }, null);

        // Auto-stop scan after timeout
        Invoke(nameof(StopScan), scanTimeout);
    }

    private void StopScan()
    {
        if (isScanning)
        {
            BluetoothLEHardwareInterface.StopScan();
            isScanning = false;
            Debug.Log("[WahooBLE] Scan stopped");
        }
    }

    private void ConnectToKickr()
    {
    Debug.Log("[WahooBLE] Connecting to trainer/power device...");

        BluetoothLEHardwareInterface.ConnectToPeripheral(
            kickrDeviceAddress,
            (address) => 
            {
                Debug.Log($"[WahooBLE] ✓ Connected to trainer/power device");
                IsKickrConnected = true;
                OnKickrConnected?.Invoke();
            },
            (address, serviceUUID) => 
            {
                Debug.Log($"[WahooBLE] Service discovered: {serviceUUID}");
            },
            (address, serviceUUID, characteristicUUID) => 
            {
                Debug.Log($"[WahooBLE] Characteristic discovered: {characteristicUUID}");

                // Subscribe to Cycling Power Measurement
                if (characteristicUUID.ToLower() == CYCLING_POWER_MEASUREMENT.ToLower())
                {
                    Debug.Log("[WahooBLE] Subscribing to Cycling Power notifications...");
                    
                    BluetoothLEHardwareInterface.SubscribeCharacteristicWithDeviceAddress(
                        kickrDeviceAddress,
                        CYCLING_POWER_SERVICE,
                        CYCLING_POWER_MEASUREMENT,
                        (notifyAddress, notifyCharacteristic) => 
                        {
                            Debug.Log("[WahooBLE] ✓ Subscribed to Cycling Power");
                        },
                        (address, characteristic, data) => 
                        {
                            HandleCyclingPowerData(data);
                        }
                    );
                }
            },
            (address) => 
            {
                Debug.LogWarning($"[WahooBLE] trainer/power device disconnected");
                IsKickrConnected = false;
                OnKickrDisconnected?.Invoke();
                kickrDeviceAddress = null;

                // Auto-reconnect after 5 seconds
                Invoke(nameof(ScanAndConnect), 5f);
            }
        );
    }

    private void ConnectToTickr()
    {
        Debug.Log("[WahooBLE] Connecting to TICKR...");

        BluetoothLEHardwareInterface.ConnectToPeripheral(
            tickrDeviceAddress,
            (address) => 
            {
                Debug.Log($"[WahooBLE] ✓ Connected to TICKR");
                IsTickrConnected = true;
                OnTickrConnected?.Invoke();
            },
            (address, serviceUUID) => 
            {
                Debug.Log($"[WahooBLE] Service discovered: {serviceUUID}");
            },
            (address, serviceUUID, characteristicUUID) => 
            {
                Debug.Log($"[WahooBLE] Characteristic discovered: {characteristicUUID}");

                // Subscribe to Heart Rate Measurement
                if (characteristicUUID.ToLower() == HEART_RATE_MEASUREMENT.ToLower())
                {
                    Debug.Log("[WahooBLE] Subscribing to Heart Rate notifications...");
                    
                    BluetoothLEHardwareInterface.SubscribeCharacteristicWithDeviceAddress(
                        tickrDeviceAddress,
                        HEART_RATE_SERVICE,
                        HEART_RATE_MEASUREMENT,
                        (notifyAddress, notifyCharacteristic) => 
                        {
                            Debug.Log("[WahooBLE] ✓ Subscribed to Heart Rate");
                        },
                        (address, characteristic, data) => 
                        {
                            HandleHeartRateData(data);
                        }
                    );
                }
            },
            (address) => 
            {
                Debug.LogWarning($"[WahooBLE] TICKR disconnected");
                IsTickrConnected = false;
                OnTickrDisconnected?.Invoke();
                tickrDeviceAddress = null;

                // Auto-reconnect after 5 seconds
                Invoke(nameof(ScanAndConnect), 5f);
            }
        );
    }

    private void HandleCyclingPowerData(byte[] data)
    {
        if (data.Length < 4) return;

        try
        {
            // Cycling Power Measurement characteristic layout (Bluetooth SIG):
            //   Bytes 0-1 : Flags (uint16 LE) — bitmask of optional fields present
            //   Bytes 2-3 : Instantaneous Power (int16 LE) — always present, in Watts
            // Additional optional fields (crank revolutions, wheel revolutions, etc.)
            // follow bytes 3+ depending on flag bits, but we only need power here.
            ushort flags = BitConverter.ToUInt16(data, 0);
            short power  = BitConverter.ToInt16(data, 2);  // signed; negative = regeneration

            currentPower = power;

            if (enableSmoothing)
            {
                // Exponential moving average: new = Lerp(old, current, alpha)
                // alpha = 1 - smoothingFactor; smaller smoothingFactor → more smoothing
                float alpha = 1f - smoothingFactor;
                smoothedPower = Mathf.Lerp(smoothedPower, currentPower, alpha);
            }

            BroadcastData();

            if (Time.frameCount % 60 == 0)
            {
                Debug.Log($"[WahooBLE] Power: {Power}W");
            }
        }
        catch (Exception e)
        {
            Debug.LogError($"[WahooBLE] Error parsing Cycling Power: {e.Message}");
        }
    }

    private void HandleHeartRateData(byte[] data)
    {
        if (data.Length < 2) return;

        try
        {
            // Heart Rate Measurement characteristic layout (Bluetooth SIG 0x2A37):
            //   Byte 0   : Flags (uint8) — bitmask
            //     Bit 0  : HR Format — 0 = uint8 value in byte 1
            //                         1 = uint16 value in bytes 1-2
            //     Bit 4  : RR-Interval Present (we ignore this for simplicity)
            //   Byte 1   : Heart Rate value (uint8, or low byte of uint16)
            //   Byte 2   : Heart Rate high byte (only present when bit 0 of flags = 1)
            byte flags    = data[0];
            bool isUint16 = (flags & 0x01) != 0;  // bit 0 set → 16-bit HR value

            if (isUint16)
            {
                // Some high-end sensors send 16-bit values for HR > 255 bpm (rare)
                currentHeartRate = BitConverter.ToUInt16(data, 1);
            }
            else
            {
                // Standard 8-bit HR value (covers normal physiological range 0-255)
                currentHeartRate = data[1];
            }

            BroadcastData();

            if (Time.frameCount % 60 == 0)
            {
                Debug.Log($"[WahooBLE] HR: {HeartRate}bpm");
            }
        }
        catch (Exception e)
        {
            Debug.LogError($"[WahooBLE] Error parsing Heart Rate: {e.Message}");
        }
    }

    private void BroadcastData()
    {
        var data = new CyclingData
        {
            timestamp = Time.timeAsDouble,
            power = Power,
            cadence = Cadence,
            speed = Speed,
            heart_rate = HeartRate
        };

        OnDataReceived?.Invoke(data);
    }

    void OnDestroy()
    {
        if (!string.IsNullOrEmpty(kickrDeviceAddress))
        {
            BluetoothLEHardwareInterface.DisconnectPeripheral(kickrDeviceAddress, null);
        }
        if (!string.IsNullOrEmpty(tickrDeviceAddress))
        {
            BluetoothLEHardwareInterface.DisconnectPeripheral(tickrDeviceAddress, null);
        }

        BluetoothLEHardwareInterface.DeInitialize(() => 
        {
            Debug.Log("[WahooBLE] Deinitialized");
        });
    }

    void OnApplicationQuit()
    {
        OnDestroy();
    }

    // Helper methods
    public bool IsPedaling() => Power > 10 || Cadence > 10;
    
    public float GetNormalizedPower(float maxPower = 300f)
    {
        return Mathf.Clamp01(Power / maxPower);
    }

#if UNITY_EDITOR
    void OnGUI()
    {
        GUIStyle style = new GUIStyle();
        style.fontSize = 18;
        style.normal.textColor = Color.white;

        GUI.Label(new Rect(10, 10, 300, 25), $"KICKR: {(IsKickrConnected ? "✓" : "✗")}", style);
        GUI.Label(new Rect(10, 35, 300, 25), $"TICKR: {(IsTickrConnected ? "✓" : "✗")}", style);
        GUI.Label(new Rect(10, 60, 300, 25), $"Power: {Power}W", style);
        GUI.Label(new Rect(10, 85, 300, 25), $"Cadence: {Cadence:F0}rpm", style);
        GUI.Label(new Rect(10, 110, 300, 25), $"Speed: {Speed:F1}km/h", style);
        GUI.Label(new Rect(10, 135, 300, 25), $"HR: {HeartRate}bpm", style);

        if (GUI.Button(new Rect(10, 170, 150, 40), "Scan & Connect"))
        {
            ScanAndConnect();
        }
    }
#endif
}
