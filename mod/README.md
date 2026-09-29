# AorBotTelemetry (BepInEx 5 plugin)

This plugin sends the player car's state to the Python bot over UDP (default
`127.0.0.1:47800`) every frame. It only reads from the game and never changes
it.

## Build and install

It needs the [.NET SDK](https://dotnet.microsoft.com/download). The
BepInEx/Unity reference assemblies come from the BepInEx NuGet feed (see
`nuget.config`), so the build does not depend on your game files.

```powershell
dotnet build -c Release mod\AorBotTelemetry -p:GameDir="D:\Games\artofrally"
```

If `GameDir\BepInEx` exists, the DLL is copied to
`BepInEx\plugins\AorBotTelemetry\AorBotTelemetry.dll`. Otherwise, copy
`mod\AorBotTelemetry\bin\Release\net472\AorBotTelemetry.dll` there yourself.

## Check

Start the game. `BepInEx\LogOutput.log` should contain:

```
[Info   :AOR Bot Telemetry] streaming car state to 127.0.0.1:47800
[Info   :AOR Bot Telemetry] found car: <name> (mass ...)
```

Then run `aorbot check-telemetry --config configs\my_stage.toml`.

## Settings

The file is `BepInEx\config\suntwosurf.aorbot.telemetry.cfg`. It is created on
the first start.

| key | default | meaning |
|---|---|---|
| Telemetry.Host / Port | 127.0.0.1 / 47800 | where the bot listens |
| Car.ComponentType | `CarDynamics` | game component on the player car; its Rigidbody is streamed. Empty = heaviest non-kinematic Rigidbody |

## Troubleshooting

* **No log lines from the plugin:** check that BepInEx itself loads
  (`LogOutput.log` exists and lists plugins).
* **Log says it started, but packets stop after the menu:** some games destroy
  BepInEx's manager object. Set `HideManagerGameObject = true` in
  `BepInEx\config\BepInEx.cfg`.
* **"type 'CarDynamics' not found":** it falls back to the heaviest Rigidbody,
  which is normally the car on a stage. Watch the `found car:` line.

## Packet (little endian, 84 bytes)

| offset | type | field |
|---|---|---|
| 0 | char[4] | `AORT` |
| 4 | u32 | version (1) |
| 8 | u32 | sequence |
| 12 | f32 | `Time.time` |
| 16 | f32×3 | position (Unity world) |
| 28 | f32×3 | forward vector |
| 40 | f32×3 | up vector |
| 52 | f32×3 | velocity |
| 64 | f32×3 | angular velocity |
| 76 | i32 | active scene build index |
| 80 | u32 | flags: 1 = car found, 2 = paused (timeScale 0) |

The Python side of the packet is `aorbot/game/telemetry.py`.
