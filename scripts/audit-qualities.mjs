import fs from 'node:fs/promises';
import path from 'node:path';
import { preferredFormatId, maxVideoHeight } from '../app/quality.ts';

// Public sample pages only. This audit inspects returned choices; it never
// starts a download or records a download ticket, media URL, or login data.
const samples = [
  ['YouTube', 'https://www.youtube.com/watch?v=YE7VzlLtp-4'],
  ['Bilibili', 'https://www.bilibili.com/bangumi/play/ep826497'],
  ['红果短剧', 'https://hongguoduanju.com/player/7684649550316833817'],
  ['西瓜视频', 'https://m.ixigua.com/video/6870484788273218055'],
  ['X', 'https://x.com/defiprincess/status/2091206304647807362'],
  ['TED', 'https://www.ted.com/talks/tim_urban_inside_the_mind_of_a_master_procrastinator'],
  ['微博', 'https://m.weibo.cn/status/5334824403343686'],
  ['Facebook', 'https://www.facebook.com/reel/1195289147628387'],
  ['Reddit', 'https://www.reddit.com/r/Unexpected/comments/1cl9h0u/the_insurance_claim_will_be_interesting/'],
  ['AcFun', 'https://www.acfun.cn/v/ac35457073'],
  ['XVideos', 'https://www.xvideos.com/video.omikotkd693/she_let_me_rub_her_pussy_i_came_inside_her', true],
  ['Pornhub', 'https://www.pornhub.com/view_video.php?viewkey=648719015', true],
  ['Eporner', 'https://www.eporner.com/video-yXwtdO6tsKx/', true],
  ['XNXX', 'https://www.xnxx.com/search/sex?top&id=25752905', true],
  ['Rule34Video', 'https://rule34video.com/video/4573701/wednesday-strategy-meeting/', true],
  ['HQPorner', 'https://hqporner.com/hdporn/127621-lets_get_into_the_male_anatomy.html', true],
  ['Beeg', 'https://beeg.com/-0547745029333362', true],
  ['SxyPrn', 'https://sxyprn.com/post/6ab7d17a66c8d.html', true],
  ['SpankBang', 'https://spankbang.com/98t61/video/porn', true],
  ['XMoviesForYou', 'https://xmoviesforyou.com/fillupmymom-armani-black-my-stepmom-is-hotter-than-before', true],
  ['海角网', 'https://www.hjw01.com/archives/194451/', true],
  ['Dailymotion', 'https://www.dailymotion.com/video/x5kesuj'],
  ['TikTok', 'https://www.tiktok.com/@patroxofficial/video/6742501081818877190?langCountry=en'],
  ['Instagram', 'https://www.instagram.com/reel/Chunk8-jurw/'],
  ['Twitch', 'https://clips.twitch.tv/FaintLightGullWholeWheat'],
  ['SoundCloud', 'https://soundcloud.com/ethmusic/lostin-powers-she-so-heavy'],
  ['Pinterest', 'https://www.pinterest.com/pin/664281013778109217/'],
  ['Vimeo', 'https://vimeo.com/76979871'],
  ['抖音', 'https://v.douyin.com/dFkCjApSoUo/', false, true],
  ['快手', 'https://www.kuaishou.com/short-video/3x2ynp7nvk7ndwq?authorId=3x69ufm6dj97dby', false, true],
];
const chosen = new Set(process.argv.slice(2).map((item) => item.toLowerCase()));
const api = process.env.STREAMNEST_VALIDATION_API || 'http://127.0.0.1:8788';
const output = path.resolve('work', `quality-audit-${new Date().toISOString().replaceAll(':', '-')}.json`);
const results = [];
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

for (const [platform, url, adult = false, core = false] of samples) {
  if (chosen.size && !chosen.has(platform.toLowerCase())) continue;
  const record = { platform, status: 'error', default: null, maxHeight: 0, offered: [], audioOffered: [], error: null };
  try {
    const response = await fetch(`${api}${core ? '/v1/core/resolve' : '/v1/resolve'}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(core ? { url } : { url, adult_confirmed: adult }),
      signal: AbortSignal.timeout(65_000),
    });
    const data = await response.json();
    if (!response.ok || (core && !data.ok)) {
      throw new Error(data?.error?.message || data?.error || data?.detail || `HTTP ${response.status}`);
    }
    const options = core
      ? (data.meta?.formats || []).filter((item) => item.ext?.toLowerCase() === 'mp4').map((item) => ({
          id: item.id, label: item.quality?.toUpperCase() || '公开画质', kind: '视频',
        }))
      : data.formats || [];
    const videoOptions = options.filter((item) => item.kind !== '图片' && item.kind !== '音频');
    const audioOptions = options.filter((item) => item.kind === '音频');
    record.offered = videoOptions.map((item) => item.label);
    record.audioOffered = audioOptions.map((item) => item.label);
    record.maxHeight = maxVideoHeight(videoOptions);
    const chosen = videoOptions.length
      ? options.find((item) => item.id === preferredFormatId(platform, videoOptions))
      : audioOptions[0];
    record.default = chosen?.label || null;
    record.status = videoOptions.length ? 'resolved-video' : audioOptions.length ? 'audio-only' : 'no-video-format';
  } catch (error) {
    record.error = String(error?.message || error).slice(0, 220);
  }
  results.push(record);
  await fs.mkdir(path.dirname(output), { recursive: true });
  await fs.writeFile(output, JSON.stringify({ checkedAt: new Date().toISOString(), results }, null, 2));
  process.stdout.write(`${platform}: ${record.status}, default=${record.default ?? '-'}, max=${record.maxHeight || '-'}\n`);
  // The resolver permits 12 requests/minute. Keep this audit below the limit.
  await delay(5500);
}

process.stdout.write(`结果：${output}\n`);
