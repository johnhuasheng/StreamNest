import { selectBilibiliCookies } from "./shared.js";

function readBilibiliCookies(chromeApi, timeoutMs) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      reject(new Error("cookie_timeout"));
    }, timeoutMs);
    const finish = (error, cookies) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) reject(error);
      else resolve(cookies ?? []);
    };
    try {
      // Extension host permissions restrict this request to Bilibili domains.
      chromeApi.cookies.getAll({ domain: "bilibili.com" }, (cookies) => {
        const error = chromeApi.runtime.lastError;
        finish(error ? new Error("cookie_permission") : null, cookies);
      });
    } catch {
      finish(new Error("cookie_permission"));
    }
  });
}

export async function syncBilibiliSession({
  chromeApi = globalThis.chrome,
  fetcher = globalThis.fetch,
  onStage = () => {},
  cookieTimeoutMs = 5000,
  requestTimeoutMs = 7000,
} = {}) {
  let cookies;
  try {
    onStage("正在读取当前 Edge 配置中的 B 站会话…");
    cookies = selectBilibiliCookies(await readBilibiliCookies(chromeApi, cookieTimeoutMs));
  } catch (error) {
    return {
      ok: false,
      message: error?.message === "cookie_timeout"
        ? "读取 B 站会话超时。请检查扩展的 B 站网站访问权限，然后重新加载助手。"
        : "无法读取 B 站会话。请检查扩展的 B 站网站访问权限，然后重新加载助手。",
    };
  }
  if (!cookies.some((cookie) => cookie.name === "SESSDATA" && cookie.value)) {
    return { ok: false, message: "当前 Edge 配置中没有找到 B 站登录会话。请确认在同一配置中已登录 B 站。" };
  }
  try {
    onStage("已读取 B 站会话，正在连接本机 StreamNest 服务…");
    const response = await fetcher("http://127.0.0.1:8788/v1/bilibili/session", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-StreamNest-Bridge": "bilibili-v1",
      },
      body: JSON.stringify({ cookies }),
      cache: "no-store",
      credentials: "omit",
      signal: AbortSignal.timeout(requestTimeoutMs),
    });
    if (!response.ok) {
      return { ok: false, message: "本机 B 站会话同步失败。请确认解析服务已启动，并重新加载浏览器助手。" };
    }
    return { ok: true, message: "已仅在本机同步 B 站会话；请重新解析这集视频以查看可用画质。" };
  } catch {
    return { ok: false, message: "连接本机解析服务超时或失败。请确认 8788 服务已启动，再重试。" };
  }
}
