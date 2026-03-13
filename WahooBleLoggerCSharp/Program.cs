// =============================================================================
// WahooBleLoggerCSharp — Standalone .NET BLE Logger
// =============================================================================
// Purpose:
//   A self-contained .NET 8 console application that connects directly to
//   Wahoo TICKR (heart-rate monitor) and KICKR (smart trainer) devices over
//   Bluetooth Low Energy (BLE) and writes all received metrics to a local
//   SQLite database (training.db).
//
// This is the C# alternative to the Python script (python/wahoo_ble_logger.py).
// Both write to the same schema so you can choose whichever runtime is more
// convenient on your machine.
//
// Architecture:
//   Main()
//     ├─ ScanForDevice() / FindDeviceByAddress()   — BLE discovery
//     ├─ HandleHeartRateDevice()  ──────────────┐
//     └─ HandleTrainerDevice()                  │  run concurrently via
//                                               └──> Task.WhenAll()
//
// BLE services & characteristics used:
//   Heart Rate Service           0x180D
//     Heart Rate Measurement     0x2A37
//   Cycling Power Service        0x1818
//     Cycling Power Measurement  0x2A63
//   Fitness Machine Service      0x1826
//     Indoor Bike Data           0x2AD2
//
// Database schema (training.db):
//   metrics(ts REAL, hr_bpm INT, rr_ms INT, power_w INT,
//           cadence_rpm REAL, speed_kph REAL)
//   ts = Unix timestamp in fractional seconds (ms precision).
//   All sensor columns are nullable — each row is written by exactly one
//   device, so TICKR rows have hr_bpm/rr_ms set while KICKR rows have
//   power_w/cadence_rpm/speed_kph set.
//
// Dependencies (NuGet):
//   InTheHand.BluetoothLE  — cross-platform BLE API (wraps OS Bluetooth stack)
//   Microsoft.Data.Sqlite  — SQLite driver for .NET
// =============================================================================

using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using InTheHand.Bluetooth;
using Microsoft.Data.Sqlite;

namespace WahooBleLogger;

/// <summary>
/// Entry point and device-handler logic for the Wahoo BLE logger.
///
/// Scans for TICKR (heart rate) and KICKR (smart trainer) devices, subscribes
/// to their BLE notifications, parses the binary characteristic payloads, and
/// persists every measurement as a row in the local SQLite database.
/// </summary>
class Program
{
    // -------------------------------------------------------------------------
    // GATT Service and Characteristic UUIDs
    // -------------------------------------------------------------------------
    // BluetoothUuid.FromShortId() converts a 16-bit Bluetooth SIG "short UUID"
    // to the full 128-bit form:  0000XXXX-0000-1000-8000-00805F9B34FB
    // -------------------------------------------------------------------------
    private static readonly Guid HEART_RATE_SERVICE       = BluetoothUuid.FromShortId(0x180D);
    private static readonly Guid HEART_RATE_MEASUREMENT   = BluetoothUuid.FromShortId(0x2A37);
    
    private static readonly Guid CYCLING_POWER_SERVICE    = BluetoothUuid.FromShortId(0x1818);
    private static readonly Guid CYCLING_POWER_MEASUREMENT = BluetoothUuid.FromShortId(0x2A63);
    
    // FTMS = Fitness Machine Service — used by smart trainers that expose
    // speed, cadence, power and resistance all in a single characteristic.
    private static readonly Guid FITNESS_MACHINE_SERVICE  = BluetoothUuid.FromShortId(0x1826);
    private static readonly Guid INDOOR_BIKE_DATA         = BluetoothUuid.FromShortId(0x2AD2);

    private const string DB_NAME = "training.db";

    // Volatile flag checked by both device-handler loops; set to false by
    // Ctrl+C so all tasks exit cleanly rather than being hard-cancelled.
    private static bool _running = true;

    static async Task Main(string[] args)
    {
        Console.WriteLine("Wahoo BLE Logger - C# Edition");
        Console.WriteLine("==============================\n");

        // ── Command-line argument parsing ────────────────────────────────────
        // --tickr-address <id>  : skip scan, connect directly to this device ID
        // --kickr-address <id>  : skip scan, connect directly to this device ID
        // --show-all-devices    : print every BLE device seen during scan
        // --debug               : verbose GATT service listing + raw hex dumps
        string? tickrAddress = GetArgument(args, "--tickr-address");
        string? kickrAddress = GetArgument(args, "--kickr-address");
        bool showAll = args.Contains("--show-all-devices");
        bool debug = args.Contains("--debug");

        // ── Database initialisation ──────────────────────────────────────────
        // Creates training.db (if absent), enables WAL mode for concurrent
        // reads, and creates the metrics table + timestamp index.
        InitializeDatabase();

        // ── Graceful shutdown via Ctrl+C ─────────────────────────────────────
        // e.Cancel = true prevents the process from terminating immediately;
        // instead we lower _running and let both while-loops exit on their
        // next iteration, giving each device a chance to StopNotifications +
        // Disconnect cleanly.
        Console.CancelKeyPress += (s, e) =>
        {
            e.Cancel = true;
            _running = false;
            Console.WriteLine("\nShutting down...");
        };

        try
        {
            // ── Device discovery ─────────────────────────────────────────────
            // If an address was supplied on the command line, skip the active
            // scan and locate the device directly; otherwise scan and match by
            // device name substring ("TICKR" / "KICKR").
            BluetoothDevice? tickr = null;
            BluetoothDevice? kickr = null;

            if (!string.IsNullOrEmpty(tickrAddress))
            {
                Console.WriteLine($"Using specified TICKR address: {tickrAddress}");
                tickr = await FindDeviceByAddress(tickrAddress);
            }
            else
            {
                tickr = await ScanForDevice("TICKR", showAll);
            }

            if (!string.IsNullOrEmpty(kickrAddress))
            {
                Console.WriteLine($"Using specified KICKR address: {kickrAddress}");
                kickr = await FindDeviceByAddress(kickrAddress);
            }
            else
            {
                kickr = await ScanForDevice("KICKR", showAll);
            }

            if (tickr == null && kickr == null)
            {
                Console.WriteLine("ERROR: No Wahoo devices found. Exiting.");
                return;
            }

            Console.WriteLine("\nStarting data collection. Press Ctrl+C to stop.\n");

            // ── Concurrent device handlers ───────────────────────────────────
            // Each handler runs its own reconnect loop independently.
            // Task.WhenAll() keeps Main alive until both tasks complete (i.e.
            // until _running is set to false and both loops have exited).
            var tasks = new List<Task>();
            
            if (tickr != null)
                tasks.Add(HandleHeartRateDevice(tickr, debug));

            if (kickr != null)
                tasks.Add(HandleTrainerDevice(kickr, debug));

            await Task.WhenAll(tasks);
        }
        catch (Exception ex)
        {
            Console.WriteLine($"ERROR: {ex.Message}");
            if (debug)
                Console.WriteLine($"Stack trace: {ex.StackTrace}");
        }
    }

    /// <summary>
    /// Creates (or opens) the SQLite database and ensures the schema exists.
    ///
    /// WAL (Write-Ahead Logging) mode is enabled so that reads from other tools
    /// (e.g. DB Browser, Python scripts) don't block the logger and vice-versa.
    ///
    /// Schema:
    ///   metrics.ts          — Unix time in fractional seconds (ms resolution)
    ///   metrics.hr_bpm      — Heart rate (beats per minute) from TICKR
    ///   metrics.rr_ms       — RR interval (ms between heartbeats); HRV data
    ///   metrics.power_w     — Instantaneous power (Watts) from KICKR
    ///   metrics.cadence_rpm — Pedalling cadence (revolutions per minute)
    ///   metrics.speed_kph   — Wheel speed (km/h)
    ///
    /// An index on ts allows efficient time-range queries.
    /// </summary>
    static void InitializeDatabase()
    {
        using var connection = new SqliteConnection($"Data Source={DB_NAME}");
        connection.Open();

        // WAL mode allows concurrent readers while a write is in progress,
        // which is important when other tools query the DB live during a ride.
        var walCmd = connection.CreateCommand();
        walCmd.CommandText = "PRAGMA journal_mode=WAL";
        walCmd.ExecuteNonQuery();

        // IF NOT EXISTS makes this call idempotent — safe to run on every start.
        var createCmd = connection.CreateCommand();
        createCmd.CommandText = @"
            CREATE TABLE IF NOT EXISTS metrics (
                ts REAL NOT NULL,
                hr_bpm INTEGER,
                rr_ms INTEGER,
                power_w INTEGER,
                cadence_rpm REAL,
                speed_kph REAL
            )";
        createCmd.ExecuteNonQuery();

        // Index allows fast SELECT … WHERE ts BETWEEN t1 AND t2 queries.
        var indexCmd = connection.CreateCommand();
        indexCmd.CommandText = "CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(ts)";
        indexCmd.ExecuteNonQuery();

        Console.WriteLine($"Database initialized: {DB_NAME}\n");
    }

    /// <summary>
    /// Performs an active BLE scan and returns the first device whose name
    /// contains <paramref name="nameContains"/> (case-insensitive).
    ///
    /// If <paramref name="showAll"/> is true, every advertising device with a
    /// non-empty name is printed — useful for debugging if the expected device
    /// doesn't appear (wrong name substring, device not advertising, etc.).
    ///
    /// Returns null if no matching device is found during the scan window.
    /// </summary>
    static async Task<BluetoothDevice?> ScanForDevice(string nameContains, bool showAll)
    {
        Console.WriteLine($"Scanning for device containing '{nameContains}'...");

        var devices = new List<BluetoothDevice>();
        
        try
        {
            // ScanForDevicesAsync() performs a one-shot active scan using the
            // OS Bluetooth stack.  Duration is controlled by InTheHand defaults.
            var scanResult = await Bluetooth.ScanForDevicesAsync();
            
            foreach (var device in scanResult)
            {
                devices.Add(device);
                
                // Print all named devices when --show-all-devices is active.
                if (showAll && !string.IsNullOrEmpty(device.Name))
                    Console.WriteLine($"  - {device.Name} ({device.Id})");

                // Return immediately on first name match to avoid waiting for
                // the full scan window.
                if (!string.IsNullOrEmpty(device.Name) && 
                    device.Name.Contains(nameContains, StringComparison.OrdinalIgnoreCase))
                {
                    Console.WriteLine($"Found {device.Name} at {device.Id}");
                    return device;
                }
            }
        }
        catch (Exception ex)
        {
            Console.WriteLine($"Scan error: {ex.Message}");
        }

        Console.WriteLine($"WARNING: No device found containing '{nameContains}'");
        return null;
    }

    /// <summary>
    /// Looks up a specific device by (partial) address string.
    ///
    /// InTheHand.BluetoothLE does not expose a direct "get by MAC address" API
    /// on all platforms, so we fall back to scanning and substring-matching the
    /// device ID string against the supplied address.  Device.Id format varies
    /// by OS (e.g. "BluetoothLE#BluetoothLE…" on Windows, raw MAC on Linux).
    ///
    /// Returns null if no matching device is found.
    /// </summary>
    static async Task<BluetoothDevice?> FindDeviceByAddress(string address)
    {
        var scanResult = await Bluetooth.ScanForDevicesAsync();
        foreach (var device in scanResult)
        {
            // device.Id is the platform-specific identifier string; we check
            // whether it contains the user-supplied address as a substring so
            // the user can pass just the MAC portion without the OS prefix.
            if (device.Id.ToString().Contains(address, StringComparison.OrdinalIgnoreCase))
                return device;
        }
        return null;
    }

    /// <summary>
    /// Manages the full lifecycle of a TICKR heart-rate device connection.
    ///
    /// Reconnect loop:
    ///   1. Connect via GATT.
    ///   2. Discover the Heart Rate Service (0x180D).
    ///   3. Discover the Heart Rate Measurement characteristic (0x2A37).
    ///   4. Subscribe to BLE notifications; each notification fires the lambda
    ///      which calls ParseHeartRate() and then LogMetric().
    ///   5. Poll gatt.IsConnected every second until the connection drops or
    ///      _running is set to false (Ctrl+C).
    ///   6. On disconnect, wait 5 s and retry from step 1.
    /// </summary>
    static async Task HandleHeartRateDevice(BluetoothDevice device, bool debug)
    {
        while (_running)
        {
            try
            {
                Console.WriteLine($"Connecting to {device.Name} ({device.Id})...");
                
                var gatt = device.Gatt;
                await gatt.ConnectAsync();

                if (!gatt.IsConnected)
                {
                    Console.WriteLine($"Failed to connect to {device.Name}");
                    await Task.Delay(5000); // wait 5 s before retrying
                    continue;
                }

                Console.WriteLine($"Connected to {device.Name}");

                // Step 1: locate the standard Heart Rate GATT service.
                var service = await gatt.GetPrimaryServiceAsync(HEART_RATE_SERVICE);
                if (service == null)
                {
                    Console.WriteLine("Heart Rate Service not found!");
                    await Task.Delay(5000);
                    continue;
                }

                // Step 2: locate the Heart Rate Measurement characteristic
                // within that service.
                var characteristic = await service.GetCharacteristicAsync(HEART_RATE_MEASUREMENT);
                if (characteristic == null)
                {
                    Console.WriteLine("Heart Rate Measurement characteristic not found!");
                    await Task.Delay(5000);
                    continue;
                }

                Console.WriteLine($"Subscribed to notifications from {device.Name}");

                // Step 3: register the notification handler.
                // CharacteristicValueChanged is invoked on a background thread
                // by the BLE stack each time the TICKR sends a new reading
                // (typically once per heartbeat).
                characteristic.CharacteristicValueChanged += (sender, args) =>
                {
                    var data = args.Value;
                    if (data == null) return;
                    
                    var parsed = ParseHeartRate(data);
                    
                    if (parsed.HasValue)
                    {
                        var (bpm, rrMs) = parsed.Value;
                        // Write hr_bpm and rr_ms; all trainer fields are NULL.
                        LogMetric(hrBpm: bpm, rrMs: rrMs);
                        
                        var msg = $"[{device.Name}] HR: {bpm} bpm";
                        if (rrMs.HasValue)
                            msg += $", RR: {rrMs} ms";
                        Console.WriteLine(msg);
                    }
                };

                await characteristic.StartNotificationsAsync();

                // Step 4: keep the loop alive while still connected.
                // 1-second polling is lightweight and keeps CPU usage minimal.
                while (_running && gatt.IsConnected)
                    await Task.Delay(1000);

                await characteristic.StopNotificationsAsync();
                gatt.Disconnect();

                if (_running)
                {
                    Console.WriteLine($"{device.Name} disconnected. Reconnecting in 5s...");
                    await Task.Delay(5000);
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine($"Error with {device.Name}: {ex.Message}");
                if (_running)
                    await Task.Delay(5000);
            }
        }
    }

    /// <summary>
    /// Manages the full lifecycle of a KICKR smart-trainer device connection.
    ///
    /// Service priority:
    ///   1. Cycling Power Service (0x1818) — tried first; provides power only.
    ///   2. Fitness Machine Service / FTMS (0x1826) — fallback; provides power,
    ///      cadence and speed in the Indoor Bike Data characteristic (0x2AD2).
    ///
    /// The KICKR may expose either service (or both) depending on firmware
    /// version, so the fallback ensures older units are still supported.
    ///
    /// Reconnect behaviour is the same as HandleHeartRateDevice(): 5-second
    /// delay then retry on any connection loss or exception.
    /// </summary>
    static async Task HandleTrainerDevice(BluetoothDevice device, bool debug)
    {
        while (_running)
        {
            try
            {
                Console.WriteLine($"Connecting to {device.Name} ({device.Id})...");
                
                var gatt = device.Gatt;
                await gatt.ConnectAsync();

                if (!gatt.IsConnected)
                {
                    Console.WriteLine($"Failed to connect to {device.Name}");
                    await Task.Delay(5000);
                    continue;
                }

                Console.WriteLine($"Connected to {device.Name}");

                // Optional: dump every advertised service UUID for debugging.
                if (debug)
                {
                    Console.WriteLine($"Available services for {device.Name}:");
                    var services = await gatt.GetPrimaryServicesAsync();
                    foreach (var svc in services)
                        Console.WriteLine($"  Service: {svc.Uuid}");
                }

                // ── Service discovery ────────────────────────────────────────
                // Prefer Cycling Power Service — it's the older, widely-supported
                // profile that all KICKR firmwares implement.
                GattCharacteristic? characteristic = null;
                var cpService = await gatt.GetPrimaryServiceAsync(CYCLING_POWER_SERVICE);
                
                if (cpService != null)
                {
                    characteristic = await cpService.GetCharacteristicAsync(CYCLING_POWER_MEASUREMENT);
                    if (characteristic != null)
                        Console.WriteLine("Using Cycling Power Service");
                }

                // If Cycling Power is absent, try the FTMS Indoor Bike Data
                // characteristic which carries speed and cadence in addition to power.
                if (characteristic == null)
                {
                    var ftmsService = await gatt.GetPrimaryServiceAsync(FITNESS_MACHINE_SERVICE);
                    if (ftmsService != null)
                    {
                        characteristic = await ftmsService.GetCharacteristicAsync(INDOOR_BIKE_DATA);
                        if (characteristic != null)
                            Console.WriteLine("Using Fitness Machine Service (FTMS)");
                    }
                }

                if (characteristic == null)
                {
                    Console.WriteLine($"No supported characteristics found on {device.Name}");
                    await Task.Delay(5000);
                    continue;
                }

                Console.WriteLine($"Subscribed to notifications from {device.Name}");

                // Subscribe to BLE notifications; the lambda is called on a
                // background thread by the OS Bluetooth stack.
                characteristic.CharacteristicValueChanged += (sender, args) =>
                {
                    var data = args.Value;
                    if (data == null) return;
                    
                    // Route to the correct parser depending on which service
                    // this characteristic belongs to.
                    var parsed = characteristic.Service.Uuid == CYCLING_POWER_SERVICE
                        ? ParseCyclingPower(data)
                        : ParseIndoorBikeData(data, debug);
                    
                    if (parsed.HasValue)
                    {
                        var (power, cadence, speed) = parsed.Value;
                        // Write trainer fields; hr_bpm and rr_ms are left NULL.
                        LogMetric(powerW: power, cadenceRpm: cadence, speedKph: speed);
                        
                        var parts = new List<string>();
                        if (power.HasValue)   parts.Add($"Power: {power} W");
                        if (cadence.HasValue) parts.Add($"Cadence: {cadence:F1} rpm");
                        if (speed.HasValue)   parts.Add($"Speed: {speed:F1} km/h");
                        
                        if (parts.Any())
                            Console.WriteLine($"[{device.Name}] {string.Join(", ", parts)}");
                    }
                };

                await characteristic.StartNotificationsAsync();

                // Poll connection state; 1-second interval keeps CPU usage low.
                while (_running && gatt.IsConnected)
                    await Task.Delay(1000);

                await characteristic.StopNotificationsAsync();
                gatt.Disconnect();

                if (_running)
                {
                    Console.WriteLine($"{device.Name} disconnected. Reconnecting in 5s...");
                    await Task.Delay(5000);
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine($"Error with {device.Name}: {ex.Message}");
                if (_running)
                    await Task.Delay(5000);
            }
        }
    }

    /// <summary>
    /// Parses a raw Heart Rate Measurement characteristic payload (0x2A37).
    ///
    /// Byte layout (Bluetooth SIG specification):
    ///   Byte 0        — Flags
    ///     bit 0  : HR value format  0 = uint8 (byte 1)
    ///                               1 = uint16 little-endian (bytes 1–2)
    ///     bit 4  : RR-interval present  1 = uint16 follows after HR value
    ///   Byte 1[–2]    — Heart rate value (uint8 or uint16 LE, see bit 0)
    ///   Byte offset+0 — RR interval, 1/1024 s units (uint16 LE), if bit 4 set
    ///
    /// Returns (bpm, rrMs) on success, or null if the buffer is too short.
    /// rrMs is null if no RR-interval field is present in this notification.
    ///
    /// RR-interval conversion:
    ///   The raw value is in units of 1/1024 second.
    ///   rrMs = raw × (1000 / 1024)  ≈  raw × 0.9766 ms
    /// </summary>
    static (int bpm, int? rrMs)? ParseHeartRate(byte[] data)
    {
        if (data.Length < 2)
            return null;

        byte flags = data[0];

        // Bit 0 of flags selects the HR value width:
        //   0 → uint8  in byte 1         (older/simpler devices like TICKR)
        //   1 → uint16 in bytes 1–2 LE   (allows HR > 255 bpm, rarely needed)
        bool is16Bit = (flags & 0x01) != 0;
        
        int bpm    = is16Bit ? BitConverter.ToUInt16(data, 1) : data[1];
        int offset = is16Bit ? 3 : 2; // advance past the HR value field

        // Bit 4 of flags indicates RR-interval data follows immediately after
        // the HR value.  Each RR interval is a uint16 in units of 1/1024 s.
        int? rrMs = null;
        bool hasRR = (flags & 0x10) != 0;
        
        if (hasRR && data.Length >= offset + 2)
        {
            ushort rr1024 = BitConverter.ToUInt16(data, offset);
            // Convert from 1/1024 s units to milliseconds:
            //   rr_ms = rr_1024 / 1024 × 1000
            rrMs = (int)((rr1024 / 1024.0) * 1000);
        }

        return (bpm, rrMs);
    }

    /// <summary>
    /// Parses a raw Cycling Power Measurement characteristic payload (0x2A63).
    ///
    /// Byte layout (Bluetooth SIG specification, abbreviated):
    ///   Bytes 0–1  — Flags (uint16 LE)  — bitmask of optional fields present
    ///   Bytes 2–3  — Instantaneous Power (sint16 LE, Watts) — ALWAYS present
    ///   Bytes 4+   — Optional fields (crank/wheel revolutions, torque, etc.)
    ///                not parsed here; power is sufficient for VR use.
    ///
    /// Returns (power, null, null) — cadence and speed are not extracted from
    /// this characteristic (use FTMS Indoor Bike Data for those fields).
    /// Returns null if the buffer is shorter than 4 bytes.
    /// </summary>
    static (int? power, double? cadence, double? speed)? ParseCyclingPower(byte[] data)
    {
        if (data.Length < 4)
            return null;

        try
        {
            ushort flags = BitConverter.ToUInt16(data, 0);
            int offset = 2;

            // Instantaneous Power is always present at bytes 2–3, regardless
            // of the flags field.  sint16 allows negative values (braking).
            short power = BitConverter.ToInt16(data, offset);
            offset += 2;

            // Additional fields (accumulated torque, wheel/crank revolutions,
            // dead-band angle, etc.) are present if the corresponding flag bits
            // are set, but are not needed for basic power/cadence logging.
            return (power, null, null);
        }
        catch
        {
            return null;
        }
    }

    /// <summary>
    /// Parses a raw FTMS Indoor Bike Data characteristic payload (0x2AD2).
    ///
    /// The FTMS Indoor Bike Data characteristic uses a flags field to indicate
    /// which optional data fields follow.  Fields are present in a fixed order
    /// when their flag bit is set; absent fields occupy no bytes (dense packing).
    ///
    /// Byte layout (Bluetooth SIG FTMS specification, section 4.9.1):
    ///   Bytes 0–1  — Flags (uint16 LE)
    ///     bit 1  : Average Speed present         (uint16, +2 bytes, 0.01 km/h)
    ///     bit 2  : Instantaneous Cadence present  (uint16, +2 bytes, 0.5 rpm)
    ///     bit 3  : Average Cadence present        (uint16, +2 bytes, 0.5 rpm)
    ///     bit 4  : Total Distance present         (uint24, +3 bytes, 1 m)
    ///     bit 5  : Resistance Level present       (sint16, +2 bytes, unitless)
    ///     bit 6  : Instantaneous Power present    (sint16, +2 bytes, 1 W)
    ///   Bytes 2–3  — Instantaneous Speed (uint16 LE, 0.01 km/h) — ALWAYS present
    ///   Bytes 4+   — Optional fields in flag-bit order (see above)
    ///
    /// Field parsers:
    ///   speed   = raw_uint16 × 0.01  → km/h
    ///   cadence = raw_uint16 × 0.5   → rpm
    ///   power   = raw_sint16          → Watts
    ///
    /// Returns (power, cadence, speed); any field not present in this packet is null.
    /// Returns null if the buffer is shorter than 2 bytes.
    /// </summary>
    static (int? power, double? cadence, double? speed)? ParseIndoorBikeData(byte[] data, bool debug)
    {
        if (data.Length < 2)
            return null;

        try
        {
            // Flags uint16 LE: indicates which optional fields follow.
            ushort flags = BitConverter.ToUInt16(data, 0);
            int offset = 2; // byte cursor; advanced as each field is consumed

            if (debug)
                Console.WriteLine($"FTMS flags: 0x{flags:X4}, raw: {BitConverter.ToString(data)}");

            double? speed    = null;
            double? cadence  = null;
            int?    power    = null;

            // Parse the flags bitmask upfront so the sequential field reads
            // below are easy to follow.
            bool hasAvgSpeed    = (flags & 0x02) != 0; // bit 1
            bool hasCadence     = (flags & 0x04) != 0; // bit 2
            bool hasAvgCadence  = (flags & 0x08) != 0; // bit 3
            bool hasDistance    = (flags & 0x10) != 0; // bit 4
            bool hasResistance  = (flags & 0x20) != 0; // bit 5
            bool hasPower       = (flags & 0x40) != 0; // bit 6

            // ── Field 1: Instantaneous Speed (always present, bytes 2–3) ────
            // Resolution: 0.01 km/h per LSB.  uint16 → divide by 100 for km/h.
            if (data.Length >= offset + 2)
            {
                speed = BitConverter.ToUInt16(data, offset) * 0.01;
                offset += 2;
            }

            // ── Field 2: Average Speed (optional, bit 1) ────────────────────
            // Same encoding as instantaneous speed.  We skip it — only the
            // live value is needed for real-time display.
            if (hasAvgSpeed && data.Length >= offset + 2)
                offset += 2;

            // ── Field 3: Instantaneous Cadence (optional, bit 2) ─────────────
            // Resolution: 0.5 rpm per LSB.  uint16 → multiply by 0.5 for rpm.
            if (hasCadence && data.Length >= offset + 2)
            {
                cadence = BitConverter.ToUInt16(data, offset) * 0.5;
                offset += 2;
            }

            // ── Field 4: Average Cadence (optional, bit 3) ───────────────────
            // Same encoding as instantaneous cadence.  Skip.
            if (hasAvgCadence && data.Length >= offset + 2)
                offset += 2;

            // ── Field 5: Total Distance (optional, bit 4) ────────────────────
            // uint24 (3 bytes), resolution 1 m.  Skip — we integrate speed instead.
            if (hasDistance && data.Length >= offset + 3)
                offset += 3;

            // ── Field 6: Resistance Level (optional, bit 5) ──────────────────
            // sint16, unitless trainer resistance setting.  Skip.
            if (hasResistance && data.Length >= offset + 2)
                offset += 2;

            // ── Field 7: Instantaneous Power (optional, bit 6) ───────────────
            // sint16, resolution 1 W.  Negative = braking/resistance.
            if (hasPower && data.Length >= offset + 2)
            {
                power = BitConverter.ToInt16(data, offset);
                offset += 2;
            }

            return (power, cadence, speed);
        }
        catch (Exception ex)
        {
            Console.WriteLine($"FTMS parsing error: {ex.Message}");
            return null;
        }
    }

    /// <summary>
    /// Inserts a single metrics row into the SQLite database.
    ///
    /// All sensor parameters are nullable.  Pass only the values that are
    /// available for this sample; all omitted parameters default to null and
    /// are stored as SQL NULL.  This means a single row always comes from one
    /// device:
    ///   TICKR notification  → hrBpm + rrMs set; power/cadence/speed = null
    ///   KICKR notification  → power/cadence/speed set; hrBpm/rrMs = null
    ///
    /// Timestamp:
    ///   DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() gives ms since epoch.
    ///   Dividing by 1000.0 stores it as fractional seconds (REAL), matching
    ///   the schema used by the Python loggers.
    ///
    /// Parameterised query (@name placeholders):
    ///   Named parameters prevent SQL injection and handle null mapping via
    ///   DBNull.Value (which SQLite stores as NULL, not the string "null").
    ///
    /// Each call opens and closes its own connection — acceptable at ~1 Hz
    /// notification rates; avoids connection-lifetime management complexity.
    /// </summary>
    static void LogMetric(int? hrBpm = null, int? rrMs = null, int? powerW = null, 
                         double? cadenceRpm = null, double? speedKph = null)
    {
        try
        {
            using var connection = new SqliteConnection($"Data Source={DB_NAME}");
            connection.Open();

            var cmd = connection.CreateCommand();
            // Named @parameters prevent SQL injection and map C# null →
            // SQL NULL cleanly via DBNull.Value below.
            cmd.CommandText = @"
                INSERT INTO metrics (ts, hr_bpm, rr_ms, power_w, cadence_rpm, speed_kph)
                VALUES (@ts, @hr_bpm, @rr_ms, @power_w, @cadence_rpm, @speed_kph)
            ";
            
            // ts: fractional Unix seconds (ms resolution), matching Python schema.
            cmd.Parameters.AddWithValue("@ts", DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0);

            // For nullable fields: pass DBNull.Value when the C# value is null
            // so SQLite stores a proper NULL rather than the text "null".
            cmd.Parameters.AddWithValue("@hr_bpm",      hrBpm.HasValue      ? hrBpm.Value      : DBNull.Value);
            cmd.Parameters.AddWithValue("@rr_ms",       rrMs.HasValue       ? rrMs.Value       : DBNull.Value);
            cmd.Parameters.AddWithValue("@power_w",     powerW.HasValue     ? powerW.Value     : DBNull.Value);
            cmd.Parameters.AddWithValue("@cadence_rpm", cadenceRpm.HasValue ? cadenceRpm.Value : DBNull.Value);
            cmd.Parameters.AddWithValue("@speed_kph",   speedKph.HasValue   ? speedKph.Value   : DBNull.Value);
            
            cmd.ExecuteNonQuery();
        }
        catch (Exception ex)
        {
            // Log but don't crash — a transient DB error shouldn't kill the
            // BLE connection or interrupt data collection.
            Console.WriteLine($"Database error: {ex.Message}");
        }
    }

    /// <summary>
    /// Parses a named argument from the command-line args array.
    ///
    /// Expects the format:  --arg-name value
    /// Returns the string immediately following the flag, or null if the flag
    /// is absent or is the last element in the array (no value follows).
    ///
    /// Example:
    ///   args = ["--tickr-address", "AA:BB:CC:DD:EE:FF"]
    ///   GetArgument(args, "--tickr-address") → "AA:BB:CC:DD:EE:FF"
    /// </summary>
    static string? GetArgument(string[] args, string name)
    {
        for (int i = 0; i < args.Length - 1; i++)
        {
            if (args[i] == name)
                return args[i + 1];
        }
        return null;
    }
}
