// Keep the popup self-contained: a failed module import must not leave both
// buttons looking clickable while no handlers were registered.
const syncButton = document.getElementById("sync-bilibili");
const syncStatus = document.getElementById("sync-status");
const extensionApi = globalThis.chrome;
const extensionReady = typeof extensionApi?.runtime?.getManifest === "function"
  && typeof extensionApi?.cookies?.getAll === "function";

syncStatus.textContent = extensionReady
  ? `助手版本 ${extensionApi.runtime.getManifest().version}，已就绪。`
  : "当前不是已安装的助手扩展页面。请从 Edge 工具栏打开 StreamNest 助手，不要直接打开 popup.html 文件。";
syncButton.disabled = !extensionReady;

function readBilibiliCookies() {
  return new Promise((resolve, reject) => {
    let finished = false;
    const timer = setTimeout(() => {
      if (finished) return;
      finished = true;
      reject(new Error("cookie_timeout"));
    }, 5000);
    const finish = (error, result) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      if (error) reject(error);
      else resolve(result ?? []);
    };
    try {
      extensionApi.cookies.getAll({ domain: "bilibili.com" }, (cookies) => {
        finish(extensionApi.runtime.lastError ? new Error("cookie_permission") : null, cookies);
      });
    } catch {
      finish(new Error("cookie_permission"));
    }
  });
}

function onlyBilibiliCookies(cookies) {
  return cookies.filter((cookie) => {
    const domain = typeof cookie?.domain === "string"
      ? cookie.domain.toLowerCase().replace(/^\./, "")
      : "";
    return domain === "bilibili.com" || domain.endsWith(".bilibili.com");
  }).slice(0, 128).map((cookie) => ({
    domain: cookie.domain,
    name: cookie.name,
    value: cookie.value,
    path: cookie.path,
    secure: cookie.secure,
    httpOnly: cookie.httpOnly,
    expirationDate: cookie.expirationDate,
  }));
}

if (extensionReady) syncButton.addEventListener("click", async () => {
  syncButton.disabled = true;
  let cookies;
  try {
    syncStatus.textContent = "正在读取当前 Edge 配置中的 B 站会话…";
    cookies = onlyBilibiliCookies(await readBilibiliCookies());
    if (!cookies.some((cookie) => cookie.name === "SESSDATA" && cookie.value)) {
      syncStatus.textContent = "当前 Edge 配置中没有找到 B 站登录会话。请确认在同一配置中已登录 B 站。";
      return;
    }
    syncStatus.textContent = "已读取 B 站会话，正在连接本机 StreamNest 服务…";
    const response = await fetch("http://127.0.0.1:8788/v1/bilibili/session", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-StreamNest-Bridge": "bilibili-v1",
      },
      body: JSON.stringify({ cookies }),
      cache: "no-store",
      credentials: "omit",
      signal: AbortSignal.timeout(7000),
    });
    syncStatus.textContent = response.ok
      ? "已仅在本机同步 B 站会话；请重新解析这集视频以查看可用画质。"
      : "本机 B 站会话同步失败。请确认解析服务已启动，并重新加载浏览器助手。";
  } catch (error) {
    syncStatus.textContent = error?.message === "cookie_timeout"
      ? "读取 B 站会话超时。请检查扩展的 B 站网站访问权限，然后重新加载助手。"
      : error?.message === "cookie_permission"
        ? "无法读取 B 站会话。请检查扩展的 B 站网站访问权限，然后重新加载助手。"
        : "连接本机解析服务超时或失败。请确认 8788 服务已启动，再重试。";
  } finally {
    syncButton.disabled = false;
    cookies = null;
  }
});
