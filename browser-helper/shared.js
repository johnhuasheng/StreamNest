const TARGETS = [
  { platform: "抖音", hosts: ["douyin.com", "iesdouyin.com"] },
  { platform: "快手", hosts: ["kuaishou.com", "gifshow.com"] },
];

const MEDIA_HOSTS = {
  "抖音": ["douyin.com", "douyinvod.com", "douyinpic.com", "byteimg.com", "snssdk.com"],
  "快手": ["kuaishou.com", "gifshow.com", "kwaicdn.com", "kwimgs.com", "yximgs.com"],
};

function hostMatches(host, suffix) {
  return host === suffix || host.endsWith(`.${suffix}`);
}

export function selectBilibiliCookies(cookies) {
  return (cookies ?? []).filter((cookie) => {
    const domain = typeof cookie?.domain === "string" ? cookie.domain.toLowerCase().replace(/^\./, "") : "";
    return hostMatches(domain, "bilibili.com");
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

export function parseSupportedTarget(value) {
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    return null;
  }
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return null;
  const host = parsed.hostname.toLowerCase().replace(/^www\./, "");
  const target = TARGETS.find((item) => item.hosts.some((allowed) => hostMatches(host, allowed)));
  return target ? { platform: target.platform, url: parsed.toString() } : null;
}

function headerValue(headers, name) {
  const header = (headers ?? []).find((item) => item.name?.toLowerCase() === name);
  return header?.value ?? "";
}

const FORWARDED_HEADERS = new Set([
  "accept",
  "accept-language",
  "origin",
  "referer",
  "sec-ch-ua",
  "sec-ch-ua-mobile",
  "sec-ch-ua-platform",
  "sec-fetch-dest",
  "sec-fetch-mode",
  "sec-fetch-site",
  "user-agent",
]);

export function sanitizeRequestHeaders(headers) {
  const safe = {};
  for (const header of headers ?? []) {
    const name = String(header.name ?? "").toLowerCase();
    const value = String(header.value ?? "").trim();
    if (!FORWARDED_HEADERS.has(name) || !value || value.length > 1024) continue;
    safe[name] = value;
  }
  return safe;
}

function inferredQuality(url) {
  const values = [...url.matchAll(/(?:^|[^0-9])(2160|1920|1440|1280|1080|960|720|540|480|360)(?:[^0-9]|$)/g)]
    .map((match) => Number(match[1]));
  return values.length ? Math.max(...values) : 0;
}

export function mediaCandidate(details, platform, requestHeaders = []) {
  let parsed;
  try {
    parsed = new URL(details.url);
  } catch {
    return null;
  }
  if (parsed.protocol !== "https:") return null;
  const allowedHosts = MEDIA_HOSTS[platform] ?? [];
  if (!allowedHosts.some((allowed) => hostMatches(parsed.hostname.toLowerCase(), allowed))) return null;

  const contentType = headerValue(details.responseHeaders, "content-type").toLowerCase();
  if (![200, 206].includes(Number(details.statusCode))) return null;
  // Query strings such as `mime_type=video_mp4` can still return an HTML
  // permission page. Only trust the server's actual response media type.
  if (!contentType.startsWith("video/mp4")) return null;

  const contentLength = Number(headerValue(details.responseHeaders, "content-length")) || 0;
  const quality = inferredQuality(details.url);
  return {
    url: details.url,
    contentLength,
    contentType: contentType || "video/mp4",
    headers: sanitizeRequestHeaders(requestHeaders),
    quality,
    score: contentLength + quality * 1_000_000,
  };
}

export function chooseBestCandidate(candidates) {
  return [...candidates].sort((left, right) => right.score - left.score)[0] ?? null;
}

export function safeDownloadName(platform, taskId) {
  const safeId = String(taskId).replace(/[^A-Za-z0-9_-]/g, "").slice(0, 48) || Date.now().toString();
  const prefix = platform === "抖音" ? "douyin" : "kuaishou";
  return `StreamNest/${prefix}-${safeId}.mp4`;
}
