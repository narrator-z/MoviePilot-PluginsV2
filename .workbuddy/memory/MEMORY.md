# MoviePilot-PluginsV2 项目长期记忆

## 插件开发硬性规则（踩坑固化）

1. **版本号双字段必须同步**：`__init__.py` 的 `plugin_version`（MP 实际加载/显示/日志用）与 `package.v2.json` 的 `version`（Market 更新比较用）每次发版都要一起改，保持一致。只改其一 → Market 更新后显示版本不变、且反复提示有更新。
2. **本地 `py_compile` 查不出运行期未定义名称**：插件加载失败（如漏 import 枚举、`NameError`）只在 MP 容器实际加载时暴露。改完代码应在容器内验证：
   `docker exec moviepilot-v2 python3 -c "from app.schemas.types import EventType, ChainEventType, ..."`
3. **排查"插件不显示/安装失败/不生效"的事实来源**：直接 SSH 上 NAS 查 `/config/logs/moviepilot.log` 的 `加载插件 X 失败` 行，不要猜网络/镜像/SSH。Market 安装失败时 MP 会把插件备份到 `/config/plugins_backup` 但不装回 `/config/plugins`，故表现为不显示。
4. **容器镜像可能与本地源码版本不一致**：用户容器是 `ghcr.io/narrator-z/moviepilot:latest`（用户自己 fork 构建），导入路径、事件枚举以容器内为准，不要只信本地 `E:\github\MoviePilot`。

## NAS / 部署环境（实测可用）
- SSH：`192.168.31.145:22022`，用户 `narratorz`，密钥 `~/.ssh/id_ed25519_1panel`。
- MoviePilot 容器：`moviepilot-v2`；插件目录（挂载卷）`/config/plugins/<id>/`；备份 `/config/plugins_backup/`；日志 `/config/logs/moviepilot.log`。
- 部署修复文件：本地 scp → `docker cp` 进容器 → `docker restart moviepilot-v2`；冷启动约 30–60s 后查日志。
- 调试技巧：`docker exec moviepilot-v2 python3`（sys.path 含 /config/plugins）可直接 `import neodbsource` 验证；`PluginManager()`/`eventmanager.send_event` 直接 import 拿到的是未初始化实例，无法验证运行时源生成。

## 已发布插件
- `NeoDBSource`（探索/推荐/识别 NeoDB 数据源，v1.0.4）、`ChineseSubFinder`（v6.0.2）、`StuckDownloadGuard`（v1.1.0）、`JackettExtend`、`ProwlarrExtend`、`SiteOpenSignup`。
- 目录名小写 = 类名/key 小写 = 插件 ID；Market 文件列表安装按 `pid.lower()` 匹配目录。

## NeoDBSource 详情页增强（v1.0.4，2026-08-01）
- **演员表**：点击探索页 NeoDB 条目 → 详情页 `media_info` 端点 → `async_recognize_media` → `_recognize_by_neodb_id` 内调用**免 token**的 `GET /api/catalog/{category}/{uuid}/credit/`，映射为 `MediaInfo.actors`（List[dict]，字段 name/character/profile_path）。详情页原生渲染演员表。实测 20 条正常。
- **相似推荐**：`GET /api/catalog/item/{uuid}/similar` **必须 OAuth2 Bearer token**（无 token → 401）。因 `schemas.MediaInfo` 无 recommend/similar 字段，相似条目以文本块 `🎬 NeoDB 相似推荐：` 注入 `mi.overview` 才能在详情页显示。
- 新增配置项 `neodb_token`（密码框）：用户填入自己的 NeoDB OAuth access_token 后解锁相似推荐；留空则只显示演员表。
- 演员 `character` 仅取 `character_name`（为空即空），不回退 role，避免详情页显示 "角色: actor" 这种脏数据。
- 部署：scp → docker cp 到 `/app/app/plugins/neodbsource/`(内置,实际加载) 与 `/config/plugins/neodbsource/`(Market) 双路径 → 清 `__pycache__` → `docker restart moviepilot-v2`。

## 插件：让探索页自定义数据源的条目可点击跳转（关键机制）
- 探索页点击条目 → 媒体详情页 `app/api/endpoints/media.py:media_info` → `parse_media_key("neodb:tv.uuid")` → `async_recognize_media(source="neodb", mediaid="tv.uuid")`。
- **要点**：要使自定义源（如 `neodb:`）的点击识别生效，**必须把 recognize 方法通过 `get_module()` 注册为插件模块**（插件 `_enabled` 时即返回 `recognize_media`/`async_recognize_media`），而**不能**依赖某个「媒体识别」子开关或 ChainBase 补丁——否则子开关未开时核心识别不了该源 → 返回空 → 前端报「未识别到媒体信息」。
- 注册为模块的方法要**只处理本源**（`source=="neodb" and mediaid` 时回查构造 MediaInfo），其余来源返回 None 交还核心，否则会抢在 TMDB/豆瓣之前「赢者通吃」覆盖正常识别（async_run_module 插件模块先于系统模块执行）。
- 同理：MP 图片代理对**公网域名自动放行**（DNS 检查），`SECURITY_IMAGE_DOMAINS` 主要给内网/私有图床用；自定义源封面若是公网域名无需手动加白名单（插件也可在 `init_plugin` 运行时 append 进白名单）。
- NeoDB 条目 `external_resources[]` 的 URL 含 tmdb/douban/imdb 映射，需用正则提取并写入 `MediaInfo.tmdb_id/douban_id/imdb_id`，详情页与订阅才能用；无外链的条目只能浏览详情、订阅需有 tmdb/douban。

## 关键坑位固化（2026-07-31 增补）

1. **用户 fork 镜像内置插件会 shadow Market 副本**：`ghcr.io/narrator-z/moviepilot:latest` 在 `/app/app/plugins/neodbsource/` 自带一份 neodbsource；Market 装的 `/config/plugins/neodbsource/` 与之同名（都 import 为 `app.plugins.neodbsource`），**实际加载的是 `/app/app` 内置那份**。改 Market 副本不生效——任何代码修复必须 docker cp 到 `/app/app/plugins/neodbsource/`（正在加载的），并顺手同步 `/config/plugins/neodbsource/` 保持一致，再清 `__pycache__` + `docker restart moviepilot-v2`。
2. **MediaInfo.year 是 str 不是 int**：详情端点带 response_model 校验，`item_to_mediainfo` 里 `mi.year = int(...)` 会触发 `ResponseValidationError` → HTTP 500（前端"出错啦"）。一律 `mi.year = str(year)`。
3. **MediaType 枚举值是中文**：`MediaType.MOVIE.value == "电影"`，详情端点 `MediaType(type_name)` 要传中文；用 `MOVIE` 测会 ValueError（假 500）。
4. **图片代理已在用户环境正确配置**：`IMAGE_PROXY_ALLOWED_PRIVATE_RANGES=["198.123.0.0/16","fdfe:dcba:9876::/64"]` 覆盖 clash fake-ip；neodb.social / doubanio / fanart 实测均可 fetch。测试时若硬传 `allowed_private_ranges=None` 会误报 non_global_dns_result——真实端点传的是 `settings.IMAGE_PROXY_ALLOWED_PRIVATE_RANGES`。
5. **容器 settings.PROXY=None**：图片代理 `fetch_image` 直连不经代理；fake-ip 的 IPv6 段 `fdfe:dcba:9876::/64` 连接 Errno 101 失败，客户端回退 IPv4（198.123.0.0/16 经 clash 可达）→ 功能正常，无需处理。
6. **列表缩略图优先取 poster_path**：`item_to_mediainfo` 若只设 `cover` 不设 `poster_path`，列表卡片可能显空白；已将 `poster_path` 兜底设为 neodb 封面。

## NeoDB 公开 API 能力边界（OpenAPI schema 实测，2026-07-31）
- **浏览端点只有 `GET /api/trending/{category}/`**：book/game/movie/music/performance/podcast/tv。**trending 全站仅 60 条/类，第 2 页起完全重复**（实测 movie 第2页 0 新增 60 重复）——这是"看不到全部"的真正根因，不是 bug、也不是缺鉴权。
- **不存在 `/api/ranking/`、`/api/discover/`**：schema 里没有，公开访问 404。
- **`/api/catalog/search`**（公开）：`query` 必填 + `category` + `page`，返回 `{count,data,pages}` 真分页每页 20；缺 query→422、空 query→400。只能"搜"不能无关键词浏览。插件已用此端点（neodbhelper.search 传 `query`）。
- **`/api/catalog/gallery/`**（公开）：返回 8 个编辑策展合集完整 item 列表（original_episodes:90、trending_book:60、trending_movie:60、trending_tv:55、trending_game:51、trending_music:40、trending_podcast:28、trending_performance:15）。其中 trending_movie/tv 与 `/api/trending/movie|tv` **同一批 60/55 条**，对影视浏览零增量；original_episodes 是播客。故 gallery 也救不了"看全部"。
- **`/api/catalog/fetch`**（公开，参数 `url`）：按 item URL 取单条详情，非浏览。
- **`/api/catalog/{item_type}/{uuid}/credit/`**（公开）：某条目演职员，可给详情页加"演员表"。
- **`/api/catalog/item/{uuid}/similar`**（**需鉴权 OAuth2 Bearer**）：相似推荐，可给详情页加"相关推荐"。
- **鉴权方式**：标准 OAuth2（无个人令牌一键生成）：`POST /api/v1/apps` 拿 client_id/secret → 浏览器 `/oauth/authorize` → `/oauth/token` 换 token。鉴权只开放 `me/tag/`（你自己的标签）与 `similar`，**不提供任何 ranking/discover/genre/年代 浏览**。
- **结论**：NeoDB 源定位就是 60 条 trending 精选；要真·全量 + genre/年代/地区筛选，必须换/加带这些能力的源（TMDB Discover / 豆瓣），鉴权无解。

## Fork 关键约定：post_message 签名已变更（影响所有插件！）
- 本 fork 的 `_PluginBase.post_message` 签名是 `post_message(self, channel: MessageChannel = None, mtype=None, title=None, text=None, image=None, link=None, userid=None, username=None, **kwargs)`，内部 `Notification(channel=channel, mtype=mtype, title=title, text=text, ...)`。
- **禁止**再像老 MP 那样 `self.post_message(Notification(...))` —— 会把整个 Notification 对象当成第一个位置参数 `channel` 传入，触发 `ValidationError: Notification channel ... Input should be [...]`（即日志里「发送通知失败：1 validation error for Notification channel」）。
- **正确写法**：`self.post_message(mtype=NotificationType.Plugin, title=..., text=..., source=self.plugin_name)`（channel 默认 None = 发给所有已配置渠道；source 经 **kwargs 透传进 Notification）。

## ChineseSubFinder 修复（v6.0.2，2026-08-02）
- **部署路径**：仅 fork 内置 `/app/app/plugins/chinesesubfinder/`（**无** `/config/plugins` 副本），修代码只 docker cp 这一个内置路径即可（不像 neodbsource 要双路径）。
- **Bug 1（WARNING「发送通知失败」）**：即上面的 post_message 签名踩坑，`__notify` 旧式传 Notification 对象 → 改为关键字拆传。已在容器内用 `MessageChain.post_message(mtype=..., title=..., text=..., source=...)` 实测不再崩 channel 校验。
- **Bug 2（ERROR「调用 API 失败 HTTP 500：open ... .nfo: no such file or directory」）根因 = 时序竞态，不是路径错**：
  - CSF 的 `/api/v1/add-job` 要靠同目录 `.nfo`（Emby/Jellyfin 扫描生成）取 IMDb/TMDB id；MP 的 `TransferComplete` 事件一触发插件就立刻调 CSF，此时 `.nfo` 还没生成 → CSF 返回 500。`.nfo` 通常晚约 1 分钟出现，之后 CSF 自己又会把字幕下好（实测 S03E05 等 .zh.srt/.zh.ass 都在）。
  - 修复：`__request_csf` 改为 **后台 daemon 线程**调用，对 5xx/网络异常做最多 5 次、间隔 20s 重试；仅「鉴权失败/路径在 CSF 端不可见/参数错」(4xx) 或重试耗尽才记 ERROR+通知。瞬时 5xx 只记 INFO「暂未就绪…重试」，不再刷屏。
  - 关键事实：MP 容器 `/media` 挂 `/vol3/@appcenter/MoviePilot/docker/media`，CSF 容器 `/media` 挂 `/vol2/1000/Media/link`（两个不同的 NAS 卷，但逻辑路径都是 `/media/shows/...`）；插件传给 CSF 的 `physical_video_file_full_path` 在 CSF 侧能正确解析（字幕已下好即证明），故重试必能成功。
- 验证：容器内 `加载插件：ChineseSubFinder 版本：6.0.2`，无加载失败；post_message 行为测试通过。

## StuckDownloadGuard 新增「降级切换」(v1.1.0, 2026-08-05)
- **部署路径**：仅 fork 内置 `/app/app/plugins/stuckdownloadguard/`（**无** `/config/plugins` 副本），修代码只 docker cp 这一个内置路径即可（同 chinesesubfinder）。
- **用户诉求**：长期下载速度=0 应「降级切换」——降级=降优先级排至队尾；切换=换源重搜后替换原种子（解决无源卡死）。
- **实现要点**：
  - `__handle_stuck` 重写：每次卡顿先 `__demote_and_move_tail`（降级），再尝试 `__switch_source`（切换，每周期仅一次）；切换成功即移除原种子并停止追踪；切换失败且达 `max_retries` 则 `__escalate`（停止+清理，订阅源顺带重搜）。
  - 订阅来源切换：`SubscribeChain().search(sid)` 重搜（MP 选最优种子）→ 成功后再 stop+remove 原种子 + 删历史。
  - 非订阅来源切换：`SearchChain().search_by_title(title)` 跨**全部索引器**重搜 → 过滤（seeders>0 且非原种子同名）→ 取做种人最多者 → `DownloadChain().download_single(context=ctx, torrent_content=ti.enclosure, label=settings.TORRENT_TAG)` 添加 → 成功则 stop+remove 原种子。
  - 新增配置 `switch_source`（默认 True，关闭则仅降级排队）。
- **关键 API 实测**（本 fork）：
  - `SearchChain.search_by_title(title, sites=None, cache_local=False) -> List[Context]`：跨所有索引器搜索；`Context.torrent_info` 为 `TorrentInfo`，含 `enclosure`（下载链接）、`seeders`、`title`。
  - `DownloadChain.download_single(self, context, torrent_file=None, torrent_content=None, episodes=None, ..., label=None, ...)`：高层下载入口，内部自动处理下载目录/cookie/分类，`label=settings.TORRENT_TAG` 可打 MP 标签。
  - `DownloadHistory` 字段：`id, title, year, tmdbid, imdbid, doubanid, type, seasons, episodes, torrent_name, ...` → 可用于回推媒体身份做重搜。
  - 无独立 `ManualDownloadChain`；`IndexerOper`/`IndexerHelper` 在核心不可直接导入（站点列表由 indexer 插件如 jackettextend 提供），故非订阅重搜走 `SearchChain.search_by_title` 而非枚举站点。
- **同修 post_message 签名 bug**：`__notify` 旧式 `self.post_message(Notification(...))` 在 fork 新签名下会把 Notification 当 channel 传 → channel 校验崩溃、所有通知失效；改为 `self.post_message(mtype=..., title=..., text=..., source=...)`。已容器内验证不再崩。
- 验证：容器内 `加载插件：StuckDownloadGuard 版本：1.1.0`，无加载失败；post_message 关键字调用不再崩 channel；`get_form` 含 `switch_source` 默认 True；切换相关方法（mangled `__switch_source/__switch_nonsub/__history_mtype/__download_single`）均存在。
