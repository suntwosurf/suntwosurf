// BepInEx 5 plugin for art of rally: streams the player car's state over UDP
// to the aorbot Python bot (packet layout = AORBOT_STRUCT in
// aorbot/game/telemetry.py), and dumps candidate stage paths for
// `aorbot import-path` (PathDump.cs). Read-only: it never changes the game.

using System;
using System.Net;
using System.Net.Sockets;
using BepInEx;
using BepInEx.Configuration;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace AorBotTelemetry
{
    [BepInPlugin(PluginGuid, "AOR Bot Telemetry", "0.1.0")]
    public partial class Plugin : BaseUnityPlugin
    {
        public const string PluginGuid = "suntwosurf.aorbot.telemetry";

        const uint PacketVersion = 1;
        const uint FlagCarFound = 1;
        const uint FlagPaused = 2;
        const int PacketSize = 84;

        ConfigEntry<string> host;
        ConfigEntry<int> port;
        ConfigEntry<string> carComponent;

        UdpClient udp;
        IPEndPoint target;
        readonly byte[] packet = new byte[PacketSize];
        uint seq;

        Rigidbody car;
        Type carComponentType;
        bool carTypeSearched;
        float nextSearch;

        void Awake()
        {
            host = Config.Bind("Telemetry", "Host", "127.0.0.1", "Address the aorbot bot listens on.");
            port = Config.Bind("Telemetry", "Port", 47800, "UDP port the aorbot bot listens on.");
            carComponent = Config.Bind("Car", "ComponentType", "CarDynamics",
                "Class name of the game component on the player car; its Rigidbody is streamed. " +
                "Empty = use the heaviest non-kinematic Rigidbody in the scene.");
            dumpPaths = Config.Bind("Paths", "DumpOnStageLoad", true,
                "When a car appears in a new scene (and on F9), write candidate stage paths to " +
                "BepInEx/aorbot/ so the bot can learn without a recorded run.");
            udp = new UdpClient();
            target = new IPEndPoint(IPAddress.Parse(host.Value), port.Value);
            Logger.LogInfo("streaming car state to " + target);
        }

        // Update (not FixedUpdate) so packets keep flowing while the game is
        // paused (timeScale 0) and the bot can see the paused flag.
        void Update()
        {
            if (car == null && Time.unscaledTime >= nextSearch)
            {
                nextSearch = Time.unscaledTime + 1f;
                car = FindCar();
                if (car != null)
                {
                    Logger.LogInfo("found car: " + car.name + " (mass " + car.mass + ")");
                    SchedulePathDump();
                }
            }
            UpdatePathDump();
            try
            {
                Send();
            }
            catch (SocketException e)
            {
                Logger.LogWarning("telemetry send failed: " + e.Message);
            }
        }

        Rigidbody FindCar()
        {
            string typeName = carComponent.Value;
            if (!string.IsNullOrEmpty(typeName))
            {
                if (!carTypeSearched)
                {
                    carTypeSearched = true;
                    carComponentType = FindType(typeName);
                    if (carComponentType == null)
                        Logger.LogWarning("type '" + typeName + "' not found; falling back to the heaviest Rigidbody");
                }
                if (carComponentType != null)
                {
                    foreach (UnityEngine.Object found in FindObjectsOfType(carComponentType))
                    {
                        Component c = found as Component;
                        if (c == null)
                            continue;
                        Rigidbody rb = c.GetComponent<Rigidbody>();
                        if (rb == null)
                            rb = c.GetComponentInParent<Rigidbody>();
                        if (rb != null && !rb.isKinematic)
                            return rb;
                    }
                    return null;
                }
            }
            Rigidbody best = null;
            foreach (Rigidbody rb in FindObjectsOfType<Rigidbody>())
            {
                if (!rb.isKinematic && (best == null || rb.mass > best.mass))
                    best = rb;
            }
            return best;
        }

        static Type FindType(string name)
        {
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                Type[] types;
                try
                {
                    types = assembly.GetTypes();
                }
                catch (System.Reflection.ReflectionTypeLoadException e)
                {
                    types = e.Types;
                }
                foreach (Type t in types)
                {
                    if (t != null && (t.Name == name || t.FullName == name) && typeof(Component).IsAssignableFrom(t))
                        return t;
                }
            }
            return null;
        }

        void Send()
        {
            uint flags = 0;
            Vector3 pos = Vector3.zero, fwd = Vector3.forward, up = Vector3.up, vel = Vector3.zero, ang = Vector3.zero;
            if (car != null)
            {
                flags |= FlagCarFound;
                Transform t = car.transform;
                pos = car.position;
                fwd = t.forward;
                up = t.up;
                vel = car.velocity;
                ang = car.angularVelocity;
            }
            if (Time.timeScale == 0f)
                flags |= FlagPaused;

            int o = 0;
            packet[o++] = (byte)'A';
            packet[o++] = (byte)'O';
            packet[o++] = (byte)'R';
            packet[o++] = (byte)'T';
            PutU32(ref o, PacketVersion);
            PutU32(ref o, ++seq);
            PutF32(ref o, Time.time);
            PutVec(ref o, pos);
            PutVec(ref o, fwd);
            PutVec(ref o, up);
            PutVec(ref o, vel);
            PutVec(ref o, ang);
            PutU32(ref o, unchecked((uint)SceneManager.GetActiveScene().buildIndex));
            PutU32(ref o, flags);
            udp.Send(packet, PacketSize, target);
        }

        void PutU32(ref int o, uint v)
        {
            packet[o++] = (byte)v;
            packet[o++] = (byte)(v >> 8);
            packet[o++] = (byte)(v >> 16);
            packet[o++] = (byte)(v >> 24);
        }

        void PutF32(ref int o, float v)
        {
            PutU32(ref o, BitConverter.ToUInt32(BitConverter.GetBytes(v), 0));
        }

        void PutVec(ref int o, Vector3 v)
        {
            PutF32(ref o, v.x);
            PutF32(ref o, v.y);
            PutF32(ref o, v.z);
        }

        void OnDestroy()
        {
            if (udp != null)
                udp.Close();
        }
    }
}
