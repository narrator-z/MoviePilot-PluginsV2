"""
NeoDBSource —— 让 MoviePilot 的探索、推荐与媒体识别支持 NeoDB 数据源。

功能对标 wumode/MoviePilot-Plugins 的 imdbsource：
1. 探索数据源（DiscoverSource）：在「探索」页提供 NeoDB 目录搜索（电影/剧集/图书/游戏/音乐/动画）。
2. 推荐数据源（RecommendSource）：在「推荐」页提供 NeoDB 热门游戏、科幻电影、动画剧集等列表。
3. 媒体识别增强（recognize_media）：当系统无法通过 TMDB/豆瓣等识别时，回退到 NeoDB 按名称搜索，
   并从 external_resources 解析出 tmdb_id 以帮助命中。
4. 媒体ID转换（MediaRecognizeConvert）：支持 `neodb:<category>.<uuid>` 形式的媒体ID回链到 TMDB。

NeoDB 公开 API 无需鉴权即可搜索/查看目录与热门游戏；如需登录态的收藏/动态接口，可后续扩展。

参考：
- https://github.com/wumode/MoviePilot-Plugins  （imdbsource 插件结构）
- https://neodb.social/developer/                （NeoDB API 文档）
"""

import inspect
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple

from app.chain import ChainBase
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
    plugin_desc = "让探索、推荐和媒体识别支持 NeoDB 数据源（电影/剧集/图书/游戏/音乐）。"
    # 插件图标
    plugin_icon = "neodb.png"
    # 插件版本
    plugin_version = "1.0.1"
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
    _recognize_media: bool = False
    _recognition_mode: str = "auxiliary"
    _neodb_url: str = "https://neodb.social"

    # 私有属性
    _helper: NeoDBHelper = None
    _original_method: Optional[Callable] = None
    _original_async_method: Optional[Callable[..., Coroutine[Any, Any, Optional[MediaInfo]]]] = None

    @staticmethod
    def _extract_method_kwargs(method: Optional[Callable], chain_self, args: tuple, kwargs: dict) -> dict:
        if not method:
            return dict(kwargs)
        try:
            signature = inspect.signature(method)
            bound = signature.bind_partial(chain_self, *args, **kwargs)
            arguments = dict(bound.arguments)
            first_param = next(iter(signature.parameters), None)
            if first_param:
                arguments.pop(first_param, None)
            return arguments
        except TypeError:
            arguments = dict(kwargs)
            if args:
                arguments.setdefault("meta", args[0])
            if len(args) > 1:
                arguments.setdefault("mtype", args[1])
            return arguments

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def init_plugin(self, config: dict = None):
        plugin_instance: NeoDBSource = self

        def patched_recognize_media(chain_self, *args, **kwargs):
            if not plugin_instance._original_method:
                return None
            result = plugin_instance._original_method(chain_self, *args, **kwargs)
            if result is None and NeoDBSource._enabled and NeoDBSource._recognize_media:
                logger.info(f"通过插件 {NeoDBSource.plugin_name} 执行：recognize_media ...")
                plugin_kwargs = plugin_instance._extract_method_kwargs(
                    plugin_instance._original_method, chain_self, args, kwargs
                )
                meta = plugin_kwargs.pop("meta", None)
                mtype = plugin_kwargs.pop("mtype", None)
                return plugin_instance.recognize_media(meta=meta, mtype=mtype, **plugin_kwargs)
            return result

        async def patched_async_recognize_media(chain_self, *args, **kwargs):
            if not plugin_instance._original_async_method:
                return None
            result = await plugin_instance._original_async_method(chain_self, *args, **kwargs)
            if result is None and NeoDBSource._enabled and NeoDBSource._recognize_media:
                logger.info(f"通过插件 {NeoDBSource.plugin_name} 执行：async_recognize_media ...")
                plugin_kwargs = plugin_instance._extract_method_kwargs(
                    plugin_instance._original_async_method, chain_self, args, kwargs
                )
                meta = plugin_kwargs.pop("meta", None)
                mtype = plugin_kwargs.pop("mtype", None)
                return await plugin_instance.async_recognize_media(meta=meta, mtype=mtype, **plugin_kwargs)
            return result

        setattr(patched_recognize_media, "_patched_by", id(self))
        if not getattr(ChainBase.recognize_media, "_patched_by", object()) == id(self):
            self._original_method = getattr(ChainBase, "recognize_media", None)

        setattr(patched_async_recognize_media, "_patched_by", id(self))
        if not getattr(ChainBase.async_recognize_media, "_patched_by", object()) == id(self):
            self._original_async_method = getattr(ChainBase, "async_recognize_media", None)

        if config:
            self._enabled = bool(config.get("enabled"))
            self._proxy = bool(config.get("proxy"))
            self._recognize_media = bool(config.get("recognize_media"))
            self._recognition_mode = config.get("recognition_mode") or "auxiliary"
            self._neodb_url = (config.get("neodb_url") or "https://neodb.social").rstrip("/")
            self._update_config()

        self._helper = NeoDBHelper(
            base_url=self._neodb_url,
            proxies=settings.PROXY if self._proxy else None,
        )

        # 允许 NeoDB 封面域名加载
        for domain in ("neodb.social", "movie.douban.com", "themoviedb.org", "image.tmdb.org"):
            if domain not in settings.SECURITY_IMAGE_DOMAINS:
                settings.SECURITY_IMAGE_DOMAINS.append(domain)

        if self._enabled:
            if self._recognize_media and self._recognition_mode == "auxiliary":
                if not (getattr(ChainBase.recognize_media, "_patched_by", object()) == id(self)):
                    ChainBase.recognize_media = patched_recognize_media
                if not getattr(ChainBase.async_recognize_media, "_patched_by", object()) == id(self):
                    ChainBase.async_recognize_media = patched_async_recognize_media
            else:
                self._restore_original()
        else:
            self.stop_service()

    def get_state(self) -> bool:
        return self._enabled

    def stop_service(self):
        self._restore_original()

    def _restore_original(self):
        if getattr(ChainBase.recognize_media, "_patched_by", object()) == id(self) and self._original_method:
            ChainBase.recognize_media = self._original_method
        if getattr(ChainBase.async_recognize_media, "_patched_by", object()) == id(self) and self._original_async_method:
            ChainBase.async_recognize_media = self._original_async_method

    def get_module(self) -> Dict[str, Any]:
        modules = {}
        if self._recognize_media and self._recognition_mode == "hijacking":
            modules["async_recognize_media"] = self.async_recognize_media
            modules["recognize_media"] = self.recognize_media
        return modules

    def _update_config(self):
        self.update_config(
            {
                "enabled": self._enabled,
                "proxy": self._proxy,
                "recognize_media": self._recognize_media,
                "recognition_mode": self._recognition_mode,
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
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {"component": "VSwitch", "props": {"model": "enabled", "label": "启用插件"}}
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {"component": "VSwitch", "props": {"model": "proxy", "label": "使用代理服务器"}}
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {"component": "VSwitch", "props": {"model": "recognize_media", "label": "媒体识别"}}
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "recognition_mode",
                                            "label": "媒体识别工作模式",
                                            "items": [
                                                {"title": "仅当系统无法识别", "value": "auxiliary"},
                                                {"title": "正常（劫持）", "value": "hijacking"},
                                            ],
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
                                            "text": "NeoDB 是开放的书籍/影视/游戏/音乐社交书目库。本插件使用其公开 API 提供探索与推荐；"
                                            "电影/剧集会尝试解析 TMDB ID 以获得完整详情，图书/游戏/音乐以 NeoDB 数据直接展示。",
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
            "recognize_media": False,
            "recognition_mode": "auxiliary",
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
                "description": "按关键词与类别搜索 NeoDB 目录",
            },
            {
                "path": "/neodb-trending",
                "endpoint": self.neodb_trending,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "NeoDB 热门游戏",
                "description": "获取 NeoDB 热门游戏榜单",
            },
        ]

    async def neodb_discover(
        self,
        category: str = "movie",
        q: Optional[str] = None,
        page: int = 1,
    ) -> List[dict]:
        """
        探索数据源：搜索 NeoDB 目录。
        :param category: movie / tv / book / game / music / anime
        :param q: 搜索关键词（探索页搜索框传入）；为空时返回该分类的热门榜单，避免探索页空白
        :param page: 页码
        """
        if not self._helper:
            return []
        # anime 在 NeoDB 中归为 tv
        api_category = "tv" if category == "anime" else category
        if q:
            items = await self._helper.async_search(query=q, category=api_category, page=page)
        else:
            # 未提供关键词：返回该分类热门榜单作为兜底
            items = await self._helper.async_trending(api_category, page=page)
        medias: List[MediaInfo] = []
        for it in items:
            try:
                medias.append(NeoDBHelper.item_to_mediainfo(it))
            except Exception as e:  # noqa: BLE001
                logger.debug(f"NeoDB 条目转换失败：{e}")
        return [m.to_dict() for m in medias]

    async def neodb_trending(
        self,
        category: str = "game",
        page: int = 1,
    ) -> List[dict]:
        """热门游戏榜单（公开接口，无需鉴权）。"""
        if not self._helper:
            return []
        items = await self._helper.async_trending(category or "game", page=page)
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
                name="NeoDB 热门游戏",
                api_path="plugin/NeoDBSource/neodb-trending?category=game",
                type="Games",
            ),
            RecommendMediaSource(
                name="NeoDB 科幻电影",
                api_path="plugin/NeoDBSource/neodb-discover?category=movie&q=%E7%A7%91%E5%B9%BB",
                type="Movies",
            ),
            RecommendMediaSource(
                name="NeoDB 动画剧集",
                api_path="plugin/NeoDBSource/neodb-discover?category=tv&q=%E5%8A%A8%E7%94%BB",
                type="TV Shows",
            ),
        ]
        if not event_data.extra_sources:
            event_data.extra_sources = sources
        else:
            event_data.extra_sources.extend(sources)

    @staticmethod
    def neodb_filter_ui() -> List[dict]:
        """探索页过滤器：关键词输入框 + 类别选择。"""
        category_ui = [
            {
                "component": "VTextField",
                "props": {
                    "model": "q",
                    "label": "搜索关键词",
                    "placeholder": "输入名称搜索（电影/剧集/图书/游戏/音乐）",
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
                    {"component": "VChip", "props": {"filter": True, "tile": True, "value": "book"}, "text": "图书"},
                    {"component": "VChip", "props": {"filter": True, "tile": True, "value": "game"}, "text": "游戏"},
                    {"component": "VChip", "props": {"filter": True, "tile": True, "value": "music"}, "text": "音乐"},
                ],
            },
        ]
        return category_ui

    # ------------------------------------------------------------------ #
    # 事件：媒体ID转换（neodb: -> tmdb）
    # ------------------------------------------------------------------ #
    @eventmanager.register(ChainEventType.MediaRecognizeConvert)
    async def async_media_recognize_covert(self, event: Event):
        if not self._enabled:
            return
        event_data: MediaRecognizeConvertEventData = event.event_data
        if not event_data:
            return
        if event_data.convert_type != "themoviedb":
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
    # 媒体识别（同步 / 异步）
    # ------------------------------------------------------------------ #
    def recognize_media(
        self,
        meta: MetaBase = None,
        mtype: MediaType = None,
        **kwargs,
    ) -> Optional[MediaInfo]:
        if not self._enabled:
            return None
        # 已有外部ID时不覆盖
        if kwargs.get("tmdbid") or kwargs.get("doubanid") or kwargs.get("bangumiid") or kwargs.get("anilistid"):
            return None
        # neodb 原生ID解析（详情页 neodb: 前缀进入）
        if kwargs.get("source") == "neodb" and kwargs.get("mediaid"):
            return self._recognize_by_neodb_id(kwargs["mediaid"], mtype)
        if not meta or not meta.name:
            return None
        else:
            if mtype:
                meta.type = mtype

        names = list(dict.fromkeys([meta.cn_name, meta.en_name]))
        names = [n for n in names if n]
        mtype_eff = mtype or meta.type

        for name in names:
            if not name:
                continue
            logger.info(f"正在通过 NeoDB 识别 {name} ...")
            if mtype_eff == MediaType.MOVIE:
                items = self._helper.search(name, "movie")
                match = NeoDBHelper.match_item(items, name, meta.year)
                if match:
                    return NeoDBHelper.item_to_mediainfo(match)
            elif mtype_eff == MediaType.TV:
                items = self._helper.search(name, "tv")
                match = NeoDBHelper.match_item(items, name, meta.year)
                if match:
                    return NeoDBHelper.item_to_mediainfo(match)
            else:
                for cat in ("movie", "tv"):
                    items = self._helper.search(name, cat)
                    match = NeoDBHelper.match_item(items, name, meta.year)
                    if match:
                        return NeoDBHelper.item_to_mediainfo(match)
        return None

    async def async_recognize_media(
        self,
        meta: MetaBase = None,
        mtype: MediaType = None,
        **kwargs,
    ) -> Optional[MediaInfo]:
        if not self._enabled:
            return None
        if kwargs.get("tmdbid") or kwargs.get("doubanid") or kwargs.get("bangumiid") or kwargs.get("anilistid"):
            return None
        if kwargs.get("source") == "neodb" and kwargs.get("mediaid"):
            return self._recognize_by_neodb_id(kwargs["mediaid"], mtype)
        if not meta or not meta.name:
            return None
        else:
            if mtype:
                meta.type = mtype

        names = list(dict.fromkeys([meta.cn_name, meta.en_name]))
        names = [n for n in names if n]
        mtype_eff = mtype or meta.type

        for name in names:
            if not name:
                continue
            logger.info(f"正在通过 NeoDB 识别 {name} ...")
            if mtype_eff == MediaType.MOVIE:
                items = await self._helper.async_search(name, "movie")
                match = NeoDBHelper.match_item(items, name, meta.year)
                if match:
                    return NeoDBHelper.item_to_mediainfo(match)
            elif mtype_eff == MediaType.TV:
                items = await self._helper.async_search(name, "tv")
                match = NeoDBHelper.match_item(items, name, meta.year)
                if match:
                    return NeoDBHelper.item_to_mediainfo(match)
            else:
                for cat in ("movie", "tv"):
                    items = await self._helper.async_search(name, cat)
                    match = NeoDBHelper.match_item(items, name, meta.year)
                    if match:
                        return NeoDBHelper.item_to_mediainfo(match)
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
