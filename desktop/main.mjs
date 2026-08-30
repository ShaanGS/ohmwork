import { app, BrowserWindow, dialog, ipcMain, net, safeStorage, session } from "electron";
import { createServer } from "node:net";
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { existsSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
let backend = null;
let backendPort = null;
// Electron does not track this for us; see the `before-quit` handler.
let quitting = false;
const PROVIDER_KEYS = new Set([
  "GROQ_API_KEY", "GEMINI_API_KEY", "MISTRAL_API_KEY",
  "OPENROUTER_API_KEY", "CEREBRAS_API_KEY"
]);

function resourcePath(...parts) {
  return app.isPackaged
    ? path.join(process.resourcesPath, ...parts)
    : path.join(here, "..", ...parts);
}

function settingsPath() {
  return path.join(app.getPath("userData"), "secrets.json");
}

async function readStoredKeys() {
  if (!safeStorage.isEncryptionAvailable()) {
    throw new Error("Your operating system's secure credential storage is unavailable.");
  }
  try {
    const encrypted = await readFile(settingsPath(), "utf8");
    const values = JSON.parse(safeStorage.decryptString(Buffer.from(encrypted, "base64")));
    return Object.fromEntries(Object.entries(values).filter(([name, value]) =>
      PROVIDER_KEYS.has(name) && typeof value === "string" && value.length >= 8));
  } catch (error) {
    if (error.code === "ENOENT") return {};
    throw new Error("Ohmwork could not read its encrypted local model-key settings.");
  }
}

async function writeStoredKey(name, value) {
  if (!PROVIDER_KEYS.has(name)) throw new Error("That is not a supported model provider.");
  if (typeof value !== "string" || value.trim().length < 8) {
    throw new Error("That model key is too short to be valid.");
  }
  const keys = await readStoredKeys();
  keys[name] = value.trim();
  await mkdir(app.getPath("userData"), { recursive: true });
  const encrypted = safeStorage.encryptString(JSON.stringify(keys)).toString("base64");
  await writeFile(settingsPath(), encrypted, { encoding: "utf8", mode: 0o600 });
}

async function writeStoredKeys(values) {
  if (!values || typeof values !== "object" || Array.isArray(values)) {
    throw new Error("Model keys must be supplied as a provider list.");
  }
  const entries = Object.entries(values).filter(([, value]) =>
    typeof value === "string" && value.trim().length > 0);
  if (!entries.length) throw new Error("Enter at least one model key.");

  const keys = await readStoredKeys();
  for (const [name, value] of entries) {
    if (!PROVIDER_KEYS.has(name)) throw new Error("That is not a supported model provider.");
    if (value.trim().length < 8) throw new Error("One of the model keys is too short to be valid.");
    keys[name] = value.trim();
  }
  await mkdir(app.getPath("userData"), { recursive: true });
  const encrypted = safeStorage.encryptString(JSON.stringify(keys)).toString("base64");
  await writeFile(settingsPath(), encrypted, { encoding: "utf8", mode: 0o600 });
}

function reserveLoopbackPort() {
  return new Promise((resolve, reject) => {
    const probe = createServer();
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const { port } = probe.address();
      probe.close((error) => error ? reject(error) : resolve(port));
    });
  });
}

function bundledLogisimEnv() {
  // The evaluator ships INSIDE the installer (fetch-logisim.ps1 builds it):
  // the pinned 4.1.0 JAR plus a jlink'd Java 21 runtime. The backend already
  // knows how to run a .jar through OHMWORK_JAVA — that path was built for
  // the Linux container and is reused here unchanged.
  const dir = app.isPackaged
    ? path.join(process.resourcesPath, "logisim")
    : path.join(here, "vendor", "logisim");
  const jar = path.join(dir, "logisim-evolution-4.1.0-all.jar");
  const java = path.join(dir, "runtime", "bin",
    process.platform === "win32" ? "java.exe" : "java");
  if (existsSync(jar) && existsSync(java)) {
    return { OHMWORK_LOGISIM: jar, OHMWORK_JAVA: java };
  }
  if (app.isPackaged) {
    // Fail closed, the way the server does without a password. Measured, not
    // assumed: with no Logisim the backend falls back to an engine that
    // raises on the first solve, so every question fails while the app looks
    // installed — the exact failure ensure-bundle.mjs exists to prevent.
    throw new Error("This build is missing its bundled Logisim evaluator, so "
      + "no answer could be verified. Reinstall Ohmwork; if this build came "
      + "from source, it was packaged without running desktop/fetch-logisim.ps1.");
  }
  return {};   // dev mode: fall back to a system-installed Logisim
}

function backendCommand() {
  if (app.isPackaged) {
    const executable = process.platform === "win32"
      ? resourcePath("backend", "ohmwork-server.exe")
      : resourcePath("backend", "ohmwork-server");
    if (!existsSync(executable)) throw new Error(`Desktop backend is missing: ${executable}`);
    return { command: executable, args: [] };
  }
  return { command: process.env.OHMWORK_PYTHON ?? "python", args: ["-m", "ohmwork.server"] };
}

async function waitForHealthyBackend(port) {
  const url = `http://127.0.0.1:${port}/api/health`;
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      const response = await net.fetch(url);
      if (response.ok) return;
    } catch {
      // The backend is expected to take a moment to start its Python imports.
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("Ohmwork's local backend did not become ready.");
}

async function startBackend() {
  backendPort = await reserveLoopbackPort();
  const password = randomBytes(32).toString("base64url");
  const { command, args } = backendCommand();
  const storedKeys = await readStoredKeys();
  backend = spawn(command, args, {
    cwd: resourcePath(),
    windowsHide: true,
    env: {
      ...process.env,
      PORT: String(backendPort),
      OHMWORK_BIND_HOST: "127.0.0.1",
      OHMWORK_PASSWORD: password,
      OHMWORK_SECURE_COOKIES: "0",
      OHMWORK_STATIC: resourcePath("web", "dist"),
      OHMWORK_LLM: "pool",
      // After process.env deliberately: the bundled evaluator is the PINNED
      // one (4.1.0, the version every published number was measured against),
      // so an OHMWORK_LOGISIM inherited from a parent shell must not
      // silently unpin a packaged app.
      ...bundledLogisimEnv(),
      ...storedKeys,
      PYTHONUNBUFFERED: "1"
    },
    stdio: ["ignore", "pipe", "pipe"]
  });

  let diagnostics = "";
  backend.stderr.on("data", (chunk) => { diagnostics += chunk.toString(); });
  backend.once("exit", (code) => {
    if (code !== 0 && !quitting) {
      dialog.showErrorBox("Ohmwork stopped", diagnostics || `The local backend exited with code ${code}.`);
    }
  });
  await waitForHealthyBackend(backendPort);

  // The password is generated in this main process and never enters the page.
  const login = await net.fetch(`http://127.0.0.1:${backendPort}/api/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password })
  });
  if (!login.ok) throw new Error("Ohmwork's local login could not be established.");
  const cookies = await session.defaultSession.cookies.get({
    url: `http://127.0.0.1:${backendPort}`,
    name: "ohmwork_session"
  });
  if (cookies.length !== 1) throw new Error("Ohmwork returned no usable local session cookie.");
}

function isBackendUrl(url) {
  const parsed = new URL(url);
  return parsed.protocol === "http:" && parsed.hostname === "127.0.0.1"
    && parsed.port === String(backendPort);
}

function createWindow() {
  const window = new BrowserWindow({
    width: 1200, height: 840, minWidth: 900, minHeight: 650, show: false,
    webPreferences: {
      preload: path.join(here, "preload.cjs"),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false
    }
  });
  window.setMenuBarVisibility(false);
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event, url) => {
    if (!isBackendUrl(url)) event.preventDefault();
  });
  window.once("ready-to-show", () => window.show());
  window.loadURL(`http://127.0.0.1:${backendPort}/`);
}

app.whenReady().then(async () => {
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    if (!isBackendUrl(details.url)) return callback({ responseHeaders: details.responseHeaders });
    callback({ responseHeaders: {
      ...details.responseHeaders,
      "Content-Security-Policy": [
        "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
        + "style-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
      ]
    }});
  });
  session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false));
  ipcMain.handle("desktop:provider-state", async () => ({
    encryptionAvailable: safeStorage.isEncryptionAvailable(),
    configured: Object.keys(await readStoredKeys())
  }));
  ipcMain.handle("desktop:save-provider-key", async (_event, name, value) => {
    await writeStoredKey(name, value);
    // The backend reads its keys at process start. A relaunch avoids a second
    // code path that mutates a running server, and guarantees the renderer
    // never sees the key after the one IPC call that stores it.
    setTimeout(() => { app.relaunch(); app.exit(0); }, 150);
    return { restarting: true };
  });
  ipcMain.handle("desktop:save-provider-keys", async (_event, values) => {
    await writeStoredKeys(values);
    setTimeout(() => { app.relaunch(); app.exit(0); }, 150);
    return { restarting: true };
  });
  await startBackend();
  createWindow();
}).catch((error) => { dialog.showErrorBox("Ohmwork could not start", String(error)); app.quit(); });

app.on("window-all-closed", () => app.quit());
app.on("before-quit", () => {
  // SET BEFORE THE KILL, and that ordering is the whole point. Killing the
  // backend makes it exit non-zero, and the `exit` handler above shows an
  // error box for any non-zero exit that is not a shutdown. Electron never
  // sets `isQuitting` itself, so without this line a perfectly normal quit
  // raises "Ohmwork stopped" over a backend that stopped because it was told
  // to.
  quitting = true;
  if (backend && !backend.killed) backend.kill();
});
