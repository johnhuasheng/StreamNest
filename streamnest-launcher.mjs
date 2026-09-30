import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const projectDir = path.dirname(fileURLToPath(import.meta.url));
const coreCandidates = [
  process.env.STREAMNEST_CORE_DIR,
].filter(Boolean);
const coreDir = coreCandidates.find((candidate) => fs.existsSync(path.join(candidate, "package.json")))
  || null;
const backendDir = path.join(projectDir, "backend");
const pythonExe = path.join(backendDir, ".venv", "Scripts", "python.exe");
const logDir = path.join(projectDir, "work", "launcher-logs");
fs.mkdirSync(logDir, { recursive: true });
const launcherLockPath = path.join(logDir, "launcher.lock");
const servedWebBuildPath = path.join(logDir, "served-web-build.txt");
let launcherLockHandle = null;

function processIsAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error?.code !== "ESRCH";
  }
}

async function acquireLauncherLock() {
  const deadline = Date.now() + 90_000;
  while (Date.now() < deadline) {
    try {
      launcherLockHandle = fs.openSync(launcherLockPath, "wx");
      fs.writeFileSync(launcherLockHandle, `${process.pid}\n`, "utf8");
      return;
    } catch (error) {
      if (error?.code !== "EEXIST") throw error;
      try {
        const ownerPid = Number(fs.readFileSync(launcherLockPath, "utf8").trim());
        const age = Date.now() - fs.statSync(launcherLockPath).mtimeMs;
        if (!processIsAlive(ownerPid) || age > 15 * 60_000) {
          fs.unlinkSync(launcherLockPath);
          continue;
        }
      } catch (lockError) {
        if (lockError?.code === "ENOENT") continue;
      }
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }
  throw new Error("另一个 StreamNest 启动任务仍在运行，请稍后再试。");
}

function releaseLauncherLock() {
  if (launcherLockHandle === null) return;
  try {
    fs.closeSync(launcherLockHandle);
  } catch {
    // The process may already be closing its file handles.
  }
  launcherLockHandle = null;
  try {
    fs.unlinkSync(launcherLockPath);
  } catch {
    // A concurrent waiter may already have removed a stale lock.
  }
}

process.once("exit", releaseLauncherLock);

function rotateLogFile(filePath, maxBytes = 8 * 1024 * 1024) {
  try {
    if (!fs.existsSync(filePath) || fs.statSync(filePath).size < maxBytes) return;
    const oldest = `${filePath}.2`;
    const previous = `${filePath}.1`;
    if (fs.existsSync(oldest)) fs.unlinkSync(oldest);
    if (fs.existsSync(previous)) fs.renameSync(previous, oldest);
    fs.renameSync(filePath, previous);
  } catch {
    // A running Windows process can briefly keep its log handle open; retry on the next launch.
  }
}

function rotateServiceLogs(logName) {
  rotateLogFile(path.join(logDir, `${logName}.log`));
  rotateLogFile(path.join(logDir, `${logName}.error.log`));
}

function assertSupportedNode() {
  const [major, minor] = process.versions.node.split(".").map(Number);
  if (major < 22 || (major === 22 && minor < 13)) {
    throw new Error(`Node.js 版本过低（当前 ${process.versions.node}），请安装 22.13 或更高版本。`);
  }
}

function runLogged(command, args, cwd, logName) {
  rotateServiceLogs(logName);
  const result = spawnSync(command, args, {
    cwd,
    windowsHide: true,
    encoding: "utf8",
    timeout: 10 * 60_000,
  });
  fs.appendFileSync(path.join(logDir, `${logName}.log`), result.stdout || "");
  fs.appendFileSync(path.join(logDir, `${logName}.error.log`), result.stderr || "");
  if (result.error || result.status !== 0) {
    throw new Error(`${logName} 自动准备失败，请查看 work/launcher-logs 中的日志。`);
  }
  return result.stdout.trim();
}

function ensureWebDependencies() {
  if (fs.existsSync(path.join(projectDir, "node_modules", ".bin", "vinext.cmd"))) return;
  runLogged("cmd.exe", ["/d", "/s", "/c", "npm.cmd install"], projectDir, "web-dependencies");
}

function newestMtime(target) {
  if (!fs.existsSync(target)) return 0;
  const stat = fs.statSync(target);
  if (!stat.isDirectory()) return stat.mtimeMs;
  return fs.readdirSync(target, { withFileTypes: true }).reduce((latest, entry) => (
    Math.max(latest, newestMtime(path.join(target, entry.name)))
  ), stat.mtimeMs);
}

function ensureWebBuild() {
  const marker = path.join(projectDir, "dist", "server", "index.js");
  const buildTime = fs.existsSync(marker) ? fs.statSync(marker).mtimeMs : 0;
  const sourceTime = Math.max(...[
    "app",
    "public",
    "package.json",
    "package-lock.json",
    "haijiao-domains.json",
    "next.config.ts",
    "vite.config.ts",
    ".env.local",
  ].map((entry) => newestMtime(path.join(projectDir, entry))));
  if (buildTime >= sourceTime) return false;
  runLogged("cmd.exe", ["/d", "/s", "/c", "npm.cmd run build"], projectDir, "web-build");
  return true;
}

function currentWebBuildStamp() {
  const marker = path.join(projectDir, "dist", "server", "index.js");
  return fs.existsSync(marker) ? String(Math.trunc(fs.statSync(marker).mtimeMs)) : "";
}

function servedWebBuildStamp() {
  try {
    return fs.readFileSync(servedWebBuildPath, "utf8").trim();
  } catch {
    return "";
  }
}

function stopOwnedWebServer(port = 3000) {
  const expectedPath = path.join(projectDir, "node_modules").replaceAll("'", "''");
  const script = [
    `$connection = Get-NetTCPConnection -State Listen -LocalPort ${port} -ErrorAction SilentlyContinue | Select-Object -First 1`,
    "if (-not $connection) { Write-Output 'NOT_RUNNING'; exit 0 }",
    "$owner = Get-CimInstance Win32_Process -Filter (\"ProcessId = $($connection.OwningProcess)\")",
    `if ($owner.Name -ne 'node.exe' -or $owner.CommandLine -notlike '*${expectedPath}*vinext*') { Write-Error '端口由其他程序占用'; exit 2 }`,
    "Stop-Process -Id $owner.ProcessId -Force",
    "Write-Output 'STOPPED'",
  ].join("; ");
  const result = spawnSync(
    "powershell.exe",
    ["-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
    { cwd: projectDir, windowsHide: true, encoding: "utf8", timeout: 15_000 },
  );
  if (result.error || result.status !== 0) {
    throw new Error("网页已经更新，但旧的 3000 端口进程不是 StreamNest，无法安全自动重启。");
  }
  return result.stdout.includes("STOPPED");
}

function normalizeVersion(version) {
  return String(version).trim().split(".").map((part) => String(Number(part))).join(".");
}

function ensureResolverDependencies() {
  const requirements = fs.readFileSync(path.join(backendDir, "requirements.txt"), "utf8");
  const expected = {
    fastapi: requirements.match(/^fastapi==([^\s]+)$/m)?.[1],
    uvicorn: requirements.match(/^uvicorn(?:\[[^\]]+\])?==([^\s]+)$/m)?.[1],
    ytDlp: requirements.match(/^yt-dlp(?:\[[^\]]+\])?==([^\s]+)$/m)?.[1],
  };
  if (!expected.fastapi || !expected.uvicorn || !expected.ytDlp) {
    throw new Error("backend/requirements.txt 缺少固定的 FastAPI、Uvicorn 或 yt-dlp 版本。");
  }
  const installed = spawnSync(
    pythonExe,
    [
      "-c",
      "import curl_cffi, fastapi, uvicorn, yt_dlp; print('|'.join((fastapi.__version__, uvicorn.__version__, yt_dlp.version.__version__)))",
    ],
    { cwd: backendDir, windowsHide: true, encoding: "utf8", timeout: 30_000 },
  );
  const versions = installed.stdout.trim().split("|");
  if (
    !installed.error
    && installed.status === 0
    && normalizeVersion(versions[0]) === normalizeVersion(expected.fastapi)
    && normalizeVersion(versions[1]) === normalizeVersion(expected.uvicorn)
    && normalizeVersion(versions[2]) === normalizeVersion(expected.ytDlp)
  ) return;
  runLogged(
    pythonExe,
    ["-m", "pip", "install", "-r", path.join(backendDir, "requirements-dev.txt")],
    backendDir,
    "resolver-dependencies",
  );
}

function assertFfmpeg() {
  const result = spawnSync("ffmpeg", ["-version"], { windowsHide: true, stdio: "ignore", timeout: 10_000 });
  if (result.error || result.status !== 0) {
    throw new Error("没有找到 FFmpeg；视频合并、MP3 和首帧图片功能无法运行。");
  }
}

function hostPortIsOpen(host, port) {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host, port });
    const finish = (open) => {
      socket.destroy();
      resolve(open);
    };
    socket.setTimeout(700);
    socket.once("connect", () => finish(true));
    socket.once("timeout", () => finish(false));
    socket.once("error", () => finish(false));
  });
}

async function portIsOpen(port) {
  return await hostPortIsOpen("127.0.0.1", port) || await hostPortIsOpen("::1", port);
}

async function responseIsHealthy(url, validate) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(1200), cache: "no-store" });
    if (!response.ok) return false;
    return await validate(response);
  } catch {
    return false;
  }
}

async function webEntryIsHealthy() {
  try {
    const response = await fetch("http://127.0.0.1:3000/", {
      signal: AbortSignal.timeout(5000), cache: "no-store",
    });
    if (!response.ok) return false;
    const html = await response.text();
    if (!html.includes("StreamNest") || !html.includes("在线视频下载管理器")) return false;
    const assets = [...new Set([
      ...[...html.matchAll(/<script\b[^>]*\bsrc="([^"]+)"/g)].map((match) => match[1]),
      ...[...html.matchAll(/<link\b[^>]*\bhref="([^"]+)"/g)].map((match) => match[1]),
    ].filter((src) => src.startsWith("/_next/static/")))];
    if (assets.length === 0) return false;
    const statuses = await Promise.all(assets.map(async (src) => {
      try {
        const asset = await fetch(new URL(src, "http://127.0.0.1:3000"), {
          signal: AbortSignal.timeout(5000), cache: "no-store",
        });
        const contentType = asset.headers.get("content-type") || "";
        return asset.ok && (src.endsWith(".css")
          ? contentType.includes("css") : contentType.includes("javascript"));
      } catch {
        return false;
      }
    }));
    return statuses.every(Boolean);
  } catch {
    return false;
  }
}

function startHidden(command, args, cwd, logName) {
  const launchTag = new Date().toISOString().replaceAll(":", "-").replaceAll(".", "-");
  const outputPath = path.join(logDir, `${logName}-${launchTag}.log`);
  const errorPath = path.join(logDir, `${logName}-${launchTag}.error.log`);
  const quotePowerShell = (value) => `'${String(value).replaceAll("'", "''")}'`;
  const argumentList = args.map(quotePowerShell).join(", ");
  const startCommand = [
    `Start-Process -FilePath ${quotePowerShell(command)} -ArgumentList @(${argumentList})`,
    `-WorkingDirectory ${quotePowerShell(cwd)} -WindowStyle Hidden`,
    `-RedirectStandardOutput ${quotePowerShell(outputPath)}`,
    `-RedirectStandardError ${quotePowerShell(errorPath)}`,
  ].join(" ");
  const script = [
    "$ErrorActionPreference = 'Stop'",
    startCommand,
  ].join(";\n");
  const encoded = Buffer.from(script, "utf16le").toString("base64");
  const bootstrap = spawnSync(
    "powershell.exe",
    ["-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
    { cwd, windowsHide: true, stdio: "ignore", timeout: 15_000 },
  );
  if (bootstrap.error || bootstrap.status !== 0) {
    throw new Error(`${logName} 后台启动命令未能执行，请查看 work/launcher-logs 中的日志。`);
  }
}

async function waitFor(check, seconds = 35) {
  const deadline = Date.now() + seconds * 1000;
  while (Date.now() < deadline) {
    if (await check()) return true;
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  return false;
}

await acquireLauncherLock();
assertSupportedNode();

if (!fs.existsSync(pythonExe)) {
  throw new Error(`没有找到 StreamNest 本机解析服务：${pythonExe}`);
}

ensureWebDependencies();
const webWasRebuilt = ensureWebBuild();
const webBuildStamp = currentWebBuildStamp();
assertFfmpeg();

const [coreWasOpen, resolverWasOpen, detectedWebWasOpen] = await Promise.all([
  portIsOpen(4000),
  portIsOpen(8788),
  portIsOpen(3000),
]);
let webWasOpen = detectedWebWasOpen;
if (webWasOpen && (
  webWasRebuilt
  || servedWebBuildStamp() !== webBuildStamp
  || !(await webEntryIsHealthy())
)) {
  stopOwnedWebServer(3000);
  await waitFor(async () => !(await portIsOpen(3000)), 8);
  webWasOpen = false;
}
if (!coreWasOpen && coreDir) {
  startHidden(
    "cmd.exe",
    ["/d", "/s", "/c", "set PORT=4000&& npm.cmd start"],
    coreDir,
    "domestic-core",
  );
}
if (!resolverWasOpen) {
  ensureResolverDependencies();
  startHidden(
    pythonExe,
    ["-m", "uvicorn", "streamnest_api.main:app", "--host", "127.0.0.1", "--port", "8788"],
    backendDir,
    "resolver-api",
  );
}
if (!webWasOpen) {
  startHidden("cmd.exe", ["/d", "/s", "/c", "npm.cmd run start"], projectDir, "streamnest-web");
}

const [coreReady, resolverReady, webReady] = await Promise.all([
  (coreWasOpen || coreDir) ? waitFor(async () => {
    try {
      const response = await fetch("http://127.0.0.1:8788/v1/core/health", {
        signal: AbortSignal.timeout(5000), cache: "no-store",
      });
      return response.ok;
    } catch {
      return false;
    }
  }, coreWasOpen ? 4 : 35) : Promise.resolve(false),
  waitFor(() => responseIsHealthy("http://127.0.0.1:8788/healthz", async (response) => {
    const payload = await response.json().catch(() => null);
    return payload?.status === "ok" && payload?.service === "streamnest-resolver";
  })),
  waitFor(webEntryIsHealthy),
]);
if (!resolverReady || !webReady) {
  throw new Error("统一下载管理器启动失败，请把 work/launcher-logs 中的错误日志发给我。");
}
if (!coreReady) console.warn("抖音/快手下载核心缺失或接口不兼容；其余平台仍可使用。");
fs.writeFileSync(servedWebBuildPath, `${webBuildStamp}\n`, "utf8");

if (!process.argv.includes("--no-open")) {
  const browser = spawn("cmd.exe", ["/c", "start", "", "http://localhost:3000/"], {
    detached: true,
    windowsHide: true,
    stdio: "ignore",
  });
  browser.unref();
}

releaseLauncherLock();
console.log("StreamNest 统一下载管理器已经启动：http://localhost:3000/");
