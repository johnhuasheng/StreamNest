const HELPER_SOURCE = "streamnest-helper";
const MANAGER_SOURCE = "streamnest-manager";

function announce() {
  chrome.runtime.sendMessage({ source: MANAGER_SOURCE, type: "STREAMNEST_BILI_CAPABILITIES" }, (response) => {
    const runtimeError = chrome.runtime.lastError;
    window.postMessage({
      source: HELPER_SOURCE,
      type: "STREAMNEST_HELPER_READY",
      version: chrome.runtime.getManifest().version,
      biliSyncSupported: !runtimeError && response?.ok === true && response?.biliSyncSupported === true,
    }, window.location.origin);
  });
}

window.addEventListener("message", (event) => {
  if (event.source !== window || event.origin !== window.location.origin) return;
  const message = event.data;
  if (!message || message.source !== MANAGER_SOURCE) return;
  if (message.type === "STREAMNEST_HELPER_PING") {
    announce();
    return;
  }
  if (!["STREAMNEST_HELPER_START", "STREAMNEST_HELPER_CANCEL", "STREAMNEST_BILI_SYNC"].includes(message.type)) return;
  if (message.type === "STREAMNEST_BILI_SYNC") {
    window.postMessage({ source: HELPER_SOURCE, type: "STREAMNEST_BILI_SYNC_ACK" }, window.location.origin);
  }
  let completed = false;
  const timeout = message.type === "STREAMNEST_BILI_SYNC" ? setTimeout(() => {
    if (completed) return;
    completed = true;
    window.postMessage({
      source: HELPER_SOURCE,
      type: "STREAMNEST_BILI_SYNC_ERROR",
      message: "浏览器助手已收到请求，但同步超时。请确认本机解析服务在线，并重新加载助手。",
    }, window.location.origin);
  }, 8500) : null;
  chrome.runtime.sendMessage(message, (response) => {
    if (completed) return;
    completed = true;
    if (timeout !== null) clearTimeout(timeout);
    const runtimeError = chrome.runtime.lastError;
    if (message.type === "STREAMNEST_BILI_SYNC") {
      window.postMessage({
        source: HELPER_SOURCE,
        type: response?.ok === true && !runtimeError ? "STREAMNEST_BILI_SYNCED" : "STREAMNEST_BILI_SYNC_ERROR",
        message: response?.message || (runtimeError ? "浏览器助手尚未重新加载，请在 Edge 扩展管理页点“重新加载”。" : "B 站会话同步失败。"),
      }, window.location.origin);
      return;
    }
    if (!runtimeError && response?.ok === true) return;
    window.postMessage({
      source: HELPER_SOURCE,
      type: "STREAMNEST_HELPER_ERROR",
      taskId: message.taskId,
      message: response?.message || (runtimeError ? "浏览器助手已更新但尚未重新加载，请在 Edge 扩展管理页点“重新加载”。" : "浏览器助手无法处理此任务。"),
    }, window.location.origin);
  });
});

chrome.runtime.onMessage.addListener((message) => {
  if (message?.source !== HELPER_SOURCE) return;
  window.postMessage(message, window.location.origin);
});

announce();
