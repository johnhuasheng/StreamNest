import fs from "node:fs/promises";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const projectDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const outputPath = path.join(projectDir, "work", "service-monitor-results.json");
const untilArgument = process.argv.find((argument) => argument.startsWith("--until="))?.slice(8);
if (!untilArgument) throw new Error("请使用 --until=<ISO 时间> 指定监测结束时间。");
const until = new Date(untilArgument);
if (!Number.isFinite(until.getTime()) || until.getTime() <= Date.now()) {
  throw new Error("监测结束时间无效或已经过去。");
}

const checks = [
  {
    name: "统一网页",
    url: "http://localhost:3000/",
    validate: async (response) => {
      const html = await response.text();
      return response.ok && html.includes("StreamNest") && html.includes("在线视频下载管理器");
    },
  },
  {
    name: "通用解析服务",
    url: "http://127.0.0.1:8788/healthz",
    validate: async (response) => {
      const payload = await response.json().catch(() => null);
      return response.ok && payload?.status === "ok" && payload?.service === "streamnest-resolver";
    },
  },
  {
    name: "抖音/快手核心",
    url: "http://127.0.0.1:4000/api/health",
    validate: async (response) => {
      const payload = await response.json().catch(() => null);
      return response.ok && payload?.status === "ok";
    },
  },
];

const state = {
  startedAt: new Date().toISOString(),
  until: until.toISOString(),
  completedAt: null,
  checks: 0,
  recoveries: [],
  lastHealthyAt: null,
  healthy: false,
};

async function checkService(check) {
  try {
    const response = await fetch(check.url, { cache: "no-store", signal: AbortSignal.timeout(3000) });
    return await check.validate(response);
  } catch {
    return false;
  }
}

async function saveState() {
  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  await fs.writeFile(outputPath, JSON.stringify(state, null, 2));
}

async function runChecks() {
  const results = await Promise.all(checks.map(async (check) => ({
    name: check.name,
    ok: await checkService(check),
  })));
  state.checks += 1;
  state.healthy = results.every((result) => result.ok);
  if (state.healthy) {
    state.lastHealthyAt = new Date().toISOString();
    return results;
  }

  const failed = results.filter((result) => !result.ok).map((result) => result.name);
  const recovery = { detectedAt: new Date().toISOString(), failed, restored: false, error: null };
  state.recoveries.push(recovery);
  const result = spawnSync(process.execPath, [path.join(projectDir, "streamnest-launcher.mjs"), "--no-open"], {
    cwd: projectDir,
    windowsHide: true,
    encoding: "utf8",
    timeout: 90_000,
  });
  if (result.error || result.status !== 0) {
    recovery.error = result.error?.message || result.stderr?.trim() || `启动器退出码 ${result.status}`;
    return results;
  }
  await new Promise((resolve) => setTimeout(resolve, 3000));
  recovery.restored = (await Promise.all(checks.map(checkService))).every(Boolean);
  return results;
}

let nextStatusAt = 0;
while (Date.now() < until.getTime()) {
  await runChecks();
  await saveState();
  if (Date.now() >= nextStatusAt) {
    process.stdout.write(
      `${new Date().toLocaleString("zh-CN", { hour12: false })}：${state.healthy ? "三个服务均正常" : "发现异常，已尝试恢复"}，累计检查 ${state.checks} 次\n`,
    );
    nextStatusAt = Date.now() + 10 * 60_000;
  }
  await new Promise((resolve) => setTimeout(resolve, 30_000));
}

await runChecks();
state.completedAt = new Date().toISOString();
await saveState();
process.stdout.write(`监测完成：累计检查 ${state.checks} 次，恢复事件 ${state.recoveries.length} 次。\n`);
if (!state.healthy || state.recoveries.some((recovery) => !recovery.restored)) process.exitCode = 1;
