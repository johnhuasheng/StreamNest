import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { access, readFile } from "node:fs/promises";
import { createServer } from "node:net";
import test from "node:test";
import { fileURLToPath } from "node:url";

const projectRoot = new URL("../", import.meta.url);

async function render() {
  const port = await new Promise((resolve, reject) => {
    const socket = createServer();
    socket.once("error", reject);
    socket.listen(0, "127.0.0.1", () => {
      const address = socket.address();
      socket.close(() => resolve(address.port));
    });
  });
  const child = spawn(process.execPath, [fileURLToPath(new URL("../node_modules/vinext/dist/cli.js", import.meta.url)), "start", "--hostname", "127.0.0.1", "--port", String(port)], {
    cwd: fileURLToPath(projectRoot),
    stdio: "ignore",
  });
  for (let attempt = 0; attempt < 60; attempt += 1) {
    if (child.exitCode !== null) break;
    try {
      const response = await fetch(`http://127.0.0.1:${port}/`, { signal: AbortSignal.timeout(1000) });
      return { response, child };
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
  child.kill();
  throw new Error("Vinext production server did not start");
}

test("server-renders the StreamNest download manager app", async () => {
  const { response, child } = await render();
  try {
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<html lang="zh-CN">/);
  assert.match(html, /<title>StreamNest \| 在线视频下载管理器<\/title>/);
  assert.match(html, /在线视频下载管理器/);
  assert.match(html, /新建下载任务/);
  assert.match(html, /粘贴在线视频链接/);
  assert.match(html, /查看可识别的[\s\S]{0,30}31[\s\S]{0,30}个网站/);
  assert.match(html, /红果短剧/);
  assert.match(html, /需年龄确认/);
  assert.match(html, /下载队列/);
  assert.match(html, /选择一个任务/);
  assert.doesNotMatch(html, /支持平台|使用指南|常见问题|想看的视频/);
  assert.match(html, /property="og:image" content="http:\/\/localhost:3000\/og-manager.png"/);
  assert.doesNotMatch(html, /codex-preview|SkeletonPreview|react-loading-skeleton/);
  } finally {
    child.kill();
  }
});

test("finished project contains its brand asset and no starter preview", async () => {
  const [page, layout, styles, packageJson, launcher, launcherCommand] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
    readFile(new URL("../streamnest-launcher.mjs", import.meta.url), "utf8"),
    readFile(new URL("../打开StreamNest统一下载管理器.cmd", import.meta.url), "utf8"),
  ]);

  assert.match(page, /StreamNest/);
  assert.match(page, /const platforms/);
  assert.doesNotMatch(page, /pornhub\.org|xvideos2\.com/);
  assert.match(page, /此链接未返回可核实的分辨率/);
  assert.match(page, /选最高分辨率/);
  assert.match(page, /选快速版本/);
  assert.match(page, /Bilibili/);
  assert.match(page, /YouTube/);
  assert.match(page, /Pornhub/);
  assert.match(page, /XVideos/);
  assert.match(page, /Eporner/);
  assert.match(page, /XNXX/);
  assert.match(page, /Rule34Video/);
  assert.match(page, /HQPorner/);
  assert.match(page, /Beeg/);
  assert.match(page, /SxyPrn/);
  assert.match(page, /SpankBang/);
  assert.match(page, /海角网/);
  assert.match(page, /XMoviesForYou/);
  assert.match(page, /Pinterest/);
  assert.match(page, /NEXT_PUBLIC_RESOLVER_API_URL/);
  assert.match(page, /download_ticket/);
  assert.match(page, /选择画质/);
  assert.match(page, /封面原图/);
  assert.match(page, /下载进度/);
  assert.match(page, /剩余时间/);
  assert.match(page, /async function saveFileToOutputFolder/);
  assert.match(page, /async function saveFileToOutputFolder[\s\S]{0,500}method: "POST"/);
  assert.match(page, /默认保存到当前项目的“下载内容”文件夹/);
  assert.doesNotMatch(page, /URL\.createObjectURL\(blob\)/);
  assert.doesNotMatch(page, /triggerBrowserSave\(`\$\{resolverApiUrl\}\/v1/);
  assert.match(page, /extractVideoUrl/);
  assert.match(page, /自动提取视频链接/);
  assert.match(page, /分享文案和 Markdown 链接/);
  assert.match(page, /正在自动重新解析并继续下载/);
  assert.match(page, /抖音\/快手下载核心在线/);
  assert.match(page, /setInterval\(\(\) => void checkServices\(\), 15_000\)/);
  assert.match(page, /\/v1\/core\/tasks/);
  assert.match(page, /quality: selected\.coreQuality[\s\S]{0,120}format: "mp4"/);
  assert.match(page, /currentTitle !== "解析中…"[\s\S]{0,120}currentTitle !== "未命名视频"/);
  assert.match(page, /\?consume=true/);
  assert.match(page, /handleImageDownloadClick/);
  assert.match(page, /▧ 下载图片/);
  assert.match(page, /helper-complete-actions/);
  assert.match(page, /再次下载视频/);
  assert.match(page, /streamnest-download-tasks-v2/);
  assert.doesNotMatch(page, /演示模式|解析预览/);
  assert.match(layout, /\/og-manager\.png/);
  assert.match(styles, /@media \(max-width: 420px\)[\s\S]*\.download-action-row \{ grid-template-columns: 1fr; \}/);
  assert.doesNotMatch(page, /_sites-preview|SkeletonPreview/);
  assert.doesNotMatch(packageJson, /react-loading-skeleton/);
  assert.match(packageJson, /vinext start --hostname 127\.0\.0\.1/);
  assert.match(packageJson, /tsc --noEmit --incremental false/);
  assert.doesNotMatch(packageJson, /@cloudflare\/workers-types/);
  assert.match(launcher, /ensureResolverDependencies/);
  assert.match(launcher, /import curl_cffi, fastapi, uvicorn, yt_dlp/);
  assert.match(launcher, /ensureWebBuild/);
  assert.match(launcher, /const webWasRebuilt = ensureWebBuild\(\)/);
  assert.match(launcher, /stopOwnedWebServer\(3000\)/);
  assert.match(launcher, /webEntryIsHealthy/);
  assert.match(launcher, /assets\.length === 0/);
  assert.match(launcher, /rotateLogFile/);
  assert.match(launcher, /acquireLauncherLock/);
  assert.match(launcher, /launcher\.lock/);
  assert.match(launcher, /npm\.cmd run start/);
  assert.doesNotMatch(launcher, /npm\.cmd run dev/);
  assert.match(launcher, /Start-Process/);
  assert.match(launcher, /-WindowStyle Hidden/);
  assert.match(launcher, /-EncodedCommand/);
  assert.match(launcher, /normalizeVersion/);
  assert.match(launcher, /\/healthz/);
  assert.match(launcher, /\/v1\/core\/health/);
  assert.match(launcher, /set PORT=4000&& npm\.cmd start/);
  assert.match(launcher, /if \(!resolverReady \|\| !webReady\)/);
  assert.doesNotMatch(launcher, /Desktop\\\\deepseek"/);
  assert.match(launcherCommand, /streamnest-launcher\.mjs %\*/);
  assert.match(launcherCommand, /chcp 65001/);
  assert.equal(launcherCommand.replaceAll("\r\n", "").includes("\n"), false);
  await access(new URL("public/og-manager.png", projectRoot));
  await assert.rejects(access(new URL("app/_sites-preview/SkeletonPreview.tsx", projectRoot)));
});
