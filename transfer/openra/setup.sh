#!/usr/bin/env bash
# OpenRA-RL without Docker, headless, on Linux x86_64 (also WSL2).
#
#   transfer/openra/setup.sh [install dir]      (default: ~/openra-rl-env)
#
# Pinned versions (see transfer/DESIGN.md, "Versions"):
#   OpenRA-RL 0.4.1                  yxc20089/OpenRA-RL @ 5dadd44 (2026-04-22)
#   its engine, a fork of OpenRA     yxc20089/OpenRA    @ 9b271c1 (2026-03-25)
#     = OpenRA's development branch of 2026-01-27 + 51 RL commits
#
# Needs: git, make, curl, python3 >= 3.10 with venv, and the native libraries
# libsdl2-2.0-0 libopenal1 libfreetype6 liblua5.1-0 (Debian/Ubuntu package
# names). The Red Alert art/sound files are not needed headless.
set -euo pipefail

ROOT=$(realpath -m "${1:-$HOME/openra-rl-env}")
OPENRA_RL_COMMIT=5dadd449c912ac2d4021cc8ed84fc0b385b1543c
ENGINE_COMMIT=9b271c1a5562a07deeb1c3f81d9a556cd563a676
mkdir -p "$ROOT"
cd "$ROOT"

fetch_commit() {  # fetch_commit <url> <commit> <dir>
	if [ ! -d "$3/.git" ]; then
		git init -q "$3"
		git -C "$3" remote add origin "$1"
	fi
	GIT_LFS_SKIP_SMUDGE=1 git -C "$3" fetch -q --depth 1 origin "$2"
	git -C "$3" checkout -q "$2"
}

missing=()
for lib in libSDL2-2.0.so.0 libopenal.so.1 libfreetype.so.6 liblua5.1.so.0; do
	ldconfig -p | grep -q "$lib" || missing+=("$lib")
done
if [ ${#missing[@]} -gt 0 ]; then
	echo "missing native libraries: ${missing[*]}"
	echo "install them, e.g.: sudo apt-get install libsdl2-2.0-0 libopenal1 libfreetype6 liblua5.1-0"
	exit 1
fi

# .NET 8 SDK (the engine targets .NET 8)
if command -v dotnet > /dev/null && dotnet --list-sdks | grep -q "^8\."; then
	DOTNET=$(command -v dotnet)
else
	if [ ! -x "$ROOT/dotnet/dotnet" ]; then
		echo "== installing the .NET 8 SDK into $ROOT/dotnet"
		curl -sSL -o dotnet-install.sh https://dot.net/v1/dotnet-install.sh
		bash dotnet-install.sh --channel 8.0 --install-dir "$ROOT/dotnet" > dotnet-install.log
	fi
	DOTNET=$ROOT/dotnet/dotnet
fi
export DOTNET_ROOT=$(dirname "$(realpath "$DOTNET")") DOTNET_CLI_TELEMETRY_OPTOUT=1
export PATH=$DOTNET_ROOT:$PATH

echo "== engine (yxc20089/OpenRA @ ${ENGINE_COMMIT:0:7})"
fetch_commit https://github.com/yxc20089/OpenRA.git "$ENGINE_COMMIT" "$ROOT/engine"
find "$ROOT/engine" -name '*.sh' -exec sed -i 's/\r$//' {} +
# No SKIP_PROTOC: the generated gRPC code checked into the fork is older than
# this commit's .proto file, so it has to be generated (works on x86_64).
make -C "$ROOT/engine" TARGETPLATFORM=unix-generic CONFIGURATION=Release > build.log 2>&1 \
	|| { tail -20 build.log; echo "engine build failed, see $ROOT/build.log"; exit 1; }
for f in OpenRA.dll OpenRA.Game.dll OpenRA.Mods.Common.dll OpenRA.Platforms.Null.dll; do
	test -f "$ROOT/engine/bin/$f" || { echo "engine build incomplete: no bin/$f"; exit 1; }
done
LIBDIR=$(dirname "$(ldconfig -p | grep -m1 'libSDL2-2.0.so.0' | awk '{print $NF}')")
ln -sf "$LIBDIR/libSDL2-2.0.so.0" "$ROOT/engine/bin/SDL2.so"
ln -sf "$LIBDIR/libopenal.so.1" "$ROOT/engine/bin/soft_oal.so"
ln -sf "$LIBDIR/libfreetype.so.6" "$ROOT/engine/bin/freetype6.so"
ln -sf "$LIBDIR/liblua5.1.so.0" "$ROOT/engine/bin/lua51.so"

echo "== OpenRA-RL (yxc20089/OpenRA-RL @ ${OPENRA_RL_COMMIT:0:7})"
fetch_commit https://github.com/yxc20089/OpenRA-RL.git "$OPENRA_RL_COMMIT" "$ROOT/openra-rl"
python3 -m venv "$ROOT/venv"
"$ROOT/venv/bin/pip" install -q --upgrade pip
"$ROOT/venv/bin/pip" install -q -e "$ROOT/openra-rl"

cat > "$ROOT/env.sh" <<EOF
# source this before starting the server
export DOTNET_ROOT=$DOTNET_ROOT DOTNET_CLI_TELEMETRY_OPTOUT=1
export PATH=$DOTNET_ROOT:\$PATH
export OPENRA_PATH=$ROOT/engine
EOF

cat <<EOF

OpenRA-RL is ready in $ROOT.
Start the game server (keep it running):
  source $ROOT/env.sh && BOT_TYPE=normal $ROOT/venv/bin/python -m openra_env.server.app
Then, in another shell, play one game with the example bot:
  $ROOT/venv/bin/python $ROOT/openra-rl/examples/scripted_bot.py --max-steps 2000
EOF
