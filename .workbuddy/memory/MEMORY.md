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
- `NeoDBSource`（探索/推荐/识别 NeoDB 数据源，v1.0.1）、`ChineseSubFinder`（v6.0.1）、`StuckDownloadGuard`、`JackettExtend`、`ProwlarrExtend`、`SiteOpenSignup`。
- 目录名小写 = 类名/key 小写 = 插件 ID；Market 文件列表安装按 `pid.lower()` 匹配目录。
