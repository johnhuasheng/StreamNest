# StreamNest 平台验收记录

## 2026-09-28 当前平台与画质巡检

本轮新增网页内可展开的 31 站识别清单、最高分辨率／快速版本快捷选择，并修正两个“网页识别、后端不接受”的备用网址。分辨率未知的链接不再被描述成“默认最高分辨率”。只解析、不下载的巡检覆盖 30 条公开样本：20 条返回视频选项、1 条仅返回音频、9 条失败；小红书尚缺可公开读取的单篇笔记样本。原始逐站画质及错误见 `work/quality-audit-2026-09-28T15-16-05.952Z.json`，这不是各平台的固定最高画质，也不代表 20 条视频已完整下载。红果短剧另有本轮完整视频与图片验收，见下文。

本轮样本返回明确 1080P 的站点为 YouTube、微博、AcFun、XVideos、Rule34Video、HQPorner、Twitch、Pinterest；返回 720P 的有 X、Reddit、Pornhub、Eporner、SxyPrn、Instagram；Facebook 为 480P、XNXX 为 418P。TED 只标 1200 kbps；红果短剧、XMoviesForYou、海角网没有可核实的分辨率标签。Bilibili 当前本机会话未同步且样本解析失败，不能用历史 4K 成功替代本轮结果。Beeg 当前样本未给 HLS，TikTok、Dailymotion 样本没有返回可用视频，Vimeo 样本没有公开普通 MP4，西瓜视频要求官方临时访客会话，SpankBang 返回 Cloudflare 403；抖音、快手的独立核心目录缺失。小红书仍需有效单篇链接验证。以上失败只针对本轮样本和当前环境，不推断全部作品永久不可下载。

## 2026-09-28 视频下载线路稳定性复核

本轮将通用视频链路的 HTTP 重试设为有限 4 次、HLS 片段重试设为有限 5 次，并禁止忽略失败片段。公开页面给出的短时效媒体地址发生明确 403/404/410 失效时，只允许重新解析同一视频、同一格式 ID 与画质标签；不同画质或不同段落不会自动顶替。HQPorner/SxyPrn 直连 MP4 新增 `Content-Range` 起止与实际字节数校验，抖音/快手浏览器助手媒体新增已知 `Content-Length` 完整性校验。以上只改善失败恢复和成品完整性，不产生源站未提供的画质或登录权限。

在隔离的新版解析服务上，对 28 个已有公开样例执行**只解析、不下载**巡检：21 个返回了视频/音频选项；7 个未通过。失败分别为 SpankBang 的 Cloudflare 人机验证、海角网页面本轮临时不可读、Dailymotion 公开源失效、SoundCloud 响应超时、Vimeo 样例没有公开普通 MP4，以及抖音/快手独立核心离线。B 站本机会员会话未启用，所以此次仅列出 480P，不代表账号支持的最高画质。逐项结果见 `work/quality-audit-2026-09-28T00-34-00.609Z.json`。

随后在已运行的正式 8788 服务上单独重试同一海角网详情页，已恢复解析并默认列出较长的第 2 段；结果见 `work/quality-audit-2026-09-28T02-56-59.905Z.json`。这是解析恢复，不是第 2 段完整下载验收。

新版下载链路另完成 YouTube 360P 和 TikTok 公开画质的**完整视频及封面下载**：两站均返回可读取的 MP4/JPEG，文件头与 HTTP 206 检查通过，结果见 `work/platform-validation-results-tiktok-youtube.json`。切换过期直链时任务进度会从零重新计算，避免显示上一个失败线路的字节数；后端单元测试 141 项通过。HQPorner 1080P 测试源约 396 MB、当前节点速度较慢，本轮在约 17.5% 时停止隔离验收；它不计入新版完整下载通过。其余 26 个样例仅有解析结论或历史验收，不能据此声称这次全部完整下载通过。

## 2026-09-27 最高画质核查

这轮按“本次公开解析实际返回的最高可用画质”选择，不能把低清源文件放大后称为原生高清。26 个现有平台样例中，20 个返回了视频或音频格式，6 个未能解析：SpankBang 的人机验证、Dailymotion 的公开媒体 403、SoundCloud 的 TLS/解析失败、Vimeo 样例无公开普通 MP4，以及抖音和快手的本地核心目录缺失。解析通过不等于完整下载通过，以下为本轮新做的真实文件验证：

| 平台 | 本次最高可用选项 | 完整下载与文件检查 |
| --- | --- | --- |
| YouTube | 1080P | 视频和封面通过；MP4/JPEG 文件头、HTTP 206 通过 |
| X | 720P 竖屏（实际 720×1280） | 视频和封面通过；网页点击后本机保存的 MP4 经 `ffprobe` 和解码检查 |
| Reddit | 720P 竖屏 | 视频和首帧 JPG 通过；MP4/JPEG 文件头、HTTP 206 通过 |
| Beeg | 1080P | 视频和封面通过；MP4/WebP 文件头、HTTP 206 通过 |
| TED | 1200 kbps（页面只给码率，未虚构分辨率） | 视频和封面通过；MP4/JPEG 文件头、HTTP 206 通过 |
| Eporner | 720P | 视频和封面通过；MP4/JPEG 文件头、HTTP 206 通过 |
| XNXX | 418P（样例最高） | 视频和封面通过；MP4/JPEG 文件头、HTTP 206 通过 |
| Rule34Video | 1080P | 视频和封面通过；MP4/JPEG 文件头、HTTP 206 通过 |
| TikTok | 576P 竖屏 | 视频和封面通过；MP4/JPEG 文件头、HTTP 206 通过 |
| 微博 | 1080P | 视频和封面通过；MP4/JPEG 文件头、HTTP 206 通过 |
| Facebook | 480P 竖屏 | 视频和封面通过；MP4/JPEG 文件头、HTTP 206 通过 |
| AcFun | 1080P 竖屏 | 视频和封面通过；MP4/JPEG 文件头、HTTP 206 通过 |
| XVideos | 1080P | 视频和封面通过；MP4/JPEG 文件头、HTTP 206 通过 |
| Pornhub | 720P | 2026-09-27 23:59 再次验证通过；视频和封面完整准备、MP4/JPEG 文件头、HTTP 206 通过；此前同画质一次失败，仍需观察稳定性 |

以上十四站验收结果保存在 `work/platform-validation-results-reddit-x-youtube-highest.json`、`work/platform-validation-results-beeg-highest.json`、`work/platform-validation-results-rule34video-xnxx-highest.json`、`work/platform-validation-results-eporner-ted-highest.json`、`work/platform-validation-results-instagram-pinterest-tiktok-twitch-highest.json`、`work/platform-validation-results-facebook-微博-highest.json`、`work/platform-validation-results-acfun-pornhub-highest.json`、`work/platform-validation-results-sxyprn-xvideos-highest.json` 和 `work/platform-validation-results-pornhub-highest.json`。混合结果文件中的 Pornhub 早期失败和 SxyPrn 失败仍保留，不能把整个文件误记为全通过。B 站 4K 是单独依照用户主动同步的本机 B 站会话验证，不能推广为其他平台登录能力。其余可解析平台目前只完成画质列表检查，不能标注为“最高画质已完整下载”。HQPorner 的 1080P 公开 MP4 已通过 1,024 字节 HTTP Range 检查，但约 396 MB 的长文件未在本轮 12 分钟旧验收窗口内完成；测试脚本现已改为最高画质最多等待 30 分钟，并新增短时效直链续接逻辑，完整 1080P 仍待复验。XMoviesForYou 当前仅返回自适应 HLS，无法从当前解析结果声明固定 1080P 或 4K。

失败与延迟样例不隐瞒：Instagram 可列出 720P 但实际下载失败；Twitch 当前样例未返回可用视频。Pinterest 的自动验收在旧的 12 分钟窗口内超时，但后台稍后写完了 18,403,864 字节的 1080×1920 MP4；本机 `ffprobe` 确认 H.264 视频、AAC 音频、57.7 秒，`ffmpeg` 整段解码通过。Pinterest 因此已有最高画质视频本体验收，但本轮图片和自动交付未完整复验，不并入上表十四站的端到端通过数。Pornhub 720P 在先前的 `work/platform-validation-results-acfun-pornhub-highest.json` 中失败，同一公开样例的 480P 视频与图片在 `work/platform-validation-results-pornhub.json` 完整通过；再次进行 720P 验收时，约 65 MB 视频与封面已在 `work/platform-validation-results-pornhub-highest.json` 通过完整准备和文件头检查，因此现在可以选择 720P，但不保证短时效媒体地址每次都稳定。SxyPrn 720P 的首轮任务在刷新公开页面时失败，第二轮下载当前约 650 MB 的样例超过 30 分钟，验收脚本最后记录 17.9%（`work/platform-validation-results-sxyprn-highest.json`）；另行验证公开 MP4 Range 可返回真实字节，但完整 720P 尚未通过。Dailymotion、SoundCloud、Vimeo、SpankBang 和缺失本地核心的抖音/快手也不能据本轮结果声称最高画质可用。

通用平台现在会优先选解析列出的最高分辨率，同分辨率比较码率；如快速合并流与高码率分离流不同，会保留两种选项。只有码率、没有分辨率的选项保留解析器的原始排序，不会误选较低码率。国内核心如果直接标为“4K”而不是“2160P”，现在也会正确优先于 1080P；这属于选择规则修复，不代表当前离线核心或任何源站已经提供 4K。竖屏以短边标注，并在详情写出原始宽高。通用单文件默认上限提高到 8 GiB，但这不会增加源站本身提供的清晰度。长期下载的临时文件以最近写入时间判断是否仍在使用，避免只按目录创建时间误清理。

最近一次完整验收：2026-08-23 06:20（Asia/Shanghai），18/18 个目标平台通过。

新增 Eporner 单项验收：2026-08-28 21:17（Asia/Shanghai），视频与封面图片 1/1 通过；完整结果保存在 `work/platform-validation-results-eporner.json`。

新增 XNXX 与 Rule34Video 单项验收：2026-08-29 02:13（Asia/Shanghai），视频与封面图片 2/2 通过；实际 MP4/JPEG 文件头、响应类型和非空内容均已检查，结果保存在 `work/platform-validation-results-rule34video-xnxx.json`。

XNXX 搜索结果链接补充验收：2026-08-29 08:12（Asia/Shanghai），`/search/sex?top&id=25752905` 已能按照页面中的精确 `data-id` 安全转换为同站视频详情页；实际下载 290P MP4 与 JPEG 封面均返回 HTTP 206，文件头分别为 `ftypisom` 与 `JFIF`。没有 `id` 的普通搜索列表仍会被拒绝，避免误下载列表中的其他视频。

新增 Beeg 单项验收：2026-08-29 03:44（Asia/Shanghai），公开视频 240P 与封面原图 1/1 通过；服务端完成 HLS 下载与 MP4 合并后，实际 MP4/WebP 文件头、响应类型和非空内容均已检查，结果保存在 `work/platform-validation-results-beeg.json`。

新增 SxyPrn 单项验收：2026-08-29 07:38（Asia/Shanghai），约 180 MB 的公开视频 720P 与封面原图 1/1 通过；实际 MP4/JPEG 文件头、响应类型和非空内容均已检查，结果保存在 `work/platform-validation-results-sxyprn.json`。

HQPorner 单项复验：2026-09-22（Asia/Shanghai），单个 `/hdporn/...html` 视频页面的 360P 完整 MP4 与视频首帧均通过真实下载检查。首页、分类页和搜索列表现在会在联网前直接提示“请打开单个视频”，不会再笼统显示解析失败或误选页面中的任意视频。

SpankBang 指定样例复验：2026-09-22（Asia/Shanghai），`/98t61/video/porn`、同编号嵌入页、手机版页面以及最新版 yt-dlp 的公开访问均返回 Cloudflare HTTP 403 人机验证。应用现在会明确说明“链接格式正确，但站点要求人机验证”；遵守安全范围，不读取浏览器 Cookie、不绕过 Cloudflare 或验证码，因此不能把该站当前状态标记为可直接下载。

8 平台解析复验：2026-09-22（Asia/Shanghai），Eporner、XNXX、Rule34Video、HQPorner、Beeg 与 XMoviesForYou 均返回视频和图片格式；SxyPrn 的旧验收作品已经删除，已从网站当前公开首页选择仍存在且较短的 `/post/6ab1c24c3e748.html` 作为新回归样例；SpankBang 仍是上述 Cloudflare 403 限制。

长视频下载优化：XMoviesForYou 当前只公开一条 HLS 清晰度，首页样例普遍超过 25 分钟。下载器已把该站分段并发从 4 提升到有上限的 8，并将回归样例切换为当前首页中较短的一条；SxyPrn 回归样例由 15:55 更换为 08:33。解析成功与首段字节成功不会冒充完整长视频下载通过。

慢节点稳定性优化：HQPorner 与 SxyPrn 的可续传直连分段由 4 MB 调整为 1 MB。两个站点的 CDN 在慢节点上可能先缓存完整 Range 才返回字节；较小分段可以更早显示进度，并降低单段超时后重复下载的成本，完整文件大小校验保持不变。

本轮 8 站当前结果（2026-09-22）：

| 平台 | 当前联网证据 | 结论 |
| --- | --- | --- |
| Eporner | 480P MP4 与封面图片完整下载、文件头通过 | 可用 |
| XNXX | 搜索结果链接转换后，418P MP4 与封面图片完整下载、文件头通过 | 可用 |
| Rule34Video | 480P MP4 与封面图片完整下载、文件头通过 | 可用 |
| HQPorner | 360P MP4 与视频首帧完整下载、文件头通过；短时效直链自动刷新 | 单视频页可用 |
| Beeg | 240P MP4 与封面图片完整下载、文件头通过 | 可用 |
| SxyPrn | 当前公开视频返回 HTTP 206、`video/mp4`、`ftypisom`；封面 JPEG 返回 JFIF 文件头 | 媒体可读，完整长视频速度受当前 CDN/本机代理链路影响 |
| SpankBang | 指定单视频页、嵌入页、手机版页及最新版解析器均返回 Cloudflare 403 | 当前不能在无 Cookie、无验证绕过模式直接下载 |
| XMoviesForYou | HLS 清单与首个 MPEG-TS 分段返回 HTTP 206，188 字节同步位均为 `0x47`；封面 WebP 文件头通过 | 媒体可读，长视频已启用 8 路受控分段 |

本轮完整下载结果保存在 `work/platform-validation-results-beeg-eporner-rule34video-xnxx.json` 和 `work/platform-validation-results-hqporner.json`。SxyPrn 与 XMoviesForYou 的“首段/图片通过”没有记作完整长视频下载通过。

同日先对 HQPorner、SxyPrn 与 XMoviesForYou 做了实时公开流验证：三个站点均成功解析视频与图片；HQPorner、SxyPrn 的 MP4 和 XMoviesForYou 的 HLS 清单均成功读取 1,024 字节。随后 SxyPrn 已升级为完整下载验收；HQPorner 与 XMoviesForYou 仍保留为“公开流验证”，不计入完整下载通过数。

验收不是只检查“能够解析”。每个平台都完成了以下链路：

1. 解析公开内容链接并读取媒体信息。
2. 选择视频格式，等待服务器完成下载，并以 HTTP Range 请求检查生成的 MP4 文件。
3. 选择图片格式，检查实际保存的文件、图片魔数和非空文件大小。
4. 图片没有公开封面时，从已下载视频安全生成 JPEG 首帧。

| 平台 | 视频/音频 | 图片 | 图片来源 |
| --- | --- | --- | --- |
| YouTube | 通过 | 通过 | 封面原图 |
| Bilibili | 通过 | 通过 | 封面原图（自动升级为 HTTPS） |
| X | 通过 | 通过 | 封面原图 |
| TED | 通过 | 通过 | 封面原图 |
| 微博 | 通过 | 通过 | 封面原图 |
| Facebook | 通过 | 通过 | 封面原图 |
| Reddit | 通过 | 通过 | 视频首帧 |
| AcFun | 通过 | 通过 | 封面原图 |
| XVideos | 通过 | 通过 | 封面原图（含 AVIF） |
| Pornhub | 通过 | 通过 | 封面原图 |
| Eporner | 通过 | 通过 | 封面原图 |
| XNXX | 通过 | 通过 | 封面原图 |
| Rule34Video | 通过 | 通过 | 封面原图 |
| Beeg | 通过 | 通过 | 封面原图 |
| SxyPrn | 通过 | 通过 | 封面原图 |
| 抖音 | 通过 | 通过 | 封面原图 |
| 快手 | 通过 | 通过 | 视频首帧 |
| Dailymotion | 视频通过 | 通过 | 封面原图 |
| TikTok | 视频通过 | 通过 | 封面原图 |
| Instagram | 视频通过 | 通过 | 封面原图 |
| Twitch | 视频通过 | 通过 | 封面原图 |
| SoundCloud | MP3 通过 | 通过 | 封面原图 |
| Pinterest | 视频通过 | 通过 | 封面原图 |

| 公开流验证平台 | 视频流 | 图片 | 当前状态 |
| --- | --- | --- | --- |
| HQPorner | 360P 完整下载与 MP4 文件头通过 | 视频首帧真实下载通过 | 单视频页可用；首页/列表页会提示复制单个视频地址 |
| XMoviesForYou | HLS 清单读取通过 | 解析通过 | 可用，尚未为验收保存完整长视频 |
| SpankBang | 未通过 | 未验证 | 指定单视频页当前返回 Cloudflare 403；明确提示而不绕过、不读取 Cookie |

2026-09-28 海角网单篇 `https://www.hjw01.com/archives/194451/` 复验：公开页面解析出两段 HLS（约 2:59、19:18）；第一段经本机服务完整下载到项目 `下载内容/`，FFprobe 检查为约 179 秒、720×1280 H.264 视频 + AAC 音频，文件约 53 MB。第二段仅完成清单与公开播放密钥读取，尚未完整下载；该站页面偶尔返回 403，不能据此保证每次都成功。`hjwang18/19/20/21.com` 当前返回脚本跳转到未核验的其他域名，`hjw2026.com` 是线路发布页；这些入口仅可识别，未通过视频下载验收。图片尚未验收。

新增平台验证同时覆盖了中文项目路径下的 HTTPS 证书兼容性，以及 SoundCloud 在同等音质下优先使用稳定直连音频。Vimeo 只会接受公开播放器明确提供的普通 MP4；当前实测样例只返回受保护的分段媒体，因此会明确拒绝。西瓜视频要求官方页面生成访客会话，小红书公开测试页没有返回视频格式；这些情况都会显示具体中文原因，StreamNest 不会读取浏览器 Cookie，也不会绕过登录或 DRM。

2026-09-28 小红书补充：现有视频提取器对图文笔记返回“无视频格式”，现已在这种情况下读取单篇笔记公开页面的 `imageList`，生成单张图片和全部图片 ZIP 选项；图片 CDN 限定为小红书使用的域名，测试覆盖顺序、恶意外域拒绝、视频笔记不伪装成图文、下载凭证和自选 ZIP。后端 148 项测试及网页构建、类型检查、Lint 通过。当前可找到的两个公开示例链接在本机均未返回具体笔记数据，故**尚未完成小红书真实媒体字节验收**；需要一条用户当前可直接打开的公开单篇笔记链接复测。识别到域名和通过模拟测试不等于保证任意笔记可下载。

2026-09-28 红果短剧单集验收：用户提供的 `https://hongguoduanju.com/player/7684649550316833817` 经统一解析接口返回《东北婆婆，笑护全家》第 1 集（02:39）、一条公开 MP4 和一张封面。视频通过 StreamNest 本机任务完整下载并保存在 `下载内容/1-7684649550316833817.mp4`，25,185,612 字节；FFprobe 检查为 159.67 秒、720×1280 H.264 视频 + AAC 音频。封面保存为 `下载内容/东北婆婆，笑护全家 第1集-cover.jpg`，117,397 字节、640×914 JPEG。该站公开格式目前未标分辨率，列表显示“站点公开画质”，不承诺其他集数或更高画质。首页和剧集列表被明确要求换成单集链接；未读取该站 Cookie，也未绕过登录或 DRM。

浏览器界面也完成了端到端检查：YouTube 与 Pinterest 均在统一页面保存“封面原图”，其中 Pinterest 验证了从视频画质一键切换并执行“下载图片”；快手在没有公开封面的情况下，也通过同一个按钮自动选择“视频首帧”，从视频生成并保存了可正常打开的 JPG。三个流程都留在 `localhost:3000` 统一页面内，不需要打开新页面或再次点击。最新生产运行组件升级后，又实际保存并检查了 `Me at the zoo-cover (2).jpg`、`Me_at_the_zoo-jNQXAC9IVRw.mp4` 和 `嘻嘻#得物 #运动 (9)-first-frame.jpg`；MP4 包含 H.264 视频与 AAC 音频，图片均可正常打开。

国内平台下载完成或首帧图片交付给浏览器后，内部视频副本会自动清理并在临时失败时重试。06:20 全量验收前后，国内核心旧文件数量保持 17 个、总计 997,832,600 字节，系统 `streamnest-*` 临时目录保持为 0，证明本轮没有新增隐藏媒体副本。

完整验收可用 `npm run test:platforms` 重复执行。它会真实下载每个平台的低流量媒体与图片，验证响应类型和文件头，并把通过数、失败数和带时间戳的逐平台结果保存到 `work/platform-validation-results.json`。也可在命令后添加平台名进行单项回归；单项结果会保存到带平台名的独立文件，不会再覆盖完整验收记录。

## 持续运行验收

2026-08-23 04:37:30–07:00:29（Asia/Shanghai）连续监测统一网页、通用解析服务和抖音/快手核心，共完成 286 次检查。最终三个服务全部健康；期间 4 次为功能升级安排的计划内重启，均由统一启动器自动恢复，未恢复事件为 0。详细机器可读记录保存在 `work/service-monitor-results.json`。

## 安全与适用范围

仅处理用户有权下载、实际可取得的非 DRM 内容。除用户明确授权并主动在本机同步的 B 站会话外，不读取其他平台的浏览器 Cookie，也不会绕过付费、登录、地区、人机验证或平台访问控制。平台页面与接口会变化，验收结果代表上述时间点的实际状态。
