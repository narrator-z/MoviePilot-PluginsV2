"""
NeoDBSource —— 让 MoviePilot 的探索、推荐与媒体识别支持 NeoDB 数据源。

功能对标 wumode/MoviePilot-Plugins 的 imdbsource：
1. 探索数据源（DiscoverSource）：在「探索」页提供 NeoDB 目录搜索（电影/剧集/动画）。
2. 推荐数据源（RecommendSource）：在「推荐」页提供 NeoDB 热门榜单列表。
3. 媒体ID转换与点击识别（MediaRecognizeConvert + recognize_media 模块）：
   - 点击探索页中的 NeoDB 条目时，以 `neodb:<category>.<uuid>` 为媒体ID，
     由本插件直接回查 NeoDB 详情并构造 MediaInfo，无需依赖「媒体识别」子开关；
   - 若条目带有 TMDB/豆瓣外链，则转换为对应 ID 以获得完整详情。

NeoDB 公开 API 无需鉴权即可搜索/查看目录与热门榜单；如需登录态的收藏/动态接口，可后续扩展。

参考：
- https://github.com/wumode/MoviePilot-Plugins  （imdbsource 插件结构）
- https://neodb.social/developer/                （NeoDB API 文档）
"""

from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.core.context import MediaInfo
from app.core.event import Event, eventmanager
from app.core.meta import MetaBase
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import (
    DiscoverMediaSource,
    DiscoverSourceEventData,
    MediaRecognizeConvertEventData,
    RecommendMediaSource,
    RecommendSourceEventData,
)
from app.schemas.types import ChainEventType, EventType, MediaType
from app.utils.http import AsyncRequestUtils, RequestUtils

from .neodbhelper import NeoDBHelper


class NeoDBSource(_PluginBase):
    # 插件名称
    plugin_name = "NeoDB源"
    # 插件描述
    plugin_desc = "让探索、推荐和媒体识别支持 NeoDB 数据源（电影/剧集/动画）。"
    # 插件图标
    plugin_icon = "neodb.png"
    # 插件版本
    plugin_version = "1.0.2"
    # 插件作者
    plugin_author = "narrator-z"
    # 作者主页
    author_url = "https://github.com/wumode"
    # 插件配置项ID前缀
    plugin_config_prefix = "neodbsource_"
    # 加载顺序
    plugin_order = 23
    # 可使用的用户级别
    auth_level = 1

    # 插件配置
    _enabled: bool = False
    _proxy: bool = False
    _neodb_url: str = "https://neodb.social"

    # 私有属性
    _helper: NeoDBHelper = None

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def init_plugin(self, config: dict = None):
        if config:
            self._enabled = bool(config.get("enabled"))
            self._proxy = bool(config.get("proxy"))
            self._neodb_url = (config.get("neodb_url") or "https://neodb.social").rstrip("/")
            self._update_config()

        self._helper = NeoDBHelper(
            base_url=self._neodb_url,
            proxies=settings.PROXY if self._proxy else None,
        )

        # 允许 NeoDB 封面域名加载（公网域名本就会被图片代理放行，
        # 这里再显式加入白名单，避免个别网络环境下被拦）
        for domain in ("neodb.social", "movie.douban.com", "themoviedb.org", "image.tmdb.org"):
            if domain not in settings.SECURITY_IMAGE_DOMAINS:
                settings.SECURITY_IMAGE_DOMAINS.append(domain)

        # 探索/推荐/点击识别所需的模块在 get_module() 中按需注册，无需在此打补丁

    def get_state(self) -> bool:
        return self._enabled

    def stop_service(self):
        # 模块随插件启停由 ModuleManager 依据 get_module() 自动注册/注销
        pass

    def get_module(self) -> Dict[str, Any]:
        """
        注册媒体识别模块，使点击探索页 NeoDB 条目（mediaid 形如 neodb:<category>.<uuid>）
        能被本插件直接识别，只要插件处于启用状态即可，不依赖任何子开关。
        """
        if not self._enabled:
            return {}
        return {
            "async_recognize_media": self.async_recognize_media,
            "recognize_media": self.recognize_media,
        }

    def _update_config(self):
        self.update_config(
            {
                "enabled": self._enabled,
                "proxy": self._proxy,
                "neodb_url": self._neodb_url,
            }
        )

    # ------------------------------------------------------------------ #
    # 配置表单
    # ------------------------------------------------------------------ #
    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {"component": "VSwitch", "props": {"model": "enabled", "label": "启用插件"}}
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {"component": "VSwitch", "props": {"model": "proxy", "label": "使用代理服务器"}}
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 12},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "neodb_url",
                                            "label": "NeoDB 实例地址",
                                            "placeholder": "https://neodb.social",
                                            "hint": "默认旗舰实例 neodb.social；自建实例填写你的域名",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "title": "关于 NeoDB",
                                            "text": "NeoDB 是开放的书籍/影视/游戏/音乐社交书目库。本插件使用其公开 API 提供"
                                            "电影/剧集/动画的探索与推荐；电影/剧集会尝试解析 TMDB/豆瓣 ID 以获得完整详情，"
                                            "无外链的条目以 NeoDB 数据直接展示（可浏览详情，订阅需有 TMDB/豆瓣 ID）。",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                ],
            }
        ], {
            "enabled": False,
            "proxy": False,
            "neodb_url": "https://neodb.social",
        }

    def get_page(self) -> List[dict]:
        pass

    def get_command(self) -> List[Dict[str, Any]]:
        return []

    # ------------------------------------------------------------------ #
    # 插件 API
    # ------------------------------------------------------------------ #
    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": "/neodb-discover",
                "endpoint": self.neodb_discover,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "NeoDB 探索数据源",
                "description": "按关键词、类别、类型与排序搜索 NeoDB 目录",
            },
            {
                "path": "/neodb-trending",
                "endpoint": self.neodb_trending,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "NeoDB 热门榜单",
                "description": "获取 NeoDB 热门榜单（电影/剧集/动画）",
            },
        ]

    async def neodb_discover(
        self,
        category: str = "movie",
        q: Optional[str] = None,
        mtype: Optional[str] = None,
        sort: Optional[str] = None,
        page: int = 1,
    ) -> List[dict]:
        """
        探索数据源：搜索 / 热门 NeoDB 目录，并支持按类型与排序二次筛选。
        :param category: movie / tv / anime（anime 归为 tv）
        :param q: 搜索关键词；为空时返回该分类热门榜单，避免探索页空白
        :param mtype: 子类型筛选（Movie / TVShow / TVSeason / all）
        :param sort: hot（默认，保持原顺序）/ rating（按评分降序）
        :param page: 页码
        """
        if not self._helper:
            return []
        # anime 在 NeoDB 中归为 tv
        api_category = "tv" if category == "anime" else category
        if q:
            items = await self._helper.async_search(query=q, category=api_category, page=page)
        else:
            items = await self._helper.async_trending(api_category, page=page)

        # 类型二次筛选（客户端，基于 NeoDB 条目的 type 字段）
        if mtype and mtype != "all":
            items = [it for it in items if it.get("type") == mtype]

        # 排序
        if sort == "rating":
            items = sorted(
                items,
                key=lambda x: (x.get("rating") or 0),
                reverse=True,
            )

        medias: List[MediaInfo] = []
        for it in items:
            try:
                medias.append(NeoDBHelper.item_to_mediainfo(it))
            except Exception as e:  # noqa: BLE001
                logger.debug(f"NeoDB 条目转换失败：{e}")
        return [m.to_dict() for m in medias]

    async def neodb_trending(
        self,
        category: str = "movie",
        page: int = 1,
    ) -> List[dict]:
        """热门榜单（公开接口，无需鉴权）。category 支持 movie/tv/anime（anime 归 tv）。"""
        if not self._helper:
            return []
        api_category = "tv" if category == "anime" else category
        items = await self._helper.async_trending(api_category, page=page)
        medias: List[MediaInfo] = []
        for it in items:
            try:
                medias.append(NeoDBHelper.item_to_mediainfo(it))
            except Exception as e:  # noqa: BLE001
                logger.debug(f"NeoDB 条目转换失败：{e}")
        return [m.to_dict() for m in medias]

    # ------------------------------------------------------------------ #
    # 事件：探索 / 推荐 数据源
    # ------------------------------------------------------------------ #
    @eventmanager.register(ChainEventType.DiscoverSource)
    def discover_source(self, event: Event):
        if not self._enabled:
            return
        event_data: DiscoverSourceEventData = event.event_data
        neodb_source = DiscoverMediaSource(
            name="NeoDB",
            mediaid_prefix="neodb",
            api_path="plugin/NeoDBSource/neodb-discover",
            filter_params={
                "category": "movie",
                "q": "",
                "mtype": "all",
                "sort": "hot",
            },
            filter_ui=self.neodb_filter_ui(),
        )
        if not event_data.extra_sources:
            event_data.extra_sources = [neodb_source]
        else:
            event_data.extra_sources.append(neodb_source)

    @eventmanager.register(ChainEventType.RecommendSource)
    def recommend_source(self, event: Event):
        if not self._enabled:
            return
        event_data: RecommendSourceEventData = event.event_data
        if not event_data:
            return
        sources = [
            RecommendMediaSource(
                name="NeoDB 热门电影",
                api_path="plugin/NeoDBSource/neodb-trending?category=movie",
                type="Movies",
            ),
            RecommendMediaSource(
                name="NeoDB 热门剧集",
                api_path="plugin/NeoDBSource/neodb-trending?category=tv",
                type="TV Shows",
            ),
            RecommendMediaSource(
                name="NeoDB 热门动画",
                api_path="plugin/NeoDBSource/neodb-trending?category=anime",
                type="TV Shows",
            ),
        ]
        if not event_data.extra_sources:
            event_data.extra_sources = sources
        else:
            event_data.extra_sources.extend(sources)

    @staticmethod
    def neodb_filter_ui() -> List[dict]:
        """
        探索页过滤器：关键词输入框 + 类别（电影/剧集/动画）+ 每个类别下的「类型」子筛选
        + 排序。类型筛选借助 MP 的 show 表达式随类别联动显示。
        """
        return [
            {
                "component": "VTextField",
                "props": {
                    "model": "q",
                    "label": "搜索关键词",
                    "placeholder": "输入名称搜索（电影/剧集/动画）",
                    "clearable": True,
                    "hide-details": True,
                },
            },
            {
                "component": "VChipGroup",
                "props": {"model": "category", "mandatory": True},
                "content": [
                    {"component": "VChip", "props": {"filter": True, "tile": True, "value": "movie"}, "text": "电影"},
                    {"component": "VChip", "props": {"filter": True, "tile": True, "value": "tv"}, "text": "剧集"},
                    {"component": "VChip", "props": {"filter": True, "tile": True, "value": "anime"}, "text": "动画"},
                ],
            },
            {
                "component": "VChipGroup",
                "props": {"model": "mtype", "mandatory": True},
                "content": [
                    {"component": "VChip", "props": {"filter": True, "tile": True, "value": "all", "show": "{{category == 'movie'}}"}, "text": "全部"},
                    {"component": "VChip", "props": {"filter": True, "tile": True, "value": "Movie", "show": "{{category == 'movie'}}"}, "text": "电影"},
                    {"component": "VChip", "props": {"filter": True, "tile": True, "value": "all", "show": "{{category == 'tv'}}"}, "text": "全部"},
                    {"component": "VChip", "props": {"filter": True, "tile": True, "value": "TVShow", "show": "{{category == 'tv'}}"}, "text": "电视剧"},
                    {"component": "VChip", "props": {"filter": True, "tile": True, "value": "TVSeason", "show": "{{category == 'tv'}}"}, "text": "单季"},
                    {"component": "VChip", "props": {"filter": True, "tile": True, "value": "all", "show": "{{category == 'anime'}}"}, "text": "全部"},
                    {"component": "VChip", "props": {"filter": True, "tile": True, "value": "TVShow", "show": "{{category == 'anime'}}"}, "text": "动画剧集"},
                    {"component": "VChip", "props": {"filter": True, "tile": True, "value": "TVSeason", "show": "{{category == 'anime'}}"}, "text": "动画单季"},
                ],
            },
            {
                "component": "VChipGroup",
                "props": {"model": "sort", "mandatory": True},
                "content": [
                    {"component": "VChip", "props": {"filter": True, "tile": True, "value": "hot"}, "text": "热门"},
                    {"component": "VChip", "props": {"filter": True, "tile": True, "value": "rating"}, "text": "高分"},
                ],
            },
        ]

    # ------------------------------------------------------------------ #
    # 事件：媒体ID转换（neodb: -> tmdb/douban）
    # ------------------------------------------------------------------ #
    @eventmanager.register(ChainEventType.MediaRecognizeConvert)
    async def async_media_recognize_covert(self, event: Event):
        if not self._enabled:
            return
        event_data: MediaRecognizeConvertEventData = event.event_data
        if not event_data:
            return
        if event_data.convert_type != "themoviedb":
            # 当前仅实现 neodb -> tmdb；douban 转换可由详情页直接识别兜底
            return
        if not str(event_data.mediaid).startswith("neodb:"):
            return
        neodb_id = event_data.mediaid[5:]
        tmdb_id = await self.async_neodb_to_tmdb(neodb_id)
        if tmdb_id is not None:
            event_data.media_dict["id"] = tmdb_id

    async def async_neodb_to_tmdb(self, neodb_id: str) -> Optional[int]:
        parts = neodb_id.split(".", 1)
        if len(parts) != 2:
            return None
        category, uuid = parts
        item = await self._helper.async_get_item(category, uuid)
        if not item:
            return None
        return NeoDBHelper.extract_tmdb_id(item)

    # ------------------------------------------------------------------ #
    # 媒体识别模块（点击探索页 NeoDB 条目时由核心识别链调用）
    # ------------------------------------------------------------------ #
    def recognize_media(
        self,
        meta: MetaBase = None,
        mtype: MediaType = None,
        **kwargs,
    ) -> Optional[MediaInfo]:
        """
        仅处理 NeoDB 来源的媒体ID（source=neodb & mediaid 形如 <category>.<uuid>），
        直接回查 NeoDB 详情并构造 MediaInfo。其它来源一律交还给核心模块处理，
        避免覆盖 TMDB/豆瓣等的正常识别结果。
        """
        source = kwargs.get("source")
        mediaid = kwargs.get("mediaid")
        if source == "neodb" and mediaid:
            return self._recognize_by_neodb_id(mediaid, mtype)
        return None

    async def async_recognize_media(
        self,
        meta: MetaBase = None,
        mtype: MediaType = None,
        **kwargs,
    ) -> Optional[MediaInfo]:
        source = kwargs.get("source")
        mediaid = kwargs.get("mediaid")
        if source == "neodb" and mediaid:
            return self._recognize_by_neodb_id(mediaid, mtype)
        return None

    def _recognize_by_neodb_id(self, mediaid: str, mtype: MediaType = None) -> Optional[MediaInfo]:
        parts = mediaid.split(".", 1)
        if len(parts) != 2:
            logger.warning(f"NeoDB 媒体ID格式错误：{mediaid}")
            return None
        category, uuid = parts
        item = self._helper.get_item(category, uuid)
        if not item:
            logger.warning(f"NeoDB 未找到条目：{mediaid}")
            return None
        mi = NeoDBHelper.item_to_mediainfo(item)
        if mtype:
            mi.type = mtype
        return mi

    # ------------------------------------------------------------------ #
    # 插件重载
    # ------------------------------------------------------------------ #
    @eventmanager.register(EventType.PluginReload)
    def reload(self, event):
        plugin_id = event.event_data.get("plugin_id")
        if plugin_id == self.__class__.__name__:
            self.init_plugin(self.get_config())
