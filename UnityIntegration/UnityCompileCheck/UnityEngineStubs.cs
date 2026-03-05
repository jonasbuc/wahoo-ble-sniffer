using System;

// Minimal UnityEngine stubs to allow a syntax/compile check outside Unity Editor.
namespace UnityEngine
{
    public class MonoBehaviour { public Transform transform = new Transform(); }

    public struct Vector3
    {
        public float x, y, z;
        public Vector3(float x, float y, float z) { this.x = x; this.y = y; this.z = z; }
    }

    public struct Quaternion
    {
        public float x, y, z, w;
        public Quaternion(float x, float y, float z, float w) { this.x = x; this.y = y; this.z = z; this.w = w; }
    }

    public class Transform
    {
        public Vector3 position;
        public Quaternion rotation;
    }

    public class Camera
    {
        public Transform transform = new Transform();
        public static Camera? main { get; } = new Camera();
    }

    public static class JsonUtility
    {
        public static string ToJson(object obj)
        {
            try { return System.Text.Json.JsonSerializer.Serialize(obj); }
            catch { return "{}"; }
        }
    }

    public static class Time
    {
        public static float deltaTime => 0.016f;
        public static float time => 0f;
    }

    public static class Debug
    {
        public static void LogError(object? o) { }
        public static void Log(object? o) { }
    }

    public enum KeyCode { F1 }

    public static class Input
    {
        public static bool GetKeyDown(KeyCode k) => false;
    }

    public struct Rect { public Rect(float a,float b,float c,float d) { } }

    public static class GUILayout
    {
        public static void BeginArea(Rect r, string t) { }
        public static void EndArea() { }
        public static void Label(string s) { }
    }
}
