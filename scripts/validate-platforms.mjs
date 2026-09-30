import fs from "node:fs/promises";
import path from "node:path";
import { preferredFormatId, videoHeight } from "../app/quality.ts";

const api = process.env.STREAMNEST_VALIDATION_API || "http://127.0.0.1:8788";
const genericSamples = [
  { platform: "YouTube", url: "https://www.youtube.com/watch?v=YE7VzlLtp-4" },
  { platform: "Bilibili", url: "https://www.bilibili.com/video/BV18Sb264EXf" },
  { platform: "X", url: "https://x.com/defiprincess/status/2091206304647807362" },
  { platform: "TED", url: "https://www.ted.com/talks/tim_urban_inside_the_mind_of_a_master_procrastinator" },
  { platform: "微博", url: "https://m.weibo.cn/status/5334824403343686" },
  { platform: "Facebook", url: "https://www.facebook.com/reel/1195289147628387" },
  { platform: "Reddit", url: "https://www.reddit.com/r/Unexpected/comments/1cl9h0u/the_insurance_claim_will_be_interesting/" },
  { platform: "AcFun", url: "https://www.acfun.cn/v/ac35457073" },
  { platform: "红果短剧", url: "https://hongguoduanju.com/player/7684649550316833817" },
  { platform: "XVideos", url: "https://www.xvideos.com/video.omikotkd693/she_let_me_rub_her_pussy_i_came_inside_her", adult: true },
  { platform: "Pornhub", url: "https://www.pornhub.com/view_video.php?viewkey=648719015", adult: true },
  { platform: "Eporner", url: "https://www.eporner.com/video-yXwtdO6tsKx/", adult: true },
  { platform: "XNXX", url: "https://www.xnxx.com/search/sex?top&id=25752905", adult: true },
  { platform: "Rule34Video", url: "https://rule34video.com/video/4573701/wednesday-strategy-meeting/", adult: true },
  { platform: "HQPorner", url: "https://hqporner.com/hdporn/127621-lets_get_into_the_male_anatomy.html", adult: true },
  { platform: "Beeg", url: "https://beeg.com/-0547745029333362", adult: true },
  { platform: "SxyPrn", url: "https://sxyprn.com/post/6ab7d17a66c8d.html", adult: true },
  { platform: "SpankBang", url: "https://spankbang.com/98t61/video/porn", adult: true },
  {
    platform: "XMoviesForYou",
    url: "https://xmoviesforyou.com/fillupmymom-armani-black-my-stepmom-is-hotter-than-before",
    adult: true,
  },
  { platform: "Dailymotion", url: "https://www.dailymotion.com/video/x5kesuj" },
  { platform: "TikTok", url: "https://www.tiktok.com/@patroxofficial/video/6742501081818877190?langCountry=en" },
  { platform: "Instagram", url: "https://www.instagram.com/reel/Chunk8-jurw/" },
  { platform: "Twitch", url: "https://clips.twitch.tv/FaintLightGullWholeWheat" },
  { platform: "SoundCloud", url: "https://soundcloud.com/ethmusic/lostin-powers-she-so-heavy", mediaKind: "audio" },
  { platform: "Pinterest", url: "https://www.pinterest.com/pin/664281013778109217/" },
];
const coreSamples = [
  { platform: "抖音", url: "https://v.douyin.com/dFkCjApSoUo/" },
  { platform: "快手", url: "https://www.kuaishou.com/short-video/3x2ynp7nvk7ndwq?authorId=3x69ufm6dj97dby" },
];
const qualityMode = process.argv.includes("--quality=highest") ? "highest" : "fast";
const requestedPlatforms = new Set(process.argv.slice(2).filter((value) => !value.startsWith("--")).map((value) => value.toLowerCase()));
const selectedGenericSamples = requestedPlatforms.size
  ? genericSamples.filter((sample) => requestedPlatforms.has(sample.platform.toLowerCase()))
  : genericSamples;
const selectedCoreSamples = requestedPlatforms.size
  ? coreSamples.filter((sample) => requestedPlatforms.has(sample.platform.toLowerCase()))
  : coreSamples;
if (requestedPlatforms.size && selectedGenericSamples.length + selectedCoreSamples.length === 0) {
  throw new Error(`没有找到指定平台：${[...requestedPlatforms].join(", ")}`);
}
const resultSlug = [...requestedPlatforms]
  .sort()
  .join("-")
  .replace(/[^a-z0-9\u4e00-\u9fff_-]+/gi, "-")
  .replace(/^-+|-+$/g, "");
const resultPath = path.resolve(
  "work",
  requestedPlatforms.size
    ? `platform-validation-results-${resultSlug || "subset"}${qualityMode === "highest" ? "-highest" : ""}.json`
    : `platform-validation-results${qualityMode === "highest" ? "-highest" : ""}.json`,
);

const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function jsonRequest(pathname, options = {}, retries = 3) {
  let lastError;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    try {
      const response = await fetch(`${api}${pathname}`, {
        ...options,
        signal: AbortSignal.timeout(75_000),
        headers: { Accept: "application/json", ...(options.headers || {}) },
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(payload?.error?.message || payload?.detail || `HTTP ${response.status}`);
      return payload;
    } catch (error) {
      lastError = error;
      if (attempt < retries) await sleep(2_000 * (attempt + 1));
    }
  }
  throw lastError;
}

async function waitForGenericFile(ticket) {
  const created = await jsonRequest(`/v1/jobs/${encodeURIComponent(ticket)}`, { method: "POST" });
  // The highest rendition can be several hundred megabytes.  Give it more
  // time than the low-bandwidth smoke test, while still bounding the run.
  const defaultTimeout = qualityMode === "highest" ? 30 * 60_000 : 12 * 60_000;
  const configuredTimeout = Number(process.env.STREAMNEST_VALIDATION_JOB_TIMEOUT_MS);
  const deadline = Date.now() + (Number.isFinite(configuredTimeout) && configuredTimeout > 0 ? configuredTimeout : defaultTimeout);
  let lastProgress = 0;
  while (Date.now() < deadline) {
    const state = await jsonRequest(`/v1/jobs/${encodeURIComponent(created.job_id)}`, {}, 0);
    if (state.status === "ready" && state.file_ticket) return state;
    if (state.status === "failed") throw new Error(state.error || "下载任务失败");
    lastProgress = Number(state.progress || 0);
    await sleep(900);
  }
  throw new Error(`下载验证超时，最后报告进度 ${lastProgress.toFixed(1)}%；任务可能仍在后台运行`);
}

async function readHead(pathname) {
  const response = await fetch(`${api}${pathname}`, {
    headers: { Range: "bytes=0-63", Accept: "application/octet-stream" },
    signal: AbortSignal.timeout(60_000),
  });
  if (![200, 206].includes(response.status)) throw new Error(`文件响应 HTTP ${response.status}`);
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (!bytes.length) throw new Error("文件内容为空");
  return {
    status: response.status,
    contentType: response.headers.get("content-type"),
    signature: Array.from(bytes.slice(0, 12), (value) => value.toString(16).padStart(2, "0")).join(" "),
  };
}

function assertMediaHead(head, mediaKind) {
  const expectedPrefix = mediaKind === "audio" ? "audio/" : "video/";
  if (!head.contentType?.startsWith(expectedPrefix)) {
    throw new Error(`媒体类型异常：${head.contentType}`);
  }
}

function assertImageHead(head) {
  if (!head.contentType?.startsWith("image/")) throw new Error(`图片类型异常：${head.contentType}`);
}

async function validateGeneric(sample) {
  const resolved = await jsonRequest("/v1/resolve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: sample.url, adult_confirmed: sample.adult === true }),
  });
  const media = sample.mediaKind === "audio"
    ? resolved.formats.find((format) => format.kind === "音频")
    : qualityMode === "highest"
      ? resolved.formats.find((format) => format.id === preferredFormatId(sample.platform, resolved.formats))
      : resolved.formats.find((format) => format.id === "video-fast")
        || resolved.formats.find((format) => !["图片", "音频"].includes(format.kind));
  const image = resolved.formats.find((format) => format.kind === "图片");
  if (!media) throw new Error("没有可验证的媒体格式");
  if (!image) throw new Error("没有可验证的图片格式");

  process.stdout.write(`${sample.platform}: 下载${sample.mediaKind === "audio" ? "音频" : "视频"} ${media.label}\n`);
  const mediaState = await waitForGenericFile(media.download_ticket);
  const mediaHead = await readHead(`/v1/file/${encodeURIComponent(mediaState.file_ticket)}`);
  assertMediaHead(mediaHead, sample.mediaKind);

  process.stdout.write(`${sample.platform}: 下载图片 ${image.label}\n`);
  const imageState = await waitForGenericFile(image.download_ticket);
  const imageHead = await readHead(`/v1/file/${encodeURIComponent(imageState.file_ticket)}`);
  assertImageHead(imageHead);
  return {
    platform: sample.platform,
    ok: true,
    media: { label: media.label, filename: mediaState.filename, ...mediaHead },
    image: { label: image.label, filename: imageState.filename, ...imageHead },
  };
}

function qualityNumber(format) {
  return videoHeight({ id: String(format.id || ""), label: String(format.quality || ""), kind: "视频" });
}

async function waitForCoreTask(taskId) {
  const deadline = Date.now() + 12 * 60_000;
  while (Date.now() < deadline) {
    const payload = await jsonRequest(`/v1/core/tasks/${taskId}`, {}, 0);
    const task = payload.task;
    if (task?.status === "completed") return task;
    if (["failed", "cancelled"].includes(task?.status)) throw new Error(task.errorMessage || "国内平台下载失败");
    await sleep(900);
  }
  throw new Error("国内平台下载验证超时");
}

async function validateCore(sample) {
  const resolved = await jsonRequest("/v1/core/resolve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: sample.url }),
  });
  const videos = (resolved.meta?.formats || []).filter((format) => format.ext === "mp4");
  const video = videos.sort((left, right) => qualityMode === "highest"
    ? qualityNumber(right) - qualityNumber(left)
    : (qualityNumber(left) || Infinity) - (qualityNumber(right) || Infinity))[0];
  const image = (resolved.meta?.formats || []).find((format) => ["image", "frame"].includes(format.kind));
  if (!video || !image) throw new Error("国内平台没有返回完整的视频与图片格式");

  process.stdout.write(`${sample.platform}: 下载视频 ${video.quality}\n`);
  const created = await jsonRequest("/v1/core/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: sample.url, quality: video.quality, format: "mp4" }),
  });
  const task = await waitForCoreTask(created.task.id);
  const mediaHead = await readHead(`/v1/core/file/${task.id}${image.kind === "image" ? "?consume=true" : ""}`);
  assertMediaHead(mediaHead, "video");

  process.stdout.write(`${sample.platform}: 下载图片 ${image.quality}\n`);
  let imageHead;
  let imageFilename;
  if (image.kind === "image" && image.download_ticket) {
    const imageState = await waitForGenericFile(image.download_ticket);
    imageHead = await readHead(`/v1/file/${encodeURIComponent(imageState.file_ticket)}`);
    imageFilename = imageState.filename;
  } else {
    imageHead = await readHead(`/v1/core/frame/${task.id}?consume=true`);
    imageFilename = `${task.title || sample.platform}-first-frame.jpg`;
  }
  assertImageHead(imageHead);
  return {
    platform: sample.platform,
    ok: true,
    media: { label: video.quality, filename: task.fileName, ...mediaHead },
    image: { label: image.quality, filename: imageFilename, ...imageHead },
  };
}

const startedAt = new Date().toISOString();
const results = [];
for (const sample of selectedGenericSamples) {
  try {
    results.push(await validateGeneric(sample));
    process.stdout.write(`${sample.platform}: 通过\n`);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    results.push({ platform: sample.platform, ok: false, error: message });
    process.stdout.write(`${sample.platform}: 失败 — ${message}\n`);
  }
}
for (const sample of selectedCoreSamples) {
  try {
    results.push(await validateCore(sample));
    process.stdout.write(`${sample.platform}: 通过\n`);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    results.push({ platform: sample.platform, ok: false, error: message });
    process.stdout.write(`${sample.platform}: 失败 — ${message}\n`);
  }
}

await fs.mkdir(path.dirname(resultPath), { recursive: true });
const passed = results.filter((result) => result.ok).length;
const summary = {
  mode: requestedPlatforms.size ? "subset" : "full",
  qualityMode,
  requestedPlatforms: [...requestedPlatforms],
  startedAt,
  completedAt: new Date().toISOString(),
  total: results.length,
  passed,
  failed: results.length - passed,
  results,
};
await fs.writeFile(resultPath, JSON.stringify(summary, null, 2));
process.stdout.write(`${requestedPlatforms.size ? "指定平台验收" : "完整验收"}：${passed}/${results.length} 个平台通过，结果已保存到 ${resultPath}\n`);
if (passed !== results.length) process.exitCode = 1;
