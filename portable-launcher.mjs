import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));
const runtime = path.join(root, "runtime");
const nodeExe = path.join(runtime, "node.exe");
const ffmpegExe = path.join(runtime, "ffmpeg.exe");
const resolverExe = path.join(runtime, "resolver", "streamnest-resolver.exe");
const webCli = path.join(root, "node_modules", "vinext", "dist", "cli.js");
const logs = path.join(root, "work", "launcher-logs");
const pidFile = path.join(logs, "runtime-pids.json");
const outputDir = path.join(root, "下载内容");

for (const file of [nodeExe, ffmpegExe, resolverExe, webCli, path.join(root, "dist", "server", "index.js"), path.join(root, "haijiao-domains.json")]) {
  if (!fs.existsSync(file)) throw new Error(`发布包缺少文件：${path.relative(root, file)}`);
}
fs.mkdirSync(logs, { recursive: true });
fs.mkdirSync(outputDir, { recursive: true });

const ffmpegCheck = spawnSync(ffmpegExe, ["-version"], { windowsHide: true, stdio: "ignore", timeout: 10_000 });
if (ffmpegCheck.error || ffmpegCheck.status !== 0) throw new Error("随包提供的 FFmpeg 无法运行。 ");

const env = {
  ...process.env,
  NODE_ENV: "production",
  STREAMNEST_APP_DIR: root,
  STREAMNEST_OUTPUT_DIR: outputDir,
  STREAMNEST_ALLOWED_ORIGINS: "http://localhost:3000,http://127.0.0.1:3000",
  PATH: `${runtime}${path.delimiter}${process.env.PATH || ""}`,
};

function pidAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try { process.kill(pid, 0); return true; } catch { return false; }
}

function savedPids() {
  try { return JSON.parse(fs.readFileSync(pidFile, "utf8")); } catch { return {}; }
}

function portOpen(port) {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host: "127.0.0.1", port });
    let finished = false;
    const done = (result) => {
      if (finished) return;
      finished = true;
      socket.destroy();
      resolve(result);
    };
    socket.setTimeout(800);
    socket.once("connect", () => done(true));
    socket.once("timeout", () => done(false));
    socket.once("error", () => done(false));
  });
}

async function healthy(url, validate) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(3000), cache: "no-store" });
    return response.ok && await validate(response);
  } catch { return false; }
}

const resolverHealthy = () => healthy("http://127.0.0.1:8788/healthz", async (response) => {
  const data = await response.json();
  return data?.status === "ok" && data?.service === "streamnest-resolver";
});
const webHealthy = () => healthy("http://127.0.0.1:3000/", async (response) => {
  const html = await response.text();
  return html.includes("StreamNest") && html.includes("在线视频下载管理器");
});

async function waitFor(check, seconds = 45) {
  const deadline = Date.now() + seconds * 1000;
  while (Date.now() < deadline) {
    if (await check()) return true;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return false;
}

function startService(file, args, name) {
  const stdout = fs.openSync(path.join(logs, `${name}.log`), "a");
  const stderr = fs.openSync(path.join(logs, `${name}.error.log`), "a");
  try {
    const child = spawn(file, args, {
      cwd: root,
      env,
      detached: true,
      windowsHide: true,
      stdio: ["ignore", stdout, stderr],
    });
    child.unref();
    return child.pid;
  } finally {
    fs.closeSync(stdout);
    fs.closeSync(stderr);
  }
}

const old = savedPids();
const [resolverPortUsed, webPortUsed] = await Promise.all([portOpen(8788), portOpen(3000)]);
if (resolverPortUsed && !(pidAlive(old.resolver) && await resolverHealthy())) {
  throw new Error("8788 端口被其他程序占用。请先关闭它，再启动 StreamNest 便携版。");
}
if (webPortUsed && !(pidAlive(old.web) && await webHealthy())) {
  throw new Error("3000 端口被其他程序占用。请先关闭它，再启动 StreamNest 便携版。");
}

const pids = { ...old };
if (!resolverPortUsed) pids.resolver = startService(resolverExe, [], "resolver");
if (!webPortUsed) pids.web = startService(nodeExe, [webCli, "start", "--hostname", "127.0.0.1", "--port", "3000"], "web");
fs.writeFileSync(pidFile, JSON.stringify(pids), "utf8");

const [resolverReady, webReady] = await Promise.all([waitFor(resolverHealthy), waitFor(webHealthy)]);
if (!resolverReady || !webReady) {
  throw new Error("服务未能启动。请查看 work\\launcher-logs 中的 resolver / web 错误日志。");
}

if (!process.argv.includes("--no-open")) {
  const browser = spawn("cmd.exe", ["/d", "/s", "/c", "start", "", "http://localhost:3000/"], {
    detached: true, windowsHide: true, stdio: "ignore",
  });
  browser.unref();
}
console.log("StreamNest 已启动：http://localhost:3000/");
console.log(`下载内容保存在：${outputDir}`);
