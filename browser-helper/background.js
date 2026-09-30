import {
  chooseBestCandidate,
  mediaCandidate,
  parseSupportedTarget,
} from "./shared.js";
import { syncBilibiliSession } from "./bilibili-sync.js";

const sessions = new Map();
const targetTabs = new Map();
const requestHeaders = new Map();

function isLocalManagerTab(tab) {
  try {
    const url = new URL(tab?.url ?? "");
    return url.protocol === "http:" && url.port === "3000"
      && ["localhost", "127.0.0.1"].includes(url.hostname);
  } catch {
    return false;
  }
}

function send(session, type, detail = {}) {
  chrome.tabs.sendMessage(session.managerTabId, {
    source: "streamnest-helper",
    type,
    taskId: session.taskId,
    ...detail,
  }).catch(() => undefined);
}

function cleanup(session, { closeTab = false } = {}) {
  sessions.delete(session.taskId);
  if (session.targetTabId !== undefined) {
    targetTabs.delete(session.targetTabId);
    if (closeTab) chrome.tabs.remove(session.targetTabId).catch(() => undefined);
  }
  if (session.captureTimer) clearTimeout(session.captureTimer);
  if (session.failureTimer) clearTimeout(session.failureTimer);
}

function deliverCandidate(session) {
  if (session.state !== "capturing") return;
  const candidate = chooseBestCandidate(session.candidates.values());
  if (!candidate) return;
  session.state = "captured";
  send(session, "STREAMNEST_HELPER_CAPTURED", {
    platform: session.platform,
    quality: candidate.quality || null,
    totalBytes: candidate.contentLength || null,
    mediaUrl: candidate.url,
    headers: candidate.headers,
  });
  setTimeout(() => cleanup(session, { closeTab: true }), 500);
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message?.source !== "streamnest-manager" || !sender.tab?.id) return false;

  if (message.type === "STREAMNEST_BILI_CAPABILITIES") {
    respond({ ok: isLocalManagerTab(sender.tab), biliSyncSupported: isLocalManagerTab(sender.tab) });
    return false;
  }

  if (message.type === "STREAMNEST_BILI_SYNC") {
    if (!isLocalManagerTab(sender.tab)) {
      respond({ ok: false, message: "会员会话只允许在本机 StreamNest 网页同步。" });
      return false;
    }
    void syncBilibiliSession().then(respond);
    return true;
  }

  if (message.type === "STREAMNEST_HELPER_START") {
    const target = parseSupportedTarget(message.url);
    const taskId = String(message.taskId ?? "");
    if (!target || !taskId || sessions.has(taskId)) {
      respond({ ok: false, message: "只支持有效的抖音或快手公开视频链接。" });
      return false;
    }
    const session = {
      taskId,
      managerTabId: sender.tab.id,
      platform: target.platform,
      targetUrl: target.url,
      state: "opening",
      candidates: new Map(),
    };
    sessions.set(taskId, session);
    chrome.tabs.create({ url: target.url, active: false }, (tab) => {
      const error = chrome.runtime.lastError;
      if (error || tab.id === undefined) {
        send(session, "STREAMNEST_HELPER_ERROR", {
          message: `无法打开平台页面：${error?.message ?? "未知错误"}`,
        });
        cleanup(session);
        return;
      }
      session.targetTabId = tab.id;
      session.state = "capturing";
      targetTabs.set(tab.id, taskId);
      session.failureTimer = setTimeout(() => {
        if (session.state !== "capturing") return;
        send(session, "STREAMNEST_HELPER_ERROR", {
          message: `未捕获到${session.platform}返回的真实 MP4。请刷新平台链接后重试。`,
        });
        cleanup(session, { closeTab: true });
      }, 35_000);
      send(session, "STREAMNEST_HELPER_WAITING", {
        platform: session.platform,
        message: `正在后台读取${session.platform}公开视频，捕获后会直接下载。`,
      });
    });
    respond({ ok: true });
    return false;
  }

  if (message.type === "STREAMNEST_HELPER_CANCEL") {
    const session = sessions.get(String(message.taskId ?? ""));
    if (session) cleanup(session, { closeTab: true });
    respond({ ok: true });
    return false;
  }
  return false;
});

chrome.webRequest.onBeforeSendHeaders.addListener(
  (details) => {
    if (!targetTabs.has(details.tabId)) return;
    requestHeaders.set(details.requestId, details.requestHeaders ?? []);
    setTimeout(() => requestHeaders.delete(details.requestId), 60_000);
  },
  {
    urls: [
      "*://*.douyin.com/*",
      "*://*.douyinvod.com/*",
      "*://*.douyinpic.com/*",
      "*://*.byteimg.com/*",
      "*://*.snssdk.com/*",
      "*://*.kuaishou.com/*",
      "*://*.gifshow.com/*",
      "*://*.kwaicdn.com/*",
      "*://*.kwimgs.com/*",
      "*://*.yximgs.com/*"
    ],
    types: ["media", "xmlhttprequest", "other"]
  },
  ["requestHeaders", "extraHeaders"]
);

chrome.webRequest.onHeadersReceived.addListener(
  (details) => {
    const taskId = targetTabs.get(details.tabId);
    const session = taskId ? sessions.get(taskId) : null;
    if (!session || session.state !== "capturing") return;
    const capturedHeaders = requestHeaders.get(details.requestId) ?? [];
    requestHeaders.delete(details.requestId);
    const candidate = mediaCandidate(details, session.platform, capturedHeaders);
    if (!candidate) return;
    session.candidates.set(candidate.url, candidate);
    if (session.captureTimer) clearTimeout(session.captureTimer);
    session.captureTimer = setTimeout(() => deliverCandidate(session), 1200);
  },
  {
    urls: [
      "*://*.douyin.com/*",
      "*://*.douyinvod.com/*",
      "*://*.douyinpic.com/*",
      "*://*.byteimg.com/*",
      "*://*.snssdk.com/*",
      "*://*.kuaishou.com/*",
      "*://*.gifshow.com/*",
      "*://*.kwaicdn.com/*",
      "*://*.kwimgs.com/*",
      "*://*.yximgs.com/*"
    ],
    types: ["media", "xmlhttprequest", "other"]
  },
  ["responseHeaders"]
);

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status !== "complete" || !targetTabs.has(tabId)) return;
  chrome.tabs.sendMessage(tabId, {
    source: "streamnest-helper",
    type: "STREAMNEST_HELPER_PLAY",
  }).catch(() => undefined);
});

chrome.tabs.onRemoved.addListener((tabId) => {
  const taskId = targetTabs.get(tabId);
  const session = taskId ? sessions.get(taskId) : null;
  if (!session) return;
  send(session, "STREAMNEST_HELPER_ERROR", {
    message: "后台平台页面提前关闭，请重新提交链接。",
  });
  cleanup(session);
});
