"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { maxVideoHeight, preferredFormatId, sortFormatsForDisplay, videoHeight } from "./quality";
import haijiaoDomains from "../haijiao-domains.json";

type Platform = {
  name: string;
  short: string;
  tone: string;
  hosts: string[];
  adult?: boolean;
  limitedReason?: string;
};

type FormatOption = {
  id: string;
  label: string;
  detail: string;
  size: string;
  kind: string;
  download_ticket: string;
  coreQuality?: string;
  coreFormat?: string;
  imageIndex?: number;
  imageCount?: number;
  imagePreview?: string;
  imageCollection?: boolean;
};

type TaskStatus = "analyzing" | "preparing" | "prepared" | "ready" | "sent" | "failed" | "expired";
type TaskFilter = "all" | "ready" | "sent" | "failed";

type DownloadTask = {
  id: string;
  url: string;
  platform: Platform;
  title: string;
  author: string;
  duration: string;
  thumbnail: string | null;
  formats: FormatOption[];
  selectedFormat: string;
  status: TaskStatus;
  createdAt: number;
  updatedAt: number;
  ticketCreatedAt?: number;
  adultConfirmed: boolean;
  helperAssisted?: boolean;
  coreAssisted?: boolean;
  selectedImageIds?: string[];
  error?: string;
};

type LocalCoreFormat = {
  id: string;
  quality: string;
  ext: string;
  size: number | null;
  note?: string;
  kind?: "video" | "image" | "image_collection" | "frame";
  download_ticket?: string;
  source_quality?: string;
  image_index?: number;
  image_count?: number;
};

type LocalCoreResolveResponse = {
  ok: boolean;
  meta: {
    platform: "douyin" | "kuaishou";
    title: string;
    author: string | null;
    thumbnail: string | null;
    images?: string[];
    duration: number | null;
    formats: LocalCoreFormat[];
  };
};

type LocalCoreTask = {
  id: number;
  status: "parsing" | "waiting" | "downloading" | "paused" | "completed" | "failed" | "cancelled";
  title: string;
  expectedSize: number | null;
  downloadedBytes: number;
  progress: number | null;
  speed: number;
  eta: number | null;
  errorMessage: string | null;
};

type HelperMessage = {
  source?: string;
  type?: string;
  taskId?: string;
  version?: string;
  biliSyncSupported?: boolean;
  platform?: string;
  message?: string;
  filename?: string;
  quality?: number | null;
  progress?: number;
  downloadedBytes?: number;
  totalBytes?: number | null;
  speed?: number | null;
  eta?: number | null;
  mediaUrl?: string;
  headers?: Record<string, string>;
};

type ResolveResponse = {
  title: string;
  author: string;
  duration: string;
  duration_seconds: number | null;
  thumbnail: string | null;
  platform: string;
  formats: FormatOption[];
};

type DownloadProgress = {
  status: "queued" | "downloading" | "ready" | "failed";
  progress: number;
  downloaded_bytes: number;
  total_bytes: number | null;
  speed: number | null;
  eta: number | null;
};

type DownloadJobResponse = DownloadProgress & {
  job_id: string;
  file_ticket: string | null;
  filename: string | null;
  error: string | null;
};

const platforms: Platform[] = [
  {
    name: "Bilibili",
    short: "B",
    tone: "pink",
    hosts: ["bilibili.com", "b23.tv"],
    limitedReason: "默认按访客身份解析；会员画质可在本机主动同步 Edge 中的 B 站会话。不会要求输入账号或密码，也不支持 DRM 内容。",
  },
  { name: "抖音", short: "抖", tone: "ink", hosts: ["douyin.com", "iesdouyin.com"] },
  { name: "小红书", short: "红", tone: "red", hosts: ["xiaohongshu.com", "xhslink.com"] },
  { name: "红果短剧", short: "果", tone: "orange", hosts: ["hongguoduanju.com"] },
  { name: "快手", short: "快", tone: "orange", hosts: ["kuaishou.com", "gifshow.com"] },
  { name: "YouTube", short: "Y", tone: "youtube", hosts: ["youtube.com", "youtu.be"] },
  { name: "TikTok", short: "T", tone: "ink", hosts: ["tiktok.com"] },
  { name: "X", short: "X", tone: "ink", hosts: ["x.com", "twitter.com"] },
  { name: "Facebook", short: "f", tone: "blue", hosts: ["facebook.com", "fb.watch"] },
  { name: "TED", short: "T", tone: "ted", hosts: ["ted.com"] },
  { name: "Instagram", short: "I", tone: "violet", hosts: ["instagram.com"] },
  { name: "Vimeo", short: "V", tone: "cyan", hosts: ["vimeo.com"] },
  { name: "Dailymotion", short: "D", tone: "navy", hosts: ["dailymotion.com", "dai.ly"] },
  { name: "Pinterest", short: "P", tone: "pinterest", hosts: ["pinterest.com", "pin.it"] },
  { name: "微博", short: "微", tone: "weibo", hosts: ["weibo.com", "weibo.cn"] },
  { name: "西瓜视频", short: "西", tone: "watermelon", hosts: ["ixigua.com"] },
  { name: "AcFun", short: "A", tone: "acfun", hosts: ["acfun.cn"] },
  { name: "Twitch", short: "T", tone: "twitch", hosts: ["twitch.tv"] },
  { name: "SoundCloud", short: "S", tone: "soundcloud", hosts: ["soundcloud.com"] },
  { name: "Reddit", short: "R", tone: "reddit", hosts: ["reddit.com", "redd.it"] },
  { name: "Pornhub", short: "P", tone: "adult", hosts: ["pornhub.com"], adult: true },
  { name: "XVideos", short: "X", tone: "adult-red", hosts: ["xvideos.com"], adult: true },
  { name: "Eporner", short: "E", tone: "adult", hosts: ["eporner.com"], adult: true },
  { name: "XNXX", short: "XN", tone: "adult-red", hosts: ["xnxx.com", "xnxx3.com"], adult: true },
  { name: "Rule34Video", short: "R34", tone: "adult", hosts: ["rule34video.com"], adult: true },
  { name: "HQPorner", short: "HQ", tone: "adult-red", hosts: ["hqporner.com"], adult: true },
  { name: "Beeg", short: "B", tone: "adult", hosts: ["beeg.com"], adult: true },
  { name: "SxyPrn", short: "S", tone: "adult-red", hosts: ["sxyprn.com"], adult: true },
  {
    name: "SpankBang",
    short: "SB",
    tone: "adult",
    hosts: ["spankbang.com"],
    adult: true,
    limitedReason: "该站当前可能触发 Cloudflare 人机验证；链接格式正确时也可能暂时无法直接下载。",
  },
  { name: "XMoviesForYou", short: "XM", tone: "adult-red", hosts: ["xmoviesforyou.com"], adult: true },
  {
    name: "海角网",
    short: "海",
    tone: "adult-red",
    hosts: haijiaoDomains.hosts,
    adult: true,
    limitedReason: "仅识别清单中的线路域名；请粘贴 /archives/编号/ 的详情页。线路失效、跳转到未核验域名或媒体受限时不会强行下载。",
  },
];

const resolverApiUrl = (process.env.NEXT_PUBLIC_RESOLVER_API_URL ?? "").replace(/\/$/, "");
const storageKey = "streamnest-download-tasks-v2";
const ticketLifetime = 12 * 60 * 1000;

function extractVideoUrl(value: string) {
  const normalized = value.trim().replace(/\\([_*~])/g, "$1");
  if (!normalized) return "";

  const markdownTarget = normalized.match(/\]\(\s*(https?:\/\/[^)\s]+)/i)?.[1];
  const looseUrl = normalized.match(/https?:\/\/[^\s<>"'）】]+/i)?.[0];
  const candidate = (markdownTarget ?? looseUrl ?? normalized)
    .split(/[，。；！？”’、]/, 1)[0]
    .replace(/[)\]}>.,;!?]+$/g, "");

  try {
    const parsed = new URL(candidate);
    if (!/^https?:$/.test(parsed.protocol)) return "";

    const host = parsed.hostname.toLowerCase().replace(/^www\./, "");
    const layerId = parsed.searchParams.get("layerid");
    if (host === "weibo.com" && layerId && /^\d{8,24}$/.test(layerId)) {
      return `https://m.weibo.cn/status/${layerId}`;
    }
    return parsed.toString();
  } catch {
    return "";
  }
}

function detectPlatform(value: string) {
  try {
    const cleanUrl = extractVideoUrl(value);
    if (!cleanUrl) return null;
    const parsed = new URL(cleanUrl);
    if (!/^https?:$/.test(parsed.protocol)) return null;
    const host = parsed.hostname.toLowerCase().replace(/^www\./, "");
    return platforms.find((platform) =>
      platform.hosts.some((allowed) => host === allowed || host.endsWith(`.${allowed}`)),
    ) ?? null;
  } catch {
    return null;
  }
}

function formatDuration(seconds: number | null) {
  if (!seconds || seconds < 0) return "--:--";
  const total = Math.floor(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remaining = total % 60;
  return hours
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remaining).padStart(2, "0")}`
    : `${minutes}:${String(remaining).padStart(2, "0")}`;
}

function formatSpeed(bytesPerSecond: number | null) {
  if (!bytesPerSecond || bytesPerSecond <= 0) return "计算中";
  const units = ["B/s", "KB/s", "MB/s", "GB/s"];
  let amount = bytesPerSecond;
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024;
    index += 1;
  }
  return `${amount.toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
}

function formatFileSize(bytes: number | null) {
  if (!bytes || bytes <= 0) return "未知大小";
  const units = ["B", "KB", "MB", "GB"];
  let amount = bytes;
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024;
    index += 1;
  }
  return `${amount.toFixed(index >= 2 ? 1 : 0)} ${units[index]}`;
}

function formatEta(seconds: number | null) {
  if (seconds === null || seconds < 0) return "计算中";
  const total = Math.floor(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remaining = total % 60;
  return hours
    ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remaining).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(remaining).padStart(2, "0")}`;
}

function makeId() {
  return globalThis.crypto?.randomUUID?.() ?? `task-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function saveFileToOutputFolder(url: string, filename: string) {
  const response = await fetch(url, {
    method: "POST",
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  const payload = await response.json().catch(() => null) as {
    ok?: boolean;
    filename?: string;
    folder?: string;
  } | null;
  if (!response.ok) {
    throw new Error(readError(payload, response.status === 404
      ? "准备好的文件已经失效，请重新解析后再下载。"
      : "文件无法保存到“下载内容”文件夹，请重试。"));
  }
  if (!payload?.ok || !payload.filename) {
    throw new Error("服务没有确认文件保存结果，请重试。");
  }
  return {
    filename: payload.filename || filename,
    folder: payload.folder || "下载内容",
  };
}

function imageDownloadLabel(formats: FormatOption[]) {
  const collection = formats.find((format) => format.id === "image-all");
  return collection ? `▧ 下载${collection.label}` : "▧ 下载图片";
}

function restoreLegacyImageGallery(formats: FormatOption[], thumbnail: string | null) {
  let inferredCount = 0;
  const restored = formats.map((format) => {
    const idMatch = format.id.match(/^image-(\d+)$/i);
    const detailMatch = format.detail.match(/图文作品第\s*(\d+)\s*\/\s*(\d+)\s*张/);
    const imageIndex = (
      format.imageIndex ?? Number(idMatch?.[1] || detailMatch?.[1] || 0)
    ) || undefined;
    const imageCount = (
      format.imageCount ?? Number(detailMatch?.[2] || 0)
    ) || undefined;
    inferredCount = Math.max(inferredCount, imageIndex ?? 0, imageCount ?? 0);
    return {
      ...format,
      imageIndex,
      imageCount,
      imageCollection: format.imageCollection || format.id === "image-all",
      imagePreview: format.imagePreview ?? (imageIndex === 1 ? thumbnail ?? undefined : undefined),
    };
  });

  const galleryCount = Math.max(
    inferredCount,
    restored.filter((format) => format.imageIndex).length,
  );
  return restored.map((format) => (
    format.imageIndex && !format.imageCount
      ? { ...format, imageCount: galleryCount }
      : format
  ));
}

function readError(payload: unknown, fallback: string) {
  if (payload && typeof payload === "object" && "error" in payload) {
    const error = (payload as { error?: unknown }).error;
    if (error && typeof error === "object" && "message" in error) {
      const message = (error as { message?: unknown }).message;
      if (typeof message === "string") return message;
    }
  }
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && "message" in detail) {
      const message = (detail as { message?: unknown }).message;
      if (typeof message === "string") return message;
    }
  }
  return fallback;
}

function readErrorCode(payload: unknown) {
  if (!payload || typeof payload !== "object" || !("error" in payload)) return "";
  const error = (payload as { error?: unknown }).error;
  if (!error || typeof error !== "object" || !("code" in error)) return "";
  const code = (error as { code?: unknown }).code;
  return typeof code === "string" ? code : "";
}

const statusText: Record<TaskStatus, string> = {
  analyzing: "正在解析",
  preparing: "下载中",
  prepared: "文件就绪",
  ready: "等待下载",
  sent: "已下载",
  failed: "任务失败",
  expired: "链接已过期",
};

export default function Home() {
  const [url, setUrl] = useState("");
  const [adultConfirmed, setAdultConfirmed] = useState(false);
  const [tasks, setTasks] = useState<DownloadTask[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filter, setFilter] = useState<TaskFilter>("all");
  const [search, setSearch] = useState("");
  const [notice, setNotice] = useState("");
  const [serviceOnline, setServiceOnline] = useState<boolean | null>(null);
  const [coreOnline, setCoreOnline] = useState<boolean | null>(null);
  const [biliSessionActive, setBiliSessionActive] = useState(false);
  const [biliSyncing, setBiliSyncing] = useState(false);
  const [helperVersion, setHelperVersion] = useState<string | null>(null);
  const [helperBiliCapability, setHelperBiliCapability] = useState(false);
  const [preparedFiles, setPreparedFiles] = useState<Record<string, { url: string; filename: string }>>({});
  const [downloadProgress, setDownloadProgress] = useState<Record<string, DownloadProgress>>({});
  const activeJobs = useRef<Record<string, string>>({});
  const biliSyncPending = useRef(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const currentPlatform = useMemo(() => detectPlatform(url), [url]);
  const helperVersionParts = (helperVersion ?? "").split(".").map(Number);
  const helperVersionReady = helperVersionParts.length >= 3 && (
    helperVersionParts[0] > 0
    || (helperVersionParts[0] === 0 && helperVersionParts[1] > 3)
    || (helperVersionParts[0] === 0 && helperVersionParts[1] === 3 && helperVersionParts[2] >= 6)
  );
  const helperSupportsBili = helperBiliCapability && helperVersionReady;

  const saveFileDirectly = async (taskId: string, fileUrl: string, filename: string) => {
    setNotice(`正在把“${filename}”保存到当前项目的“下载内容”文件夹…`);
    const saved = await saveFileToOutputFolder(fileUrl, filename);
    setDownloadProgress((current) => ({
      ...current,
      [taskId]: {
        ...(current[taskId] ?? {
          downloaded_bytes: 0,
          total_bytes: null,
        }),
        status: "ready",
        progress: 100,
        speed: 0,
        eta: 0,
      },
    }));
    return saved;
  };

  useEffect(() => {
    try {
      const stored = localStorage.getItem(storageKey);
      if (!stored) return;
      const parsed = JSON.parse(stored) as DownloadTask[];
      if (!Array.isArray(parsed)) return;
      const now = Date.now();
      const restored = parsed.slice(0, 60).map((task) => {
        const formats = restoreLegacyImageGallery(
          Array.isArray(task.formats) ? task.formats : [],
          task.thumbnail,
        );
        const galleryIds = formats.filter((format) => format.imageIndex).map((format) => format.id);
        const selectedImageIds = Array.isArray(task.selectedImageIds)
          ? task.selectedImageIds.filter((id) => galleryIds.includes(id))
          : galleryIds;
        const normalized: DownloadTask = {
          ...task,
          formats,
          selectedFormat: task.selectedFormat || formats[0]?.id || "",
          selectedImageIds,
          duration: task.duration && !task.duration.includes("NaN") ? task.duration : "--:--",
        };
        if (task.status === "analyzing" || task.status === "preparing" || task.status === "prepared") {
          return {
            ...normalized,
            status: "failed" as const,
            error: task.status === "analyzing" ? "上次解析已中断，请重新解析。" : "上次准备的文件已释放，请重新解析后下载。",
          };
        }
        if (task.status === "ready" && !task.coreAssisted && !task.helperAssisted && now - task.updatedAt > ticketLifetime) {
          return { ...normalized, status: "expired" as const };
        }
        return normalized;
      });
      setTasks(restored);
      setSelectedId(restored[0]?.id ?? null);
    } catch {
      localStorage.removeItem(storageKey);
    }
  }, []);

  useEffect(() => {
    if (!resolverApiUrl) {
      setCoreOnline(false);
      setServiceOnline(false);
      return undefined;
    }
    let disposed = false;
    const checkServices = async () => {
      const [serviceReady, coreReady, memberSession] = await Promise.all([
        fetch(`${resolverApiUrl}/healthz`, { signal: AbortSignal.timeout(5000), cache: "no-store" })
          .then((response) => response.ok)
          .catch(() => false),
        fetch(`${resolverApiUrl}/v1/core/health`, { signal: AbortSignal.timeout(5000), cache: "no-store" })
          .then((response) => response.ok)
          .catch(() => false),
        fetch(`${resolverApiUrl}/v1/bilibili/session`, { signal: AbortSignal.timeout(5000), cache: "no-store" })
          .then((response) => response.ok ? response.json() as Promise<{ active: boolean }> : null)
          .catch(() => null),
      ]);
      if (!disposed) {
        setServiceOnline(serviceReady);
        setCoreOnline(coreReady);
        setBiliSessionActive(Boolean(memberSession?.active));
      }
    };
    void checkServices();
    const interval = window.setInterval(() => void checkServices(), 15_000);
    return () => {
      disposed = true;
      window.clearInterval(interval);
    };
  }, []);

  const storeTasks = (updater: (current: DownloadTask[]) => DownloadTask[]) => {
    setTasks((current) => {
      const next = updater(current);
      localStorage.setItem(storageKey, JSON.stringify(next));
      return next;
    });
  };

  const updateTask = (id: string, patch: Partial<DownloadTask>) => {
    storeTasks((current) => current.map((task) => (
      task.id === id ? { ...task, ...patch, updatedAt: Date.now() } : task
    )));
  };

  useEffect(() => {
    const downloadCapturedMedia = async (message: HelperMessage) => {
      const taskId = message.taskId as string;
      if (!message.mediaUrl || !message.platform) {
        const error = "浏览器助手版本过旧，请在扩展管理页点击“重新加载”后再试。";
        updateTask(taskId, { status: "failed", error });
        setNotice(error);
        return;
      }
      if (activeJobs.current[taskId]) return;
      updateTask(taskId, {
        status: "preparing",
        title: `${message.platform} 视频`,
        error: undefined,
      });
      setNotice("已捕获真实 MP4，正在本机直接下载，不会跳转到平台页面。");

      try {
        const response = await fetch(`${resolverApiUrl}/v1/helper/jobs`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({
            url: message.mediaUrl,
            platform: message.platform,
            headers: message.headers ?? {},
          }),
        });
        const payload = await response.json().catch(() => null) as DownloadJobResponse | null;
        if (!response.ok || !payload?.job_id) {
          throw new Error(readError(payload, "本机下载任务创建失败，请重试。"));
        }
        activeJobs.current[taskId] = payload.job_id;
        let current = payload;
        while (activeJobs.current[taskId] === payload.job_id) {
          setDownloadProgress((progress) => ({ ...progress, [taskId]: current }));
          if (current.status === "ready") {
            if (!current.file_ticket || !current.filename) {
              throw new Error("文件已完成，但保存凭证缺失，请重试。");
            }
            const fileUrl = `${resolverApiUrl}/v1/file/${encodeURIComponent(current.file_ticket)}`;
            const saved = await saveFileDirectly(taskId, fileUrl, current.filename);
            updateTask(taskId, { status: "sent", error: undefined });
            setNotice(`文件“${saved.filename}”已保存到当前项目的“下载内容”文件夹。`);
            delete activeJobs.current[taskId];
            return;
          }
          if (current.status === "failed") {
            throw new Error(current.error || "视频下载失败，请重试。");
          }
          await new Promise((resolve) => window.setTimeout(resolve, 800));
          const progressResponse = await fetch(
            `${resolverApiUrl}/v1/jobs/${encodeURIComponent(payload.job_id)}`,
            { headers: { Accept: "application/json" }, cache: "no-store" },
          );
          const progressPayload = await progressResponse.json().catch(() => null) as DownloadJobResponse | null;
          if (!progressResponse.ok || !progressPayload) {
            throw new Error(readError(progressPayload, "无法读取下载进度，请重试。"));
          }
          current = progressPayload;
        }
      } catch (error) {
        delete activeJobs.current[taskId];
        const text = error instanceof Error ? error.message : "本机下载失败，请重试。";
        updateTask(taskId, { status: "failed", error: text });
        setNotice(text);
      }
    };

    const onHelperMessage = (event: MessageEvent<HelperMessage>) => {
      if (event.source !== window || event.origin !== window.location.origin) return;
      const message = event.data;
      if (message?.source !== "streamnest-helper" || !message.type) return;
      if (message.type === "STREAMNEST_HELPER_READY") {
        setHelperVersion(message.version ?? "");
        setHelperBiliCapability(message.biliSyncSupported === true);
        return;
      }
      if (message.type === "STREAMNEST_BILI_SYNC_ACK") {
        setNotice("浏览器助手已收到请求，正在仅向本机解析服务同步 B 站会话…");
        return;
      }
      if (message.type === "STREAMNEST_BILI_SYNCED") {
        biliSyncPending.current = false;
        setBiliSessionActive(true);
        setBiliSyncing(false);
        setNotice(message.message || "B 站会话已在本机同步；请重新解析视频。 ");
        return;
      }
      if (message.type === "STREAMNEST_BILI_SYNC_ERROR") {
        biliSyncPending.current = false;
        setBiliSyncing(false);
        setNotice(message.message || "B 站会话同步失败，请确认浏览器助手已重新加载。 ");
        return;
      }
      if (!message.taskId) return;
      if (message.type === "STREAMNEST_HELPER_WAITING") {
        updateTask(message.taskId, { status: "preparing", error: undefined });
        setNotice(message.message || "浏览器助手正在后台读取公开媒体请求。");
      } else if (message.type === "STREAMNEST_HELPER_CAPTURED") {
        void downloadCapturedMedia(message);
      } else if (message.type === "STREAMNEST_HELPER_PROGRESS") {
        setDownloadProgress((current) => ({
          ...current,
          [message.taskId as string]: {
            status: "downloading",
            progress: message.progress ?? 0,
            downloaded_bytes: message.downloadedBytes ?? 0,
            total_bytes: message.totalBytes ?? null,
            speed: message.speed ?? null,
            eta: message.eta ?? null,
          },
        }));
      } else if (message.type === "STREAMNEST_HELPER_COMPLETE") {
        setDownloadProgress((current) => ({
          ...current,
          [message.taskId as string]: {
            ...(current[message.taskId as string] ?? {
              status: "ready",
              downloaded_bytes: 0,
              total_bytes: null,
              speed: null,
              eta: 0,
            }),
            status: "ready",
            progress: 100,
            speed: 0,
            eta: 0,
          },
        }));
        updateTask(message.taskId, { status: "sent", error: undefined });
        setNotice(`文件“${message.filename || "video.mp4"}”已保存到浏览器下载文件夹。`);
      } else if (message.type === "STREAMNEST_HELPER_ERROR") {
        const error = message.message || "浏览器助手未能捕获可下载的视频。";
        updateTask(message.taskId, { status: "failed", error });
        setNotice(error);
      }
    };
    window.addEventListener("message", onHelperMessage);
    window.postMessage({ source: "streamnest-manager", type: "STREAMNEST_HELPER_PING" }, window.location.origin);
    return () => window.removeEventListener("message", onHelperMessage);
    // The compatibility listener is registered once; task writes use functional state updates.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const syncBilibiliSession = () => {
    if (!resolverApiUrl || serviceOnline === false) {
      setNotice("本机解析服务未连接，请先启动 StreamNest。");
      return;
    }
    if (!helperSupportsBili) {
      setNotice(helperVersion
        ? helperVersionReady
          ? "浏览器助手文件已更新，但当前活动扩展尚未重新加载。请在 Edge 扩展管理页点“重新加载”；也可点工具栏助手图标，在弹窗中同步。"
          : `当前浏览器助手是 ${helperVersion}，请在 Edge 扩展管理页重新加载，升级到 0.3.6 后再试。`
        : "网页未检测到助手。请在 Edge 扩展管理页重新加载当前项目的 browser-helper；也可点工具栏助手图标，在弹窗中同步本机会话。");
      return;
    }
    biliSyncPending.current = true;
    setBiliSyncing(true);
    setNotice("正在从当前 Edge 配置中仅同步 B 站域名会话，请稍候…");
    window.postMessage({ source: "streamnest-manager", type: "STREAMNEST_BILI_SYNC" }, window.location.origin);
    window.setTimeout(() => {
      if (!biliSyncPending.current) return;
      biliSyncPending.current = false;
      setBiliSyncing(false);
      setNotice("未收到浏览器助手回应。请在 edge://extensions 重新加载 StreamNest 浏览器助手后再试。");
    }, 10_000);
  };

  const clearBilibiliSession = async () => {
    try {
      const response = await fetch(`${resolverApiUrl}/v1/bilibili/session/clear`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      if (!response.ok) throw new Error("本机会话暂时无法断开，请重启解析服务。");
      setBiliSessionActive(false);
      setNotice("已从本机解析服务内存中移除 B 站会话；Edge 登录状态不受影响。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "断开 B 站会话失败。");
    }
  };

  const resolveTask = async (
    targetUrl: string,
    platform: Platform,
    confirmed: boolean,
    existingId?: string,
  ) => {
    const id = existingId ?? makeId();
    const now = Date.now();
    if (existingId) {
      updateTask(id, {
        status: "analyzing",
        title: "正在读取视频信息…",
        error: undefined,
        formats: [],
        selectedFormat: "",
      });
    } else {
      const task: DownloadTask = {
        id,
        url: targetUrl,
        platform,
        title: "正在读取视频信息…",
        author: "",
        duration: "--:--",
        thumbnail: null,
        formats: [],
        selectedFormat: "",
        status: "analyzing",
        createdAt: now,
        updatedAt: now,
        adultConfirmed: confirmed,
      };
      storeTasks((current) => [task, ...current].slice(0, 60));
    }

    setSelectedId(id);
    setNotice("");

    try {
      const response = await fetch(`${resolverApiUrl}/v1/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: targetUrl, adult_confirmed: confirmed }),
      });
      const payload = await response.json().catch(() => null) as ResolveResponse | null;
      if (!response.ok || !payload) {
        throw new Error(readError(payload, "暂时无法解析这个链接，请稍后重试。"));
      }
      if (!payload.formats?.length) {
        throw new Error("没有找到可下载的公开格式。受保护、登录可见或直播内容无法处理。");
      }

      updateTask(id, {
        title: payload.title || `${platform.name} 视频`,
        author: payload.author || "未知作者",
        duration: payload.duration || formatDuration(payload.duration_seconds),
        thumbnail: payload.thumbnail,
        formats: payload.formats,
        selectedFormat: preferredFormatId(platform.name, payload.formats),
        ticketCreatedAt: Date.now(),
        status: "ready",
        error: undefined,
      });
      const highest = maxVideoHeight(payload.formats);
      const selectedLabel = payload.formats.find((format) => format.id === preferredFormatId(platform.name, payload.formats))?.label;
      setNotice(platform.name === "Bilibili" && highest <= 480
        ? biliSessionActive
          ? "这次解析只返回了 480P 或更低画质；账号可观看不代表平台提供可保存的高清媒体流。"
          : "当前仅有访客画质。请同步本机 Edge B 站会话，再重新解析，查看是否提供更高画质。"
        : platform.name === "Bilibili" && highest > 0
          ? `解析完成，已默认选择本次解析列出的最高分辨率 ${highest}P${highest === 2160 ? "（4K）" : ""}；能否下载以实际文件为准。`
          : platform.name === "海角网"
            ? `解析完成，共找到 ${payload.formats.length} 段独立视频，已默认选择时长最长的一段；站点未提供可核实的清晰度档位。`
          : selectedLabel
            ? `解析完成，已默认选择本次解析列出的最高分辨率：${selectedLabel}；能否下载以实际文件为准。`
            : "解析完成，选择格式后即可下载。");
      return payload;
    } catch (error) {
      updateTask(id, {
        status: "failed",
        title: `${platform.name} 视频解析失败`,
        error: error instanceof Error ? error.message : "解析失败，请稍后重试。",
      });
      return null;
    }
  };

  const resolveCoreTask = async (
    targetUrl: string,
    platform: Platform,
    existingId?: string,
  ) => {
    const id = existingId ?? makeId();
    const now = Date.now();
    const pending: Partial<DownloadTask> = {
      url: targetUrl,
      platform,
      title: `正在读取${platform.name}视频信息…`,
      author: "本机国内平台下载核心",
      duration: "--:--",
      thumbnail: null,
      formats: [],
      selectedFormat: "",
      status: "analyzing",
      helperAssisted: false,
      coreAssisted: true,
      error: undefined,
    };

    if (existingId) {
      updateTask(id, pending);
    } else {
      const task: DownloadTask = {
        id,
        createdAt: now,
        updatedAt: now,
        adultConfirmed: false,
        ...(pending as Omit<DownloadTask, "id" | "createdAt" | "updatedAt" | "adultConfirmed">),
      };
      storeTasks((current) => [task, ...current].slice(0, 60));
    }

    setSelectedId(id);
    setNotice(`正在通过当前网页解析${platform.name}公开视频…`);

    try {
      const response = await fetch(`${resolverApiUrl}/v1/core/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ url: targetUrl }),
      });
      const payload = await response.json().catch(() => null) as LocalCoreResolveResponse | null;
      if (!response.ok || !payload?.ok || !payload.meta) {
        throw new Error(readError(payload, `暂时无法解析这个${platform.name}链接，请稍后重试。`));
      }
      const formats = (payload.meta.formats ?? []).flatMap<FormatOption>((format): FormatOption[] => {
        if (format.kind === "image_collection" && format.download_ticket) {
          return [{
            id: format.id,
            label: format.quality,
            detail: format.note || `图片合集 · ${format.image_count ?? "多"} 张原尺寸图片 · ZIP`,
            size: "ZIP 压缩包",
            kind: "图片",
            download_ticket: format.download_ticket,
            imageCount: format.image_count,
            imageCollection: true,
          }];
        }
        if (format.kind === "image" && format.download_ticket) {
          return [{
            id: format.id,
            label: format.quality || "封面原图",
            detail: format.note || "图片 · 平台公开图片 · 原始尺寸",
            size: "未知大小",
            kind: "图片",
            download_ticket: format.download_ticket,
            imageIndex: format.image_index,
            imageCount: format.image_count,
            imagePreview: format.image_index
              ? payload.meta.images?.[format.image_index - 1]
              : payload.meta.thumbnail ?? undefined,
          }];
        }
        if (format.kind === "frame") {
          return [{
            id: format.id,
            label: "视频首帧",
            detail: format.note || "图片 · 从公开视频生成 · JPG",
            size: "生成后可用",
            kind: "图片",
            download_ticket: "",
            coreQuality: format.source_quality || "720p",
            coreFormat: "frame",
          }];
        }
        if (format.ext.toLowerCase() !== "mp4") return [];
        return [{
          id: format.id,
          label: format.quality.toUpperCase(),
          detail: `${format.ext.toUpperCase()} · ${format.note || "平台公开播放流"}`,
          size: formatFileSize(format.size),
          kind: videoHeight({ id: format.id, label: format.quality, kind: "视频" }) >= 1080 ? "高清" : "流畅",
          download_ticket: "",
          coreQuality: format.quality,
          coreFormat: format.ext,
        }];
      });
      if (!formats.length) {
        throw new Error("没有找到可下载的公开 MP4 画质。登录可见、私密或直播内容无法处理。");
      }

      updateTask(id, {
        title: payload.meta.title || `${platform.name} 视频`,
        author: payload.meta.author || "未知作者",
        duration: formatDuration(payload.meta.duration),
        thumbnail: payload.meta.thumbnail,
        formats,
        selectedFormat: preferredFormatId(platform.name, formats),
        selectedImageIds: formats.filter((format) => format.imageIndex).map((format) => format.id),
        ticketCreatedAt: Date.now(),
        status: "ready",
        helperAssisted: false,
        coreAssisted: true,
        error: undefined,
      });
      setCoreOnline(true);
      setNotice(`${platform.name}解析完成，选择画质后即可直接下载。`);
      return formats;
    } catch (error) {
      const message = error instanceof Error ? error.message : `${platform.name}解析失败，请稍后重试。`;
      updateTask(id, {
        status: "failed",
        title: `${platform.name} 视频解析失败`,
        helperAssisted: false,
        coreAssisted: true,
        error: message,
      });
      setNotice(message);
      return null;
    }
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const cleanUrl = extractVideoUrl(url);
    const platform = detectPlatform(cleanUrl);

    if (!cleanUrl || !platform) {
      setNotice("请粘贴受支持平台的完整视频链接。");
      inputRef.current?.focus();
      return;
    }
    if (platform.adult && !adultConfirmed) {
      setNotice("该平台仅限成年人使用，请先确认年龄及下载授权。");
      return;
    }
    if (platform.name === "抖音" || platform.name === "快手") {
      if (!resolverApiUrl || coreOnline === false) {
        setNotice("抖音/快手下载核心未连接。请检查独立核心是否仍在原目录，再从统一启动入口启动；其他平台可继续使用。");
        return;
      }
      setUrl("");
      await resolveCoreTask(cleanUrl, platform);
      return;
    }
    if (!resolverApiUrl || serviceOnline === false) {
      setNotice("本机解析服务未连接。请双击当前项目的“打开StreamNest统一下载管理器.cmd”，等待服务在线后重试；无需重复粘贴链接。");
      return;
    }

    setUrl("");
    setAdultConfirmed(false);
    await resolveTask(cleanUrl, platform, adultConfirmed);
  };

  const pasteFromClipboard = async () => {
    try {
      const value = await navigator.clipboard.readText();
      const extracted = extractVideoUrl(value);
      setUrl(extracted || value);
      setNotice(
        extracted && extracted !== value.trim()
          ? "已从剪贴板文字中自动提取视频链接。"
          : value ? "已粘贴剪贴板内容。" : "剪贴板里没有链接。",
      );
      inputRef.current?.focus();
    } catch {
      setNotice("浏览器未允许读取剪贴板，请手动粘贴链接。");
      inputRef.current?.focus();
    }
  };

  const retryTask = (task: DownloadTask) => {
    delete activeJobs.current[task.id];
    setDownloadProgress((current) => {
      const next = { ...current };
      delete next[task.id];
      return next;
    });
    const prepared = preparedFiles[task.id];
    if (prepared) {
      setPreparedFiles((current) => {
        const next = { ...current };
        delete next[task.id];
        return next;
      });
    }
    if (task.platform.adult && !task.adultConfirmed) {
      setUrl(task.url);
      setNotice("请重新确认年龄及下载授权后再解析。");
      inputRef.current?.focus();
      return;
    }
    if (task.coreAssisted || task.helperAssisted) {
      void resolveCoreTask(task.url, task.platform, task.id);
      return;
    }
    void resolveTask(task.url, task.platform, task.adultConfirmed, task.id);
  };

  const removeTask = (id: string) => {
    const activeJob = activeJobs.current[id];
    if (activeJob?.startsWith("core:")) {
      const coreTaskId = activeJob.slice("core:".length);
      void fetch(`${resolverApiUrl}/v1/core/tasks/${encodeURIComponent(coreTaskId)}/cancel`, {
        method: "POST",
        headers: { Accept: "application/json" },
      }).catch(() => undefined);
    }
    delete activeJobs.current[id];
    setDownloadProgress((current) => {
      const next = { ...current };
      delete next[id];
      return next;
    });
    const prepared = preparedFiles[id];
    if (prepared) {
      setPreparedFiles((current) => {
        const next = { ...current };
        delete next[id];
        return next;
      });
    }
    storeTasks((current) => current.filter((task) => task.id !== id));
    if (selectedId === id) {
      const remaining = tasks.filter((task) => task.id !== id);
      setSelectedId(remaining[0]?.id ?? null);
    }
  };

  const startCoreDownload = async (task: DownloadTask) => {
    const selected = task.formats.find((format) => format.id === task.selectedFormat)
      ?? task.formats[0];
    if (!selected) {
      setNotice("没有可用画质，请重新解析链接。");
      return;
    }

    updateTask(task.id, {
      status: "preparing",
      helperAssisted: false,
      coreAssisted: true,
      error: undefined,
    });
    setDownloadProgress((current) => ({
      ...current,
      [task.id]: {
        status: "queued",
        progress: 0,
        downloaded_bytes: 0,
        total_bytes: null,
        speed: null,
        eta: null,
      },
    }));
    setNotice(`${task.platform.name}下载任务已开始，当前页面会实时显示进度。`);

    try {
      const response = await fetch(`${resolverApiUrl}/v1/core/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          url: task.url,
          quality: selected.coreQuality || selected.label.toLowerCase(),
          format: "mp4",
        }),
      });
      const created = await response.json().catch(() => null) as { ok?: boolean; task?: LocalCoreTask } | null;
      if (!response.ok || !created?.ok || !created.task) {
        throw new Error(readError(created, "国内平台下载任务创建失败，请重试。"));
      }

      const marker = `core:${created.task.id}`;
      activeJobs.current[task.id] = marker;
      let current = created.task;
      let appliedTitle = task.title;
      while (activeJobs.current[task.id] === marker) {
        const completed = current.status === "completed";
        const failed = current.status === "failed" || current.status === "cancelled";
        setDownloadProgress((progress) => ({
          ...progress,
          [task.id]: {
            status: completed ? "ready" : failed ? "failed" : current.status === "waiting" || current.status === "parsing" ? "queued" : "downloading",
            progress: completed ? 100 : Math.max(0, Math.min(100, current.progress ?? 0)),
            downloaded_bytes: current.downloadedBytes ?? 0,
            total_bytes: current.expectedSize ?? null,
            speed: current.speed ?? null,
            eta: current.eta ?? null,
          },
        }));
        const currentTitle = current.title?.trim();
        if (
          currentTitle
          && currentTitle !== "解析中…"
          && currentTitle !== "未命名视频"
          && currentTitle !== appliedTitle
        ) {
          appliedTitle = currentTitle;
          updateTask(task.id, { title: currentTitle });
        }
        if (completed) {
          const isFrame = selected.coreFormat === "frame";
          const filename = `${current.title || task.title}${isFrame ? "-first-frame.jpg" : ".mp4"}`;
          const saved = await saveFileDirectly(
            task.id,
            `${resolverApiUrl}/v1/core/${isFrame ? "frame" : "file"}/${current.id}?consume=true`,
            filename,
          );
          updateTask(task.id, { status: "sent", error: undefined });
          setNotice(`文件“${saved.filename}”已保存到当前项目的“下载内容”文件夹。`);
          delete activeJobs.current[task.id];
          return;
        }
        if (failed) {
          throw new Error(current.errorMessage || (current.status === "cancelled" ? "下载任务已取消。" : "视频下载失败，请重试。"));
        }

        await new Promise((resolve) => window.setTimeout(resolve, 800));
        const progressResponse = await fetch(
          `${resolverApiUrl}/v1/core/tasks/${current.id}`,
          { headers: { Accept: "application/json" }, cache: "no-store" },
        );
        const progressPayload = await progressResponse.json().catch(() => null) as { ok?: boolean; task?: LocalCoreTask } | null;
        if (!progressResponse.ok || !progressPayload?.task) {
          throw new Error(readError(progressPayload, "无法读取国内平台下载进度，请重试。"));
        }
        current = progressPayload.task;
      }
    } catch (error) {
      delete activeJobs.current[task.id];
      const message = error instanceof Error ? error.message : "国内平台下载失败，请重试。";
      updateTask(task.id, { status: "failed", error: message });
      setNotice(message);
    }
  };

  const startDownload = async (task: DownloadTask, allowTicketRefresh = true): Promise<void> => {
    const selectedForRouting = task.formats.find((format) => format.id === task.selectedFormat)
      ?? task.formats[0];
    if (
      (task.coreAssisted || task.helperAssisted)
      && (selectedForRouting?.kind !== "图片" || selectedForRouting.coreFormat === "frame")
    ) {
      await startCoreDownload(task);
      return;
    }
    let startedJobId: string | null = null;
    const selected = task.formats.find((format) => format.id === task.selectedFormat)
      ?? task.formats[0];
    if (!selected) {
      setNotice("没有可用格式，请重新解析链接。");
      return;
    }
    const refreshTicketAndRestart = async (): Promise<boolean> => {
      if (!allowTicketRefresh) return false;
      setNotice("下载凭证已失效，正在自动重新解析并继续下载…");
      const refreshedFormats = task.coreAssisted || task.helperAssisted
        ? await resolveCoreTask(task.url, task.platform, task.id)
        : (await resolveTask(task.url, task.platform, task.adultConfirmed, task.id))?.formats;
      if (!refreshedFormats?.length) return false;
      const refreshedFormat = refreshedFormats.find((format) => format.id === selected.id)
        ?? refreshedFormats.find((format) => format.label === selected.label)
        ?? refreshedFormats[0];
      updateTask(task.id, { selectedFormat: refreshedFormat.id });
      await startDownload(
        {
          ...task,
          formats: refreshedFormats,
          selectedFormat: refreshedFormat.id,
          ticketCreatedAt: Date.now(),
          status: "ready",
          error: undefined,
        },
        false,
      );
      return true;
    };
    if (Date.now() - (task.ticketCreatedAt ?? task.updatedAt) > ticketLifetime) {
      if (!(await refreshTicketAndRestart())) {
        updateTask(task.id, { status: "expired" });
        setNotice("下载链接已过期，请重新解析后再下载。");
      }
      return;
    }

    updateTask(task.id, { status: "preparing", error: undefined });
    setDownloadProgress((current) => ({
      ...current,
      [task.id]: {
        status: "queued",
        progress: 0,
        downloaded_bytes: 0,
        total_bytes: null,
        speed: null,
        eta: null,
      },
    }));
    setNotice("下载任务已开始，进度、速度和剩余时间会实时更新。");

    try {
      const response = await fetch(
        `${resolverApiUrl}/v1/jobs/${encodeURIComponent(selected.download_ticket)}`,
        { method: "POST", headers: { Accept: "application/json" } },
      );
      const payload = await response.json().catch(() => null) as DownloadJobResponse | null;
      if (!response.ok) {
        if (response.status === 404 && readErrorCode(payload) === "ticket_not_found") {
          if (await refreshTicketAndRestart()) return;
        }
        throw new Error(readError(payload, "下载任务创建失败，请重新解析后再试。"));
      }
      if (!payload?.job_id) {
        throw new Error("服务没有返回下载任务，请重新解析后再试。");
      }

      startedJobId = payload.job_id;
      activeJobs.current[task.id] = payload.job_id;
      let current = payload;
      while (activeJobs.current[task.id] === payload.job_id) {
        setDownloadProgress((progress) => ({ ...progress, [task.id]: current }));
        if (current.status === "ready") {
          if (!current.file_ticket || !current.filename) {
            throw new Error("文件已完成，但保存凭证缺失，请重新下载。");
          }
          const filename = current.filename;
          const fileUrl = `${resolverApiUrl}/v1/file/${encodeURIComponent(current.file_ticket)}`;
          const saved = await saveFileDirectly(task.id, fileUrl, filename);
          updateTask(task.id, { status: "sent", error: undefined });
          setNotice(`文件“${saved.filename}”已保存到当前项目的“下载内容”文件夹。`);
          delete activeJobs.current[task.id];
          return;
        }
        if (current.status === "failed") {
          throw new Error(current.error || "文件下载失败，请重新解析后再试。");
        }
        await new Promise((resolve) => window.setTimeout(resolve, 800));
        const progressResponse = await fetch(
          `${resolverApiUrl}/v1/jobs/${encodeURIComponent(payload.job_id)}`,
          { headers: { Accept: "application/json" }, cache: "no-store" },
        );
        const progressPayload = await progressResponse.json().catch(() => null) as DownloadJobResponse | null;
        if (!progressResponse.ok || !progressPayload) {
          throw new Error(readError(progressPayload, "无法读取下载进度，请稍后重试。"));
        }
        current = progressPayload;
      }
    } catch (error) {
      if (startedJobId && activeJobs.current[task.id] !== startedJobId) return;
      delete activeJobs.current[task.id];
      const message = error instanceof Error ? error.message : "文件准备失败，请稍后重试。";
      updateTask(task.id, { status: "failed", error: message });
      setNotice(message);
    }
  };

  const savePreparedFile = async (task: DownloadTask) => {
    const prepared = preparedFiles[task.id];
    if (!prepared) {
      updateTask(task.id, { status: "failed", error: "准备好的文件已释放，请重新解析后下载。" });
      setNotice("准备好的文件已释放，请重新解析后下载。");
      return;
    }

    try {
      const saved = await saveFileDirectly(task.id, prepared.url, prepared.filename);
      updateTask(task.id, { status: "sent", error: undefined });
      setNotice(`文件“${saved.filename}”已保存到当前项目的“下载内容”文件夹。`);
      setPreparedFiles((current) => {
        const next = { ...current };
        delete next[task.id];
        return next;
      });
      setDownloadProgress((current) => {
        const next = { ...current };
        delete next[task.id];
        return next;
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : "文件保存失败，请重试。";
      updateTask(task.id, { status: "failed", error: message });
      setNotice(message);
    }
  };

  const handleDownloadClick = (task: DownloadTask) => {
    if (task.status === "prepared") {
      void savePreparedFile(task);
    } else {
      void startDownload(task);
    }
  };

  const handleImageDownloadClick = (task: DownloadTask) => {
    const galleryFormats = task.formats.filter((format) => format.imageIndex && format.download_ticket);
    if (galleryFormats.length) {
      void downloadImageSelection(task, galleryFormats.map((format) => format.id));
      return;
    }
    const imageFormat = task.formats.find((format) => format.id === "image-all")
      ?? task.formats.find((format) => format.kind === "图片");
    if (!imageFormat) {
      setNotice("这个任务暂时没有可下载的封面或视频首帧。");
      return;
    }
    updateTask(task.id, { selectedFormat: imageFormat.id });
    void startDownload({ ...task, selectedFormat: imageFormat.id, status: "ready" });
  };

  const toggleImageSelection = (task: DownloadTask, formatId: string) => {
    const allImageIds = task.formats.filter((format) => format.imageIndex).map((format) => format.id);
    const current = new Set(task.selectedImageIds ?? allImageIds);
    if (current.has(formatId)) current.delete(formatId);
    else current.add(formatId);
    updateTask(task.id, { selectedImageIds: allImageIds.filter((id) => current.has(id)) });
  };

  const setAllImagesSelected = (task: DownloadTask, selected: boolean) => {
    updateTask(task.id, {
      selectedImageIds: selected
        ? task.formats.filter((format) => format.imageIndex).map((format) => format.id)
        : [],
    });
  };

  const downloadImageSelection = async (
    task: DownloadTask,
    requestedIds: string[],
    allowRefresh = true,
  ): Promise<void> => {
    const galleryFormats = task.formats.filter(
      (format) => format.imageIndex && format.download_ticket && requestedIds.includes(format.id),
    );
    if (!galleryFormats.length) {
      setNotice("请至少选择一张图片后再下载。");
      return;
    }

    if (allowRefresh && Date.now() - (task.ticketCreatedAt ?? task.updatedAt) > ticketLifetime) {
      const selectedIndexes = new Set(galleryFormats.map((format) => format.imageIndex));
      setNotice("图片链接已过期，正在重新解析并保留你的选择…");
      const refreshedFormats = await resolveCoreTask(task.url, task.platform, task.id);
      if (!refreshedFormats?.length) return;
      const refreshedIds = refreshedFormats
        .filter((format) => format.imageIndex && selectedIndexes.has(format.imageIndex))
        .map((format) => format.id);
      await downloadImageSelection(
        {
          ...task,
          formats: refreshedFormats,
          selectedImageIds: refreshedIds,
          ticketCreatedAt: Date.now(),
          status: "ready",
          error: undefined,
        },
        refreshedIds,
        false,
      );
      return;
    }

    const allGalleryFormats = task.formats.filter((format) => format.imageIndex && format.download_ticket);
    const allCollection = task.formats.find((format) => format.imageCollection && format.download_ticket);
    if (galleryFormats.length === allGalleryFormats.length && allCollection) {
      await startDownload(
        { ...task, selectedFormat: allCollection.id, status: "ready", error: undefined },
        allowRefresh,
      );
      return;
    }
    if (galleryFormats.length === 1) {
      await startDownload(
        { ...task, selectedFormat: galleryFormats[0].id, status: "ready", error: undefined },
        allowRefresh,
      );
      return;
    }

    setNotice(`正在打包所选 ${galleryFormats.length} 张图片…`);
    try {
      const response = await fetch(`${resolverApiUrl}/v1/image-collections`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          tickets: galleryFormats.map((format) => format.download_ticket),
          title: task.title,
        }),
      });
      const payload = await response.json().catch(() => null) as {
        download_ticket?: string;
        image_count?: number;
      } | null;
      if (!response.ok || !payload?.download_ticket) {
        if (response.status === 404 && allowRefresh) {
          const selectedIndexes = new Set(galleryFormats.map((format) => format.imageIndex));
          setNotice("部分图片链接已过期，正在重新解析并保留你的选择…");
          const refreshedFormats = await resolveCoreTask(task.url, task.platform, task.id);
          if (!refreshedFormats?.length) return;
          const refreshedIds = refreshedFormats
            .filter((format) => format.imageIndex && selectedIndexes.has(format.imageIndex))
            .map((format) => format.id);
          await downloadImageSelection(
            {
              ...task,
              formats: refreshedFormats,
              selectedImageIds: refreshedIds,
              ticketCreatedAt: Date.now(),
              status: "ready",
              error: undefined,
            },
            refreshedIds,
            false,
          );
          return;
        }
        throw new Error(readError(payload, "无法打包所选图片，请重新解析后再试。"));
      }

      const selectionFormat: FormatOption = {
        id: `image-selection-${Date.now()}`,
        label: `所选 ${payload.image_count ?? galleryFormats.length} 张图片`,
        detail: "自定义图片合集 · ZIP",
        size: "ZIP 压缩包",
        kind: "图片",
        download_ticket: payload.download_ticket,
        imageCollection: true,
        imageCount: payload.image_count ?? galleryFormats.length,
      };
      await startDownload(
        {
          ...task,
          formats: [...task.formats, selectionFormat],
          selectedFormat: selectionFormat.id,
          selectedImageIds: galleryFormats.map((format) => format.id),
          ticketCreatedAt: Date.now(),
          status: "ready",
          error: undefined,
        },
        false,
      );
    } catch (error) {
      const message = error instanceof Error ? error.message : "所选图片打包失败，请稍后重试。";
      updateTask(task.id, { status: "failed", error: message });
      setNotice(message);
    }
  };

  const counts = useMemo(() => ({
    all: tasks.length,
    ready: tasks.filter((task) => task.status === "ready" || task.status === "analyzing" || task.status === "preparing" || task.status === "prepared").length,
    sent: tasks.filter((task) => task.status === "sent").length,
    failed: tasks.filter((task) => task.status === "failed" || task.status === "expired").length,
  }), [tasks]);

  const filteredTasks = useMemo(() => tasks.filter((task) => {
    const matchesFilter = filter === "all"
      || (filter === "ready" && (task.status === "ready" || task.status === "analyzing" || task.status === "preparing" || task.status === "prepared"))
      || (filter === "sent" && task.status === "sent")
      || (filter === "failed" && (task.status === "failed" || task.status === "expired"));
    const needle = search.trim().toLowerCase();
    const matchesSearch = !needle
      || task.title.toLowerCase().includes(needle)
      || task.platform.name.toLowerCase().includes(needle)
      || task.author.toLowerCase().includes(needle);
    return matchesFilter && matchesSearch;
  }), [filter, search, tasks]);

  const selectedTask = tasks.find((task) => task.id === selectedId) ?? null;
  const selectedTaskGalleryFormats = selectedTask
    ? selectedTask.formats.filter((format) => format.imageIndex && format.download_ticket)
    : [];
  const selectedTaskHasGallery = selectedTaskGalleryFormats.length > 1;
  const selectedTaskDisplayFormats = selectedTask
    ? selectedTaskHasGallery
      ? sortFormatsForDisplay(selectedTask.formats.filter((format) => !format.imageIndex && !format.imageCollection))
      : sortFormatsForDisplay(selectedTask.formats)
    : [];
  const selectedTaskDisplayFormatId = selectedTaskDisplayFormats.some(
    (format) => format.id === selectedTask?.selectedFormat,
  )
    ? selectedTask?.selectedFormat ?? ""
    : selectedTaskDisplayFormats[0]?.id ?? "";
  const selectedTaskDisplayFormat = selectedTaskDisplayFormats.find(
    (format) => format.id === selectedTaskDisplayFormatId,
  );
  const selectedTaskBestFormatId = selectedTask
    ? preferredFormatId(selectedTask.platform.name, selectedTaskDisplayFormats)
    : "";
  const selectedTaskQuickFormat = selectedTaskDisplayFormats.find((format) => format.kind === "快速");

  const renderImagePicker = (task: DownloadTask) => {
    const images = task.formats.filter((format) => format.imageIndex && format.download_ticket);
    if (images.length <= 1) return null;
    const allIds = images.map((format) => format.id);
    const selectedIds = new Set(task.selectedImageIds ?? allIds);
    const selectedCount = allIds.filter((id) => selectedIds.has(id)).length;

    return (
      <section className="image-picker" aria-label="图片选择区">
        <div className="image-picker-heading">
          <div><strong>选择要下载的图片</strong><small>依次点击图片即可多选，无需按 Ctrl</small></div>
          <span>{selectedCount}/{images.length} 已选</span>
        </div>
        <div className="image-picker-tools">
          <button type="button" onClick={() => setAllImagesSelected(task, true)}>全选</button>
          <button type="button" onClick={() => setAllImagesSelected(task, false)}>清空</button>
        </div>
        <div className="image-picker-grid">
          {images.map((format) => {
            const checked = selectedIds.has(format.id);
            return (
              <label key={format.id} className={checked ? "selected" : ""}>
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => toggleImageSelection(task, format.id)}
                />
                <span className="image-check">{checked ? "✓" : ""}</span>
                {format.imagePreview
                  ? <img src={format.imagePreview} alt={`第 ${format.imageIndex} 张图片`} referrerPolicy="no-referrer" />
                  : <span className="image-placeholder">图</span>}
                <b>{format.imageIndex}</b>
              </label>
            );
          })}
        </div>
        <div className="image-picker-actions">
          <button
            type="button"
            className="download-selected-images"
            disabled={selectedCount === 0}
            onClick={() => void downloadImageSelection(task, allIds.filter((id) => selectedIds.has(id)))}
          >
            ↓ 下载所选 {selectedCount} 张
          </button>
          <button
            type="button"
            className="download-all-images"
            onClick={() => void downloadImageSelection(task, allIds)}
          >
            ▣ 一键下载全部 {images.length} 张
          </button>
        </div>
        <p>两张及以上会自动打包成 ZIP；只选一张时直接保存原图。默认保存到当前项目的“下载内容”文件夹。</p>
      </section>
    );
  };

  const filterItems: { key: TaskFilter; label: string; glyph: string }[] = [
    { key: "all", label: "全部任务", glyph: "▦" },
    { key: "ready", label: "等待下载", glyph: "↓" },
    { key: "sent", label: "已下载", glyph: "✓" },
    { key: "failed", label: "异常任务", glyph: "!" },
  ];

  return (
    <main className="manager-app">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark"><i /><i /><i /></span>
          <span><strong>StreamNest</strong><small>在线视频下载管理器</small></span>
        </div>

        <button className="new-task-button" onClick={() => inputRef.current?.focus()}>
          <span>＋</span> 新建下载任务
        </button>

        <nav className="side-nav" aria-label="任务分类">
          <p>任务</p>
          {filterItems.map((item) => (
            <button
              key={item.key}
              className={filter === item.key ? "active" : ""}
              onClick={() => setFilter(item.key)}
            >
              <span className="nav-glyph">{item.glyph}</span>
              <span>{item.label}</span>
              <b>{counts[item.key]}</b>
            </button>
          ))}
        </nav>

        <div className="sidebar-footer">
          <div className="service-state">
            <span className={serviceOnline ? "online" : serviceOnline === false ? "offline" : "checking"} />
            <div><strong>{serviceOnline ? "解析服务在线" : serviceOnline === false ? "解析服务离线" : "正在连接服务"}</strong><small>可识别 {platforms.length} 个平台 · 具体画质与可下载性以当前链接解析结果为准</small></div>
          </div>
          <div className="service-state">
            <span className={coreOnline ? "online" : coreOnline === false ? "offline" : "checking"} />
            <div>
              <strong>{coreOnline ? "抖音/快手下载核心在线" : coreOnline === false ? "抖音/快手下载核心离线" : "正在连接国内平台核心"}</strong>
              <small>{coreOnline ? "已合并到当前网页 · 无需扩展" : "独立核心未运行或目录缺失 · 其他平台可用"}</small>
            </div>
          </div>
          <p>任务记录仅保存在这台设备。请只下载你有权保存的公开内容。</p>
        </div>
      </aside>

      <section className="manager-shell">
        <header className="topbar">
          <div><h1>下载管理</h1><p>解析链接、选择格式并管理本机下载任务</p></div>
          <div className="topbar-actions">
            <span className="local-label">本机记录</span>
            <button
              className="clear-button"
              disabled={!counts.sent}
              onClick={() => {
                storeTasks((current) => current.filter((task) => task.status !== "sent"));
                if (selectedTask?.status === "sent") setSelectedId(null);
              }}
            >清理已下载</button>
          </div>
        </header>

        <div className="workspace">
          <section className="primary-column">
            <form className="create-panel" onSubmit={handleSubmit}>
              <div className="panel-heading">
                <div><span className="eyebrow">新任务</span><h2>粘贴在线视频链接</h2></div>
                <span className="platform-count">自动识别平台</span>
              </div>
              <div className={`url-control ${currentPlatform ? "recognized" : ""}`}>
                <span className="link-glyph">↗</span>
                <input
                  ref={inputRef}
                  value={url}
                  onChange={(event) => { setUrl(event.target.value); setNotice(""); }}
                  placeholder="粘贴视频链接或包含链接的分享文字"
                  aria-label="视频链接"
                />
                {currentPlatform && (
                  <span className={`detected-platform tone-${currentPlatform.tone}`}>
                    <i>{currentPlatform.short}</i>{currentPlatform.name}
                  </span>
                )}
                <button type="button" className="paste-button" onClick={pasteFromClipboard}>粘贴</button>
                <button type="submit" className="resolve-button" disabled={tasks.some((task) => task.status === "analyzing")}>
                  {tasks.some((task) => task.status === "analyzing") ? "解析中…" : "开始解析"}
                </button>
              </div>

              {currentPlatform?.adult && (
                <label className="adult-confirmation">
                  <input
                    type="checkbox"
                    checked={adultConfirmed}
                    onChange={(event) => setAdultConfirmed(event.target.checked)}
                  />
                  <span>我已年满 18 岁，并确认拥有下载和保存此内容的合法授权。</span>
                </label>
              )}

              {currentPlatform?.limitedReason && (
                <p className="platform-warning" role="status">{currentPlatform.limitedReason}</p>
              )}
              {currentPlatform?.name === "Bilibili" && (
                <div className="bili-session-control">
                  <button type="button" onClick={syncBilibiliSession} disabled={biliSyncing}>
                    {biliSyncing ? "同步中…" : "同步 Edge B 站会话"}
                  </button>
                  <span>{biliSessionActive
                    ? "本机会员会话已同步；可重新解析"
                    : helperSupportsBili
                      ? "未同步，当前使用访客画质"
                      : helperVersionReady
                        ? "助手文件已更新，需要在 Edge 中重新加载"
        : `需要 0.3.6 助手${helperVersion ? `（当前 ${helperVersion}）` : "（未检测到）"}；也可从助手弹窗同步`}</span>
                  {biliSessionActive && (
                    <button type="button" onClick={clearBilibiliSession}>断开本机会话</button>
                  )}
                </div>
              )}

              <div className="form-foot">
                <p className={notice ? "notice visible" : "notice"}>{notice || "支持纯链接、分享文案和 Markdown 链接，系统会自动提取。"}</p>
                <p>仅下载你有权保存的内容；不处理 DRM、私密及直播内容</p>
              </div>
              <details className="platform-directory">
                <summary>查看可识别的 {platforms.length} 个网站</summary>
                <p>识别域名不代表每条视频都可下载；实际画质以粘贴单个作品链接后的结果为准。</p>
                <div className="platform-directory-groups">
                  <div><strong>常用及综合</strong><span>{platforms.filter((platform) => !platform.adult).map((platform) => platform.name).join(" · ")}</span></div>
                  <div><strong>需年龄确认</strong><span>{platforms.filter((platform) => platform.adult).map((platform) => platform.name).join(" · ")}</span></div>
                </div>
                {coreOnline === false && <p>抖音、快手当前专用核心未就绪；其他平台仍可单独解析。</p>}
              </details>
            </form>

            <section className="queue-panel">
              <div className="queue-toolbar">
                <div><h2>下载队列</h2><span>{filteredTasks.length} 个任务</span></div>
                <label className="task-search">
                  <span>⌕</span>
                  <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索任务" />
                </label>
              </div>

              <div className="mobile-filters" aria-label="移动端任务分类">
                {filterItems.map((item) => (
                  <button key={item.key} className={filter === item.key ? "active" : ""} onClick={() => setFilter(item.key)}>
                    {item.label}<span>{counts[item.key]}</span>
                  </button>
                ))}
              </div>

              <div className="task-list">
                {filteredTasks.length === 0 ? (
                  <div className="empty-queue">
                    <span className="empty-icon">↓</span>
                    <h3>{tasks.length ? "没有匹配的任务" : "还没有下载任务"}</h3>
                    <p>{tasks.length ? "换个关键词或任务分类试试。" : "在上方粘贴视频链接，解析结果会出现在这里。"}</p>
                    {!tasks.length && <button onClick={() => inputRef.current?.focus()}>创建第一个任务</button>}
                  </div>
                ) : filteredTasks.map((task) => (
                  <div
                    key={task.id}
                    className={`task-row ${selectedId === task.id ? "selected" : ""}`}
                    onClick={() => setSelectedId(task.id)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        setSelectedId(task.id);
                      }
                    }}
                    role="button"
                    tabIndex={0}
                  >
                    <div className={`task-thumb ${task.thumbnail ? "has-image" : ""}`}>
                      {task.thumbnail
                        ? <img src={task.thumbnail} alt="" referrerPolicy="no-referrer" />
                        : <span className={`tone-${task.platform.tone}`}>{task.platform.short}</span>}
                      <i>{task.duration}</i>
                    </div>
                    <div className="task-copy">
                      <div className="task-title-line">
                        <h3>{task.title}</h3>
                        <span className={`status status-${task.status}`}>
                          {(task.status === "analyzing" || task.status === "preparing") && <i />}{statusText[task.status]}
                        </span>
                      </div>
                      <p>
                        <span className={`mini-platform tone-${task.platform.tone}`}>{task.platform.short}</span>
                        {task.platform.name}
                        {task.author && <><b>·</b>{task.author}</>}
                      </p>
                      {task.status === "preparing" && (
                        <div className="task-progress">
                          <div className="task-progress-head">
                            <span>{Math.round(downloadProgress[task.id]?.progress ?? 0)}%</span>
                            <small>{formatSpeed(downloadProgress[task.id]?.speed ?? null)} · 剩余 {formatEta(downloadProgress[task.id]?.eta ?? null)}</small>
                          </div>
                          <span className="task-progress-track">
                            <i style={{ width: `${Math.max(2, downloadProgress[task.id]?.progress ?? 0)}%` }} />
                          </span>
                        </div>
                      )}
                      {task.error && <small className="task-error">{task.error}</small>}
                    </div>
                    <div className="task-row-actions">
                      {(task.status === "failed" || task.status === "expired") && (
                        <button onClick={(event) => { event.stopPropagation(); retryTask(task); }}>重试</button>
                      )}
                      {(task.status === "ready" || task.status === "prepared") && (
                        <button className="quick-download" onClick={(event) => { event.stopPropagation(); handleDownloadClick(task); }}>{task.status === "prepared" ? "保存" : "下载"}</button>
                      )}
                      <button className="remove-task" aria-label="删除任务" onClick={(event) => { event.stopPropagation(); removeTask(task.id); }}>×</button>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          </section>

          <aside className="inspector">
            {selectedTask ? (
              <>
                <div className="inspector-heading">
                  <div><span className="eyebrow">任务详情</span><h2>下载选项</h2></div>
                  <button aria-label="关闭详情" onClick={() => setSelectedId(null)}>×</button>
                </div>

                <div className={`preview ${selectedTask.thumbnail ? "has-image" : ""}`}>
                  {selectedTask.thumbnail
                    ? <img src={selectedTask.thumbnail} alt="视频封面" referrerPolicy="no-referrer" />
                    : <span className={`tone-${selectedTask.platform.tone}`}>{selectedTask.platform.short}</span>}
                  <div className="preview-overlay"><span>{selectedTask.duration}</span></div>
                </div>

                <div className="inspector-title">
                  <h3>{selectedTask.title}</h3>
                  <p><span className={`mini-platform tone-${selectedTask.platform.tone}`}>{selectedTask.platform.short}</span>{selectedTask.platform.name}{selectedTask.author ? ` · ${selectedTask.author}` : ""}</p>
                </div>
                {selectedTask.platform.name === "Bilibili" && maxVideoHeight(selectedTask.formats) > 0 && maxVideoHeight(selectedTask.formats) <= 480 && (
                  <p className="platform-warning" role="status">此任务只解析到 {maxVideoHeight(selectedTask.formats)}P。要提高真实画质，请在上方同步本机 Edge B 站会话并重新解析；放大旧文件不会增加原始清晰度。</p>
                )}

                {selectedTask.status === "analyzing" ? (
                  <div className="inspector-state"><span className="large-spinner" /><h3>正在解析视频</h3><p>正在读取公开视频信息与可用格式，请稍候。</p></div>
                ) : selectedTask.status === "preparing" ? (
                  <div className="download-progress-card">
                    <div className="download-progress-heading">
                      <div><span className="live-dot" /><h3>正在下载文件</h3></div>
                      <strong>{downloadProgress[selectedTask.id]?.total_bytes || (downloadProgress[selectedTask.id]?.progress ?? 0) > 0 ? `${Math.round(downloadProgress[selectedTask.id]?.progress ?? 0)}%` : "—"}</strong>
                    </div>
                    <div
                      className="download-progress-track"
                      role="progressbar"
                      aria-label="下载进度"
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={Math.round(downloadProgress[selectedTask.id]?.progress ?? 0)}
                    >
                      <i style={{ width: `${Math.max(1, downloadProgress[selectedTask.id]?.progress ?? 0)}%` }} />
                    </div>
                    <div className="download-progress-meta">
                      <div><span>速度</span><strong>{formatSpeed(downloadProgress[selectedTask.id]?.speed ?? null)}</strong></div>
                      <div><span>剩余时间</span><strong>{formatEta(downloadProgress[selectedTask.id]?.eta ?? null)}</strong></div>
                    </div>
                    <p>{downloadProgress[selectedTask.id]?.status === "queued" ? "正在等待可用下载通道…" : `已接收 ${formatFileSize(downloadProgress[selectedTask.id]?.downloaded_bytes ?? 0)}${downloadProgress[selectedTask.id]?.total_bytes ? ` / ${formatFileSize(downloadProgress[selectedTask.id].total_bytes)}` : " · 总大小待确认"}。实际速度取决于源站与网络；完成后保存到“下载内容”。`}</p>
                  </div>
                ) : selectedTask.status === "failed" || selectedTask.status === "expired" ? (
                  <div className="inspector-state error-state"><span>!</span><h3>{statusText[selectedTask.status]}</h3><p>{selectedTask.error || "临时下载链接已过期，请重新解析。"}</p><button onClick={() => retryTask(selectedTask)}>重新解析</button></div>
                ) : selectedTask.status === "sent" && (selectedTask.coreAssisted || selectedTask.helperAssisted) && !selectedTaskHasGallery ? (
                  <div className="inspector-state helper-complete">
                    <span>✓</span>
                    <h3>已保存到“下载内容”文件夹</h3>
                    <p>文件已经直接写入当前项目文件夹，无需浏览器扩展，也不会跳转到平台页面。</p>
                    <div className="helper-complete-actions">
                      <button onClick={() => void resolveCoreTask(selectedTask.url, selectedTask.platform, selectedTask.id)}>再次下载视频</button>
                      {selectedTask.formats.some((format) => format.kind === "图片") && (
                        <button className="image-download-button" onClick={() => handleImageDownloadClick(selectedTask)}>{imageDownloadLabel(selectedTask.formats)}</button>
                      )}
                    </div>
                  </div>
                ) : (
                  <>
                    {renderImagePicker(selectedTask)}
                    <label className="quality-picker">
                      <span><small>下载设置</small><strong>{selectedTask.platform.name === "海角网" ? "选择视频段落" : selectedTaskHasGallery ? "选择视频画质" : "选择画质或图片"}</strong></span>
                      <select
                        aria-label="选择画质"
                        value={selectedTaskDisplayFormatId}
                        disabled={selectedTask.status === "prepared"}
                        onChange={(event) => updateTask(selectedTask.id, { selectedFormat: event.target.value })}
                      >
                        {selectedTaskDisplayFormats.map((format) => (
                          <option key={format.id} value={format.id}>{format.label} · {format.size}{format.detail.includes("推荐") ? "（推荐）" : ""}</option>
                        ))}
                      </select>
                    </label>
                    {selectedTask && maxVideoHeight(selectedTaskDisplayFormats) > 0 && (
                      <div className="quality-shortcuts" aria-label="画质快捷选择">
                        <button
                          type="button"
                          aria-pressed={selectedTaskDisplayFormatId === selectedTaskBestFormatId}
                          disabled={selectedTask.status === "prepared"}
                          onClick={() => updateTask(selectedTask.id, { selectedFormat: selectedTaskBestFormatId })}
                        >选最高分辨率</button>
                        {selectedTaskQuickFormat && selectedTaskQuickFormat.id !== selectedTaskBestFormatId && (
                          <button
                            type="button"
                            aria-pressed={selectedTaskDisplayFormatId === selectedTaskQuickFormat.id}
                            disabled={selectedTask.status === "prepared"}
                            onClick={() => updateTask(selectedTask.id, { selectedFormat: selectedTaskQuickFormat.id })}
                          >选快速版本</button>
                        )}
                      </div>
                    )}
                    {selectedTaskDisplayFormat && (
                      <div className="selected-format-summary" role="status">
                        <strong>当前选择：{selectedTaskDisplayFormat.label}</strong>
                        <span>{selectedTaskDisplayFormat.size} · {selectedTaskDisplayFormat.detail}</span>
                        {selectedTaskDisplayFormat.detail.includes("AI 放大") && <span>此档由平台 AI 放大生成，不等于原生同分辨率画质；可改选下方未标 AI 的档位。</span>}
                      </div>
                    )}
                    <p className="quality-availability-note">
                      {selectedTask.platform.name === "海角网"
                        ? "此页可能包含多段不同内容；请按时长选择要保存的片段。网站未提供清晰度列表，无法凭空提高原视频画质。"
                        : selectedTask.platform.name === "Bilibili"
                        ? "仅在 B 站向当前账号返回可保存、无 DRM 的 2160P 媒体流时显示 4K；播放器菜单有 4K 选项不等于该链接可下载 4K。"
                        : maxVideoHeight(selectedTask.formats) === 0
                        ? "此链接未返回可核实的分辨率，界面只显示站点提供的公开媒体选项；不能据此宣称支持 1080P 或 4K。"
                        : "默认选择本次解析实际提供的最高分辨率；高分辨率不保证更清晰，请结合编码和 AI 放大标记选择。不处理 DRM 或需要登录的媒体。"}
                      大型文件需预留足够磁盘空间。
                    </p>
                    <div className="format-heading"><h3>{selectedTaskHasGallery ? "选择视频或音频" : "选择视频、音频或图片"}</h3><span>{selectedTaskDisplayFormats.length} 个可用</span></div>
                    <div className="format-list">
                      {selectedTaskDisplayFormats.map((format) => (
                        <label key={format.id} className={selectedTaskDisplayFormatId === format.id ? "selected" : ""}>
                          <input
                            type="radio"
                            name={`format-${selectedTask.id}`}
                            checked={selectedTaskDisplayFormatId === format.id}
                            disabled={selectedTask.status === "prepared"}
                            onChange={() => updateTask(selectedTask.id, { selectedFormat: format.id })}
                          />
                          <span className="radio-mark" />
                          <span className="format-copy"><strong>{format.label}{format.id === selectedTaskBestFormatId && videoHeight(format) > 0 && <em>最高分辨率{format.detail.includes("AI 放大") ? " · AI 放大" : ""}</em>}</strong><small>{format.detail}</small></span>
                          <b>{format.size}</b>
                        </label>
                      ))}
                    </div>
                    <div className="download-action-row">
                      <button className="download-button" onClick={() => handleDownloadClick({ ...selectedTask, selectedFormat: selectedTaskDisplayFormatId })}>
                        <span>↓</span>{selectedTask.status === "prepared" ? "保存到下载内容" : selectedTask.status === "sent" ? "再次下载" : "下载所选格式"}
                      </button>
                      {!selectedTaskHasGallery && selectedTask.formats.some((format) => format.kind === "图片")
                        && selectedTask.formats.find((format) => format.id === selectedTask.selectedFormat)?.kind !== "图片" && (
                          <button className="image-download-button" onClick={() => handleImageDownloadClick(selectedTask)}>
                            {imageDownloadLabel(selectedTask.formats)}
                          </button>
                        )}
                    </div>
                    <p className="download-note">点击后实时显示下载进度，完成后直接保存到当前项目的“下载内容”文件夹。</p>
                  </>
                )}

                <div className="inspector-actions">
                  <button onClick={() => retryTask(selectedTask)}>↻ 重新解析</button>
                  <button onClick={() => removeTask(selectedTask.id)}>⌫ 删除任务</button>
                </div>
              </>
            ) : (
              <div className="empty-inspector">
                <span>↙</span>
                <h2>选择一个任务</h2>
                <p>任务的封面、格式与下载操作会显示在这里。</p>
              </div>
            )}
          </aside>
        </div>
      </section>
    </main>
  );
}
