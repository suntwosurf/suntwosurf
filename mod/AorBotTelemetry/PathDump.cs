// Finds the stage's own path data so the bot needs no recorded run.
//
// The game must know where its road goes (e.g. to put the car back on the
// road), but its class names are unknown here, so this scans generically:
// every array/list of points (Vector3s, Transforms, or structs holding one) on
// every MonoBehaviour and ScriptableObject, children of big parent objects and
// LineRenderers. Point lists that form a long path passing the car are written
// to BepInEx/aorbot/paths_latest.json together with the car pose and a count of
// all component types in the scene (to target the right class by hand if needed).

using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Text;
using BepInEx.Configuration;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace AorBotTelemetry
{
    public partial class Plugin
    {
        const int MinPathPoints = 10;
        const int MaxPathPoints = 200000;
        const float MinPathLength = 300f;
        const float MaxCarDistance = 40f;
        const int MaxCandidates = 60;
        const BindingFlags AllInstance = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly;

        ConfigEntry<bool> dumpPaths;
        string dumpedScene;
        float dumpAt = -1f;
        bool keyInputBroken;

        class Candidate
        {
            public string Source;
            public List<Vector3> Points;
            public float Length;
        }

        void SchedulePathDump()
        {
            string scene = SceneManager.GetActiveScene().name;
            if (dumpPaths.Value && scene != dumpedScene)
                dumpAt = Time.unscaledTime + 3f; // let the stage finish loading
        }

        void UpdatePathDump()
        {
            if (!keyInputBroken)
            {
                try
                {
                    if (Input.GetKeyDown(KeyCode.F9))
                        dumpAt = Time.unscaledTime;
                }
                catch (InvalidOperationException)
                {
                    keyInputBroken = true; // game uses the new Input System only
                }
            }
            if (dumpAt < 0f || Time.unscaledTime < dumpAt)
                return;
            dumpAt = -1f;
            if (car == null)
                return;
            dumpedScene = SceneManager.GetActiveScene().name;
            try
            {
                DumpPaths();
            }
            catch (Exception e)
            {
                Logger.LogWarning("path dump failed: " + e);
            }
        }

        void DumpPaths()
        {
            var found = new List<Candidate>();
            var seen = new HashSet<string>();
            var typeCounts = new Dictionary<string, int>();

            foreach (MonoBehaviour mb in FindObjectsOfType<MonoBehaviour>())
            {
                if (mb == null)
                    continue;
                string typeName = mb.GetType().FullName;
                int n;
                typeCounts.TryGetValue(typeName, out n);
                typeCounts[typeName] = n + 1;
                ScanObject(mb, mb.transform, typeName + " on '" + mb.name + "'", found, seen, 0);
            }
            foreach (ScriptableObject so in Resources.FindObjectsOfTypeAll<ScriptableObject>())
            {
                if (so != null)
                    ScanObject(so, null, "asset " + so.GetType().FullName + " '" + so.name + "'", found, seen, 0);
            }
            foreach (LineRenderer lr in FindObjectsOfType<LineRenderer>())
            {
                var arr = new Vector3[lr.positionCount];
                lr.GetPositions(arr);
                var pts = new List<Vector3>(arr);
                if (!lr.useWorldSpace)
                    pts = pts.ConvertAll(p => lr.transform.TransformPoint(p));
                Consider("LineRenderer on '" + lr.name + "'", pts, found, seen);
            }
            foreach (Transform tr in FindObjectsOfType<Transform>())
            {
                if (tr.childCount < MinPathPoints)
                    continue;
                var pts = new List<Vector3>(tr.childCount);
                for (int i = 0; i < tr.childCount; i++)
                    pts.Add(tr.GetChild(i).position);
                Consider("children of '" + tr.name + "'", pts, found, seen);
            }

            found.Sort((a, b) => b.Length.CompareTo(a.Length));
            if (found.Count > MaxCandidates)
                found.RemoveRange(MaxCandidates, found.Count - MaxCandidates);

            string dir = Path.Combine(BepInEx.Paths.BepInExRootPath, "aorbot");
            Directory.CreateDirectory(dir);
            string scene = SceneManager.GetActiveScene().name;
            string json = ToJson(scene, found, typeCounts);
            File.WriteAllText(Path.Combine(dir, "paths_" + SafeName(scene) + ".json"), json);
            if (found.Count > 0)
                File.WriteAllText(Path.Combine(dir, "paths_latest.json"), json);
            Logger.LogInfo("path dump: " + found.Count + " candidate path(s) in scene '" + scene + "' -> " + dir);
        }

        void ScanObject(object owner, Transform space, string label, List<Candidate> found, HashSet<string> seen, int depth)
        {
            for (Type t = owner.GetType(); t != null && t != typeof(MonoBehaviour) && t != typeof(ScriptableObject)
                 && t != typeof(object); t = t.BaseType)
            {
                foreach (FieldInfo f in t.GetFields(AllInstance))
                {
                    Type ft = f.FieldType;
                    if (ft.IsPrimitive || ft.IsEnum || ft == typeof(string))
                        continue;
                    object value;
                    try
                    {
                        value = f.GetValue(owner);
                    }
                    catch (Exception)
                    {
                        continue;
                    }
                    if (value == null)
                        continue;
                    List<Vector3> pts = null;
                    try
                    {
                        pts = ToPoints(value);
                    }
                    catch (Exception)
                    {
                    }
                    string name = label + "." + f.Name;
                    if (pts != null)
                    {
                        Consider(name, pts, found, seen);
                        if (space != null)
                            Consider(name + " (local)", pts.ConvertAll(p => space.TransformPoint(p)), found, seen);
                        continue;
                    }
                    if (depth >= 1)
                        continue;
                    var asset = value as ScriptableObject;
                    if (asset != null)
                        ScanObject(asset, null, name, found, seen, depth + 1);
                    else if (!(value is UnityEngine.Object) && !(value is IEnumerable) && ft.IsClass)
                        ScanObject(value, space, name, found, seen, depth + 1);
                }
            }
        }

        static List<Vector3> ToPoints(object value)
        {
            var arr = value as Vector3[];
            if (arr != null)
                return new List<Vector3>(arr);
            var vlist = value as List<Vector3>;
            if (vlist != null)
                return new List<Vector3>(vlist);
            var list = value as IList;
            if (list == null || list.Count < MinPathPoints || list.Count > MaxPathPoints)
                return null;
            var pts = new List<Vector3>(list.Count);
            foreach (object e in list)
            {
                Vector3? p = PointOf(e);
                if (p == null)
                    return null;
                pts.Add(p.Value);
            }
            return pts;
        }

        static Vector3? PointOf(object e)
        {
            if (e == null)
                return null;
            if (e is Vector3)
                return (Vector3)e;
            var go = e as GameObject;
            if (go != null)
                return go.transform.position;
            var comp = e as Component;
            if (comp != null)
                return comp.transform.position;
            if (e is UnityEngine.Object)
                return null; // destroyed object or an unrelated asset
            foreach (FieldInfo f in e.GetType().GetFields(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic))
            {
                if (f.FieldType == typeof(Vector3))
                    return (Vector3)f.GetValue(e);
                if (typeof(Component).IsAssignableFrom(f.FieldType))
                {
                    var c = f.GetValue(e) as Component;
                    if (c != null)
                        return c.transform.position;
                }
            }
            return null;
        }

        void Consider(string source, List<Vector3> pts, List<Candidate> found, HashSet<string> seen)
        {
            if (pts == null || pts.Count < MinPathPoints)
                return;
            foreach (Vector3 p in pts)
            {
                if (float.IsNaN(p.x) || float.IsNaN(p.z) || float.IsInfinity(p.x) || float.IsInfinity(p.z))
                    return;
            }
            string key = pts.Count + "|" + pts[0].ToString("F1") + "|" + pts[pts.Count - 1].ToString("F1");
            if (!seen.Add(key))
                return;
            float length = 0f;
            for (int i = 1; i < pts.Count; i++)
                length += Flat(pts[i] - pts[i - 1]).magnitude;
            if (length < MinPathLength || DistanceToPolyline(pts, car.position) > MaxCarDistance)
                return;
            found.Add(new Candidate { Source = source, Points = pts, Length = length });
        }

        static Vector2 Flat(Vector3 v)
        {
            return new Vector2(v.x, v.z);
        }

        static float DistanceToPolyline(List<Vector3> pts, Vector3 point)
        {
            Vector2 p = Flat(point);
            float best = float.MaxValue;
            for (int i = 1; i < pts.Count; i++)
            {
                Vector2 a = Flat(pts[i - 1]), ab = Flat(pts[i]) - a;
                float len2 = ab.sqrMagnitude;
                float t = len2 > 1e-6f ? Mathf.Clamp01(Vector2.Dot(p - a, ab) / len2) : 0f;
                best = Mathf.Min(best, (a + t * ab - p).magnitude);
            }
            return best;
        }

        string ToJson(string scene, List<Candidate> found, Dictionary<string, int> typeCounts)
        {
            var sb = new StringBuilder();
            Vector3 fwd = car.transform.forward;
            sb.Append("{\"format\": 1, \"scene\": \"").Append(Escape(scene)).Append("\",\n");
            sb.Append("\"car\": ").Append(Vec(car.position)).Append(", \"car_forward\": ").Append(Vec(fwd)).Append(",\n");
            sb.Append("\"candidates\": [\n");
            for (int i = 0; i < found.Count; i++)
            {
                Candidate c = found[i];
                sb.Append("{\"source\": \"").Append(Escape(c.Source)).Append("\", \"length\": ")
                  .Append(Num(c.Length)).Append(", \"points\": [");
                for (int j = 0; j < c.Points.Count; j++)
                {
                    if (j > 0)
                        sb.Append(',');
                    sb.Append(Vec(c.Points[j]));
                }
                sb.Append("]}").Append(i + 1 < found.Count ? ",\n" : "\n");
            }
            sb.Append("],\n\"types\": {");
            bool first = true;
            foreach (var kv in typeCounts)
            {
                if (!first)
                    sb.Append(", ");
                first = false;
                sb.Append('"').Append(Escape(kv.Key)).Append("\": ").Append(kv.Value);
            }
            sb.Append("}}\n");
            return sb.ToString();
        }

        static string Num(float v)
        {
            return v.ToString("0.###", CultureInfo.InvariantCulture);
        }

        static string Vec(Vector3 v)
        {
            return "[" + Num(v.x) + "," + Num(v.y) + "," + Num(v.z) + "]";
        }

        static string Escape(string s)
        {
            var sb = new StringBuilder(s.Length);
            foreach (char ch in s)
            {
                if (ch == '"' || ch == '\\')
                    sb.Append('\\').Append(ch);
                else if (ch < ' ')
                    sb.Append(' ');
                else
                    sb.Append(ch);
            }
            return sb.ToString();
        }

        static string SafeName(string s)
        {
            foreach (char ch in Path.GetInvalidFileNameChars())
                s = s.Replace(ch, '_');
            return s;
        }
    }
}
