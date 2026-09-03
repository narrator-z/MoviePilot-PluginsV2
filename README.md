# MoviePilot 插件

> **免责声明**  
> 本项目及其插件仅供学习与交流使用，严禁用于任何商业或非法用途。请遵守当地法律法规，因使用本项目产生的任何后果由使用者自行承担。

## 使用说明
1. 基础参数配置完成点击"保存"，再次点击插件"查看数据"
2. 复制站点=>MP站点管理=>添加站点=>粘贴到"站点地址"=>保存
![](https://raw.githubusercontent.com/narrator-z/MoviePilot-Plugins/main/docs/imgs/plugins_domains.png)
![](https://raw.githubusercontent.com/narrator-z/MoviePilot-Plugins/main/docs/imgs/add_site.png)
![](https://raw.githubusercontent.com/narrator-z/MoviePilot-Plugins/main/docs/imgs/plugin_site.png)
## 插件目录

### V2 版本插件

#### JackettExtend
- **插件名称**: JackettExtend
- **插件描述**: 扩展检索以支持 Jackett 站点资源
- **插件版本**: 1.4
- **插件作者**: narrator-z
- **作者主页**: https://github.com/narrator-z
- **主要功能**:
  - 支持配置 Jackett 服务器信息（地址、API Key、密码、代理等）
  - 定时获取 Jackett 索引器列表
  - 支持通过 Web 界面配置插件参数
  - 支持关键字资源搜索
  - 支持定时任务与手动触发索引器状态获取
  - 支持多站点资源聚合检索
- **使用方法**:
  1. 在插件配置页面填写 Jackett 服务器地址、API Key、密码（如有）、代理等信息
  2. 设置定时任务周期，或点击"立即运行一次"手动获取索引器列表
  3. 插件会自动定时更新索引器列表
  4. 可在详情页面查看已获取的索引器列表及状态
- **注意事项**:
  - 需先在 Jackett 中添加并配置好 indexer
  - 建议先在 Jackett 后台测试通过后再在本插件中使用

#### ProwlarrExtend
- **插件名称**: ProwlarrExtend
- **插件描述**: 扩展检索以支持 Prowlarr 站点资源
- **插件版本**: 1.4
- **插件作者**: narrator-z
- **作者主页**: https://github.com/narrator-z
- **主要功能**:
  - 支持配置 Prowlarr 服务器信息（地址、API Key、代理等）
  - 定时获取 Prowlarr 索引器列表
  - 支持通过 Web 界面配置插件参数
  - 支持关键字资源搜索
  - 支持定时任务与手动触发索引器状态获取
  - 支持多站点资源聚合检索
- **使用方法**:
  1. 在插件配置页面填写 Prowlarr 服务器地址、API Key、代理等信息
  2. 设置定时任务周期，或点击"立即运行一次"手动获取索引器列表
  3. 插件会自动定时更新索引器列表
  4. 可在详情页面查看已获取的索引器列表及状态
- **注意事项**:
  - 需先在 Prowlarr 中添加并配置好 indexer
  - 建议先在 Prowlarr 后台测试通过后再在本插件中使用

#### StuckDownloadGuard
- **插件名称**: StuckDownloadGuard
- **插件描述**: 监控下载任务，长时间无进度则降级排至队尾，连续无效则停止并重新搜索
- **插件版本**: 1.0.0
- **插件作者**: narrator-z
- **作者主页**: https://github.com/narrator-z
- **主要功能**:
  - 监控下载管理中带 MoviePilot 标签的种子，识别「进度≈0 且下载速度=0（不含排队时间）」的卡住任务
  - 卡顿达到设定时长（默认 30 分钟）后，降低优先级并排至下载队列队尾
  - 连续多次（默认 3 次）降级仍无效后，停止种子、清理下载任务，并尝试重新搜索（订阅来源）
  - 直接调用下载器原生 API（qBittorrent / Transmission）读取真实状态，精确区分「排队中」与「下载中」
  - 支持配置监控周期、卡顿阈值、连续次数、是否仅处理订阅来源、清理时是否删除文件/下载记录、是否发送通知
  - 详情页面展示当前被监控的卡住任务（已卡住时长、连续降级次数等）
- **使用方法**:
  1. 在插件配置页面开启「启用插件」，按需调整监控周期（默认 `*/5 * * * *`，即每 5 分钟检查一次）
  2. 设置「卡顿时长(分钟)」（默认 30）与「连续降级次数」（默认 3）
  3. 如需自动重新搜索，保持「仅处理订阅来源」开启（重新搜索依赖订阅来源）；关闭则覆盖全部下载任务
  4. 可打开「立即运行一次」立即触发一次监控
  5. 在详情页面查看监控中的卡住任务与连续降级次数
- **注意事项**:
  - 仅处理带 MoviePilot 标签（`settings.TORRENT_TAG`）的种子，即由 MoviePilot 管理的下载任务
  - 下载器需为 qBittorrent 或 Transmission；「排至队尾 / 降低优先级」通过下载器原生 API 实现
  - 「重新搜索」仅对订阅来源的下载有效，会通过订阅链（`SubscribeChain`）触发重新搜索；非订阅来源只做停止与清理
  - 升级处理（停止 + 清理 + 重新搜索）时默认删除对应下载历史（便于重新搜索不被误判为已下载），可通过「清理时删除下载记录」关闭
  - 「清理时删除文件」默认关闭，避免误删已下载文件，请谨慎开启
  - 卡顿计时会在「排队中」状态暂停（不计入排队时间），并在恢复下载或任务完成时清零

#### ChineseSubFinder
- **插件名称**: ChineseSubFinder
- **插件描述**: 整理入库时通知 ChineseSubFinder 下载字幕（修复 API 失败、增加连接测试与诊断）
- **插件版本**: 1.0.0
- **插件作者**: narrator-z
- **作者主页**: https://github.com/narrator-z
- **主要功能**:
  - 监听 MoviePilot 整理入库事件（TransferComplete），自动通知 ChineseSubFinder 下载中文字幕
  - 支持本地路径 → 远端路径映射（适配 MoviePilot 与 CSF 容器路径不一致的场景）
  - 提供「测试连接」按钮，一次性校验服务器可达性与 API Token 有效性
  - 调用失败时记录 HTTP 状态码与 CSF 返回的具体原因（如 AccessToken Error / api_key_enabled == false / physical video file not found），并通过通知推送
- **使用方法**:
  1. 在插件配置页面填写 CSF 服务器地址（含端口，如 `http://192.168.1.10:19035`）
  2. **API Token 填写 ChineseSubFinder 设置中的「外部 API Token」**，不是 Web 登录密码；需在 CSF 设置中开启外部 API 并设置一个 ApiToken
  3. 若 MoviePilot 与 CSF 看到的媒体路径前缀不同，填写「本地路径」与「远端路径」做替换
  4. 点击插件 API 中的「测试连接」验证地址与 Token，再启用插件
- **注意事项**:
  - 官方同名插件报错「调用 ChineseSubFinder API 失败」的根因：CSF 的 `/api/v1` 外部接口由 `CheckApiAuth` 保护，要求 `Authorization: Bearer <ApiToken>`（即 CSF 外部 API Token），填成 Web 登录密码或留空会返回 HTTP 401，本插件会在日志/通知中明确指出该原因
  - 若返回「physical video file not found」，说明该文件在 CSF 容器侧不存在，需检查路径映射

---

#### NeoDBSource
- **插件名称**: NeoDB源
- **插件描述**: 让探索、推荐和媒体识别支持 NeoDB 数据源（电影/剧集/图书/游戏/音乐）
- **插件版本**: 1.0.0
- **插件作者**: narrator-z
- **作者主页**: https://github.com/narrator-z
- **主要功能**:
  - **探索数据源**：在「探索」页新增 NeoDB 标签页，按「关键词 + 类别（电影/剧集/动画/图书/游戏/音乐）」搜索 NeoDB 公开目录
  - **推荐数据源**：在「推荐」页新增 NeoDB 热门游戏、科幻电影、动画剧集等列表
  - **媒体识别增强**：当系统无法通过 TMDB/豆瓣等识别时，回退到 NeoDB 按名称搜索，并从 `external_resources` 解析出 TMDB ID 以帮助命中（支持「仅当系统无法识别」与「劫持」两种模式）
  - **媒体ID回链**：支持 `neodb:<类别>.<uuid>` 形式的媒体ID，详情页自动转换为 TMDB ID
  - 支持自定义 NeoDB 实例地址（默认旗舰实例 `neodb.social`，可指向自建实例）与代理开关
- **使用方法**:
  1. 在插件配置页面开启「启用插件」，按需开启「媒体识别」（如需把 NeoDB 作为识别兜底）
  2. 在「探索」页选择 NeoDB 来源，输入关键词并选择类别即可搜索
  3. 在「推荐」页查看 NeoDB 精选列表
- **注意事项**:
  - NeoDB 公开 API 无需鉴权即可搜索/查看目录与热门游戏；电影/剧集会尝试解析 TMDB ID 以获得完整详情，图书/游戏/音乐以 NeoDB 数据直接展示
  - 参考实现：对标 wumode/MoviePilot-Plugins 的 `imdbsource`，API 文档见 https://neodb.social/developer/

如需扩展更多 BT 站点或自定义插件，请参考 `plugins.v2` 目录下的插件实现方式。