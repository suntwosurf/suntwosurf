// node --import ./register.mjs ...   with ZADBOT_JS_ROOTS=root1<path-sep>root2
import { register } from "node:module";
import { delimiter } from "node:path";

const roots = (process.env.ZADBOT_JS_ROOTS || "").split(delimiter).filter(Boolean);
register("./hooks.mjs", import.meta.url, { "data": { roots } });
