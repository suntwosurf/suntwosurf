// Node module hooks: resolve 0 A.D. module paths ("simulation/ai/...") the
// way the game's VFS does, by searching the given roots in order (later
// mods override earlier ones in the game; here the first root wins).
import { existsSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

let roots = [];

export function initialize(data)
{
	roots = data.roots;
}

export async function resolve(specifier, context, next)
{
	if (specifier.startsWith("simulation/"))
		for (const root of roots)
		{
			const file = join(root, specifier);
			if (existsSync(file))
				return { "url": pathToFileURL(file).href, "shortCircuit": true, "format": "module" };
		}
	return next(specifier, context);
}

export async function load(url, context, next)
{
	// The game's files are ES modules without a package.json saying so.
	if (url.startsWith("file:") && url.endsWith(".js"))
		return next(url, { ...context, "format": "module" });
	return next(url, context);
}
