import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { runInNewContext } from "node:vm";

import {
  chooseBestCandidate,
  mediaCandidate,
  parseSupportedTarget,
  sanitizeRequestHeaders,
  safeDownloadName,
  selectBilibiliCookies,
} from "../browser-helper/shared.js";
import { syncBilibiliSession } from "../browser-helper/bilibili-sync.js";

test("browser helper only accepts Douyin and Kuaishou targets", () => {
  assert.equal(parseSupportedTarget("https://v.douyin.com/example/").platform, "抖音");
  assert.equal(parseSupportedTarget("https://www.kuaishou.com/short-video/example").platform, "快手");
  assert.equal(parseSupportedTarget("https://douyin.com.evil.example/video"), null);
  assert.equal(parseSupportedTarget("https://example.com/video"), null);
});

test("browser helper only captures allowlisted MP4 media", () => {
  const candidate = mediaCandidate({
    url: "https://v3.douyinvod.com/video/tos/example.mp4?quality=1080",
    statusCode: 206,
    responseHeaders: [
      { name: "content-type", value: "video/mp4" },
      { name: "content-length", value: "1234567" },
    ],
  }, "抖音");
  assert.equal(candidate.contentLength, 1234567);
  assert.equal(candidate.quality, 1080);
  assert.equal(mediaCandidate({
    url: "https://evil.example/video.mp4",
    statusCode: 200,
    responseHeaders: [{ name: "content-type", value: "video/mp4" }],
  }, "抖音"), null);
  assert.equal(mediaCandidate({
    url: "https://p3.douyinpic.com/image/example.jpg",
    statusCode: 200,
    responseHeaders: [{ name: "content-type", value: "image/jpeg" }],
  }, "抖音"), null);
  assert.equal(mediaCandidate({
    url: "https://www.douyin.com/download?mime_type=video_mp4",
    statusCode: 200,
    responseHeaders: [{ name: "content-type", value: "text/html" }],
  }, "抖音"), null);
});

test("browser helper never forwards cookies or authorization", () => {
  assert.deepEqual(sanitizeRequestHeaders([
    { name: "Referer", value: "https://www.douyin.com/video/example" },
    { name: "User-Agent", value: "Test browser" },
    { name: "Cookie", value: "secret=1" },
    { name: "Authorization", value: "Bearer secret" },
    { name: "Range", value: "bytes=0-" },
  ]), {
    referer: "https://www.douyin.com/video/example",
    "user-agent": "Test browser",
  });
});

test("member sync sends only Bilibili-domain cookies", () => {
  const selected = selectBilibiliCookies([
    { domain: ".bilibili.com", name: "SESSDATA", value: "test-only", path: "/", secure: true },
    { domain: "api.bilibili.com", name: "buvid3", value: "test", path: "/" },
    { domain: "bilibili.com.evil.example", name: "LEAK", value: "secret" },
    { domain: ".youtube.com", name: "LEAK", value: "secret" },
  ]);
  assert.deepEqual(selected.map((item) => item.name), ["SESSDATA", "buvid3"]);
  assert.equal(JSON.stringify(selected).includes("LEAK"), false);
});

test("content bridge forwards explicit Bilibili sync requests", () => {
  const source = readFileSync(new URL("../browser-helper/content.js", import.meta.url), "utf8");
  assert.match(source, /"STREAMNEST_BILI_SYNC"/);
  assert.match(source, /"STREAMNEST_BILI_SYNC_ACK"/);
  assert.match(source, /"STREAMNEST_BILI_SYNC_ERROR"/);
});

test("Bilibili session bridge returns a status without exposing cookies to the page", async () => {
  const priorChrome = globalThis.chrome;
  const priorFetch = globalThis.fetch;
  let onMessage;
  let posted = false;
  globalThis.chrome = {
    runtime: {
      id: "a".repeat(32),
      getURL: (path) => `chrome-extension://${"a".repeat(32)}/${path}`,
      onMessage: { addListener: (listener) => { onMessage = listener; } },
      lastError: null,
    },
    webRequest: {
      onBeforeSendHeaders: { addListener: () => {} },
      onHeadersReceived: { addListener: () => {} },
    },
    tabs: {
      onUpdated: { addListener: () => {} },
      onRemoved: { addListener: () => {} },
    },
    cookies: { getAll: (_query, callback) => callback([
      { domain: ".bilibili.com", name: "SESSDATA", value: "synthetic-secret", path: "/", secure: true },
      { domain: ".example.com", name: "LEAK", value: "other-secret", path: "/" },
    ]) },
  };
  globalThis.fetch = async (url, options) => {
    assert.equal(url, "http://127.0.0.1:8788/v1/bilibili/session");
    assert.equal(options.headers["X-StreamNest-Bridge"], "bilibili-v1");
    assert.deepEqual(JSON.parse(options.body).cookies.map((item) => item.name), ["SESSDATA"]);
    posted = true;
    return { ok: true };
  };
  try {
    await import(new URL("../browser-helper/background.js?bili-bridge-test", import.meta.url));
    let capabilities;
    onMessage(
      { source: "streamnest-manager", type: "STREAMNEST_BILI_CAPABILITIES" },
      { tab: { id: 7, url: "http://localhost:3000/" } },
      (result) => { capabilities = result; },
    );
    assert.deepEqual(capabilities, { ok: true, biliSyncSupported: true });
    const response = await new Promise((resolve) => {
      const keepOpen = onMessage(
        { source: "streamnest-manager", type: "STREAMNEST_BILI_SYNC" },
        { tab: { id: 7, url: "http://localhost:3000/" } },
        resolve,
      );
      assert.equal(keepOpen, true);
    });
    assert.equal(posted, true);
    assert.equal(response.ok, true);
    assert.equal(JSON.stringify(response).includes("synthetic-secret"), false);
    let rejected;
    onMessage(
      { source: "streamnest-manager", type: "STREAMNEST_BILI_SYNC" },
      { tab: { id: 8, url: "https://example.com/" } },
      (result) => { rejected = result; },
    );
    assert.equal(rejected.ok, false);
  } finally {
    globalThis.chrome = priorChrome;
    globalThis.fetch = priorFetch;
  }
});

test("popup sync runs directly, reports stages, and never returns cookie values", async () => {
  const stages = [];
  const result = await syncBilibiliSession({
    chromeApi: {
      runtime: { lastError: null },
      cookies: { getAll: (_query, callback) => callback([
        { domain: ".bilibili.com", name: "SESSDATA", value: "synthetic-secret", path: "/" },
        { domain: ".example.com", name: "LEAK", value: "other-secret", path: "/" },
      ]) },
    },
    fetcher: async (_url, options) => {
      assert.deepEqual(JSON.parse(options.body).cookies.map((item) => item.name), ["SESSDATA"]);
      return { ok: true };
    },
    onStage: (stage) => stages.push(stage),
  });
  assert.equal(result.ok, true);
  assert.equal(stages.length, 2);
  assert.equal(JSON.stringify({ result, stages }).includes("synthetic-secret"), false);
});

test("popup sync times out instead of leaving the button disabled forever", async () => {
  const result = await syncBilibiliSession({
    chromeApi: {
      runtime: { lastError: null },
      cookies: { getAll: () => {} },
    },
    fetcher: () => { throw new Error("must not request local service"); },
    cookieTimeoutMs: 10,
  });
  assert.equal(result.ok, false);
  assert.match(result.message, /超时/);
});

test("classic popup script registers a working click and never displays cookie values", async () => {
  const html = readFileSync(new URL("../browser-helper/popup.html", import.meta.url), "utf8");
  const script = readFileSync(new URL("../browser-helper/popup.js", import.meta.url), "utf8");
  assert.match(html, /<script src="popup\.js"><\/script>/);
  assert.match(html, /href="http:\/\/localhost:3000\/"/);
  let click;
  const button = { disabled: false, addEventListener: (type, handler) => {
    assert.equal(type, "click");
    click = handler;
  } };
  const status = { textContent: "助手脚本未启动。" };
  runInNewContext(script, {
    document: { getElementById: (id) => id === "sync-bilibili" ? button : status },
    chrome: {
      runtime: { getManifest: () => ({ version: "0.3.6" }), lastError: null },
      cookies: { getAll: (_query, callback) => callback([
        { domain: ".bilibili.com", name: "SESSDATA", value: "synthetic-secret", path: "/" },
        { domain: ".example.com", name: "LEAK", value: "other-secret", path: "/" },
      ]) },
    },
    fetch: async (url, options) => {
      assert.equal(url, "http://127.0.0.1:8788/v1/bilibili/session");
      assert.deepEqual(JSON.parse(options.body).cookies.map((item) => item.name), ["SESSDATA"]);
      return { ok: true };
    },
    AbortSignal,
    setTimeout,
    clearTimeout,
  });
  assert.match(status.textContent, /0\.3\.6.*已就绪/);
  assert.equal(typeof click, "function");
  await click();
  assert.equal(button.disabled, false);
  assert.match(status.textContent, /已仅在本机同步/);
  assert.equal(status.textContent.includes("synthetic-secret"), false);
});

test("popup explains when opened as a normal file instead of an installed extension", () => {
  const script = readFileSync(new URL("../browser-helper/popup.js", import.meta.url), "utf8");
  const button = { disabled: false, addEventListener: () => { throw new Error("must not enable sync"); } };
  const status = { textContent: "助手脚本未启动。" };
  runInNewContext(script, {
    document: { getElementById: (id) => id === "sync-bilibili" ? button : status },
    setTimeout,
    clearTimeout,
  });
  assert.equal(button.disabled, true);
  assert.match(status.textContent, /不要直接打开 popup\.html/);
});

test("browser helper chooses the strongest candidate and sanitizes filenames", () => {
  const best = chooseBestCandidate([
    { url: "low", score: 360_000_000 },
    { url: "high", score: 1080_000_000 },
  ]);
  assert.equal(best.url, "high");
  assert.equal(safeDownloadName("快手", "task/../../unsafe"), "StreamNest/kuaishou-taskunsafe.mp4");
});
