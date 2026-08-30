// Refuses to package an installer that would look installed and work for
// nothing. electron-builder copies extraResources it finds and is quiet
// about what it does not, so each ingredient is checked here, loudly,
// BEFORE the build starts. Same species as the server refusing to boot
// without OHMWORK_PASSWORD: fail closed, name the fix.
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const spec = JSON.parse(readFileSync(path.join(here, "logisim-bundle.json"), "utf8"));

const required = [
  [path.join(here, "vendor", "logisim", spec.jar_filename),
   "the bundled Logisim JAR — run desktop\\fetch-logisim.ps1"],
  [path.join(here, "vendor", "logisim", "runtime", "bin",
             process.platform === "win32" ? "java.exe" : "java"),
   "the bundled Java runtime — run desktop\\fetch-logisim.ps1"],
  [path.join(here, "..", "web", "dist", "index.html"),
   "the built page — run `npm run build` in web/"],
  // The executable itself, not the directory: desktop-backend/ always
  // exists (it holds a .gitkeep), so a directory check can never fail.
  [path.join(here, "..", "desktop-backend",
             process.platform === "win32" ? "ohmwork-server.exe" : "ohmwork-server"),
   "the PyInstaller backend — run desktop\\build-backend.ps1"],
];

const missing = required.filter(([p]) => !existsSync(p));
if (missing.length) {
  console.error("Refusing to package: the installer would be missing");
  for (const [p, fix] of missing) console.error(`  ${p}\n    -> ${fix}`);
  process.exit(1);
}
console.log("all installer ingredients present");
