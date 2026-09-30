import type { Metadata, Viewport } from "next";
import "./globals.css";

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000";

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: "StreamNest | 在线视频下载管理器",
  description: "解析公开在线视频链接、选择可用格式，并在一个工作台中管理本机下载任务。",
  openGraph: {
    title: "StreamNest | 在线视频下载管理器",
    description: "粘贴链接、选择格式并管理你有权保存的公开视频。",
    type: "website",
    images: [{ url: "/og-manager.png", width: 1536, height: 1024, alt: "StreamNest 在线视频下载管理器" }],
  },
  twitter: {
    card: "summary_large_image",
    title: "StreamNest | 在线视频下载管理器",
    description: "粘贴链接、选择格式并管理你有权保存的公开视频。",
    images: ["/og-manager.png"],
  },
};

export const viewport: Viewport = {
  themeColor: "#171a20",
  colorScheme: "light",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
