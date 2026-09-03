"""
NeoDB API 辅助模块

封装 NeoDB 公开 API（catalog search / item 详情 / 热门游戏）的同步与异步请求，
并负责把 NeoDB 条目映射为 MoviePilot 的 MediaInfo。

文档参考：
- https://neodb.social/developer/  (Swagger 文档)
- https://neodb.net/api/

主要端点：
- GET /api/catalog/search        ?query=<关键词>&category=<类别>&page=<页码>   搜索目录（公开）
- GET /api/{category}/{uuid}/    条目详情（公开，external_resources 含 tmdb/douban/imdb 映射）
- GET /api/trending/game/        热门游戏（公开，无需鉴权）

说明：NeoDB 条目的 tmdb_id 不直接给出，而是体现在 external_resources 的
themoviedb.org URL 中，因此需要解析提取。
"""

import re
from typing import Dict, List, Optional

from app.core.context import MediaInfo
from app.schemas.types import MediaType
from app.utils.http import AsyncRequestUtils, RequestUtils

# NeoDB 类别 -> MoviePilot 媒体类型
_CATEGORY_TO_MTYPE = {
    "movie": MediaType.MOVIE,
    "tv": MediaType.TV,
    "anime": MediaType.TV,  # 动画归为剧集处理
}

# 可识别的外部资源 host 与对应正则（用于提取 tmdb / douban / imdb id）
_TMDB_RE = re.compile(r"themoviedb\.org/(?:movie|tv)/(\d+)")
_DOUBAN_RE = re.compile(r"movie\.douban\.com/subject/(\d+)")
_IMDB_RE = re.compile(r"imdb\.com/title/(tt\d+)")


class NeoDBHelper:
    """NeoDB 公开 API 客户端。"""

    def __init__(self, base_url: str = "https://neodb.social", proxies=None):
        self.base_url = (base_url or "https://neodb.social").rstrip("/")
        self.proxies = proxies

    # ------------------------------------------------------------------ #
    # 同步请求
    # ------------------------------------------------------------------ #
    def search(self, query: str, category: Optional[str] = None, page: int = 1) -> List[dict]:
        """目录搜索，返回 data 列表（原始条目字典）。"""
        if not query:
            return []
        params = {"query": query, "page": page}
        if category:
            params["category"] = category
        data = RequestUtils(accept_type="application/json", proxies=self.proxies).get_json(
            f"{self.base_url}/api/catalog/search", params=params
        )
        if isinstance(data, dict):
            return data.get("data") or []
        return []

    def get_item(self, category: str, uuid: str) -> Optional[dict]:
        """获取单个条目详情。"""
        if not category or not uuid:
            return None
        data = RequestUtils(accept_type="application/json", proxies=self.proxies).get_json(
            f"{self.base_url}/api/{category}/{uuid}"
        )
        if isinstance(data, dict):
            return data
        return None

    def trending(self, category: str = "game", page: int = 1) -> List[dict]:
        """获取某分类热门榜单（公开，无需鉴权）。category 支持 movie/tv/book/game/music；anime 归入 tv。"""
        if category == "anime":
            category = "tv"
        data = RequestUtils(accept_type="application/json", proxies=self.proxies).get_json(
            f"{self.base_url}/api/trending/{category}/", params={"page": page}
        )
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("data") or []
        return []

    def get_credit(self, category: str, uuid: str) -> Optional[dict]:
        """获取条目演职员（公开，无需鉴权）。返回 {data:[...], pages, count}。"""
        if not category or not uuid:
            return None
        data = RequestUtils(accept_type="application/json", proxies=self.proxies).get_json(
            f"{self.base_url}/api/catalog/{category}/{uuid}/credit/"
        )
        if isinstance(data, dict):
            return data
        return None

    def get_similar(self, uuid: str, token: str) -> Optional[dict]:
        """获取单条相似推荐（需 OAuth2 Bearer token）。返回 {data:[...], pages, count}。"""
        if not uuid or not token:
            return None
        resp = RequestUtils(accept_type="application/json", proxies=self.proxies).request(
            "get",
            f"{self.base_url}/api/catalog/item/{uuid}/similar",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if resp is None or getattr(resp, "status_code", 0) != 200:
            return None
        try:
            return resp.json()
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    # 异步请求
    # ------------------------------------------------------------------ #
    async def async_search(self, query: str, category: Optional[str] = None, page: int = 1) -> List[dict]:
        if not query:
            return []
        params = {"query": query, "page": page}
        if category:
            params["category"] = category
        data = await AsyncRequestUtils(accept_type="application/json", proxies=self.proxies).get_json(
            f"{self.base_url}/api/catalog/search", params=params
        )
        if isinstance(data, dict):
            return data.get("data") or []
        return []

    async def async_get_item(self, category: str, uuid: str) -> Optional[dict]:
        if not category or not uuid:
            return None
        data = await AsyncRequestUtils(accept_type="application/json", proxies=self.proxies).get_json(
            f"{self.base_url}/api/{category}/{uuid}"
        )
        if isinstance(data, dict):
            return data
        return None

    async def async_trending(self, category: str = "game", page: int = 1) -> List[dict]:
        """异步获取某分类热门榜单（公开，无需鉴权）。category 支持 movie/tv/book/game/music；anime 归入 tv。"""
        if category == "anime":
            category = "tv"
        data = await AsyncRequestUtils(accept_type="application/json", proxies=self.proxies).get_json(
            f"{self.base_url}/api/trending/{category}/", params={"page": page}
        )
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("data") or []
        return []

    async def async_get_credit(self, category: str, uuid: str) -> Optional[dict]:
        """异步获取条目演职员（公开，无需鉴权）。"""
        if not category or not uuid:
            return None
        data = await AsyncRequestUtils(accept_type="application/json", proxies=self.proxies).get_json(
            f"{self.base_url}/api/catalog/{category}/{uuid}/credit/"
        )
        if isinstance(data, dict):
            return data
        return None

    async def async_get_similar(self, uuid: str, token: str) -> Optional[dict]:
        """异步获取单条相似推荐（需 OAuth2 Bearer token）。"""
        if not uuid or not token:
            return None
        resp = await AsyncRequestUtils(accept_type="application/json", proxies=self.proxies).request(
            "get",
            f"{self.base_url}/api/catalog/item/{uuid}/similar",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if resp is None or getattr(resp, "status_code", 0) != 200:
            return None
        try:
            return resp.json()
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    # 解析 / 映射
    # ------------------------------------------------------------------ #
    @staticmethod
    def extract_tmdb_id(item: dict) -> Optional[int]:
        """从 external_resources 的 URL 中提取 tmdb id。"""
        resources = item.get("external_resources") or []
        for res in resources:
            url = res.get("url", "") if isinstance(res, dict) else str(res)
            m = _TMDB_RE.search(url)
            if m:
                return int(m.group(1))
        return None

    @staticmethod
    def extract_douban_id(item: dict) -> Optional[str]:
        """从 external_resources 的 URL 中提取豆瓣 id。"""
        resources = item.get("external_resources") or []
        for res in resources:
            url = res.get("url", "") if isinstance(res, dict) else str(res)
            m = _DOUBAN_RE.search(url)
            if m:
                return m.group(1)
        return None

    @staticmethod
    def extract_imdb_id(item: dict) -> Optional[str]:
        """从 external_resources 的 URL 中提取 imdb id。"""
        resources = item.get("external_resources") or []
        for res in resources:
            url = res.get("url", "") if isinstance(res, dict) else str(res)
            m = _IMDB_RE.search(url)
            if m:
                return m.group(1)
        return None

    @staticmethod
    def category_to_mtype(category: str) -> MediaType:
        return _CATEGORY_TO_MTYPE.get(category, MediaType.UNKNOWN)

    @staticmethod
    def credit_to_actors(credit: Optional[dict]) -> List[dict]:
        """
        把 NeoDB credit 端点返回的演职员列表映射为 MoviePilot 详情页演员表结构。
        每个 actor dict 需含 name / character / profile_path（前端演员表渲染用）。
        """
        out: List[dict] = []
        if not isinstance(credit, dict):
            return out
        for c in credit.get("data") or []:
            if not isinstance(c, dict):
                continue
            person = c.get("person") or {}
            name = person.get("display_name") or c.get("name") or ""
            if not name:
                continue
            character = c.get("character_name") or ""
            avatar = person.get("cover_image_url") or ""
            out.append(
                {
                    "id": person.get("uuid") or c.get("id"),
                    "name": name,
                    "character": character,
                    "profile_path": avatar,
                }
            )
        return out

    @staticmethod
    def similar_to_text(similar: Optional[dict], limit: int = 10) -> str:
        """
        把 NeoDB similar 端点返回的相似条目整理成可注入详情页简介的文本块。
        详情页 API 模型无独立的 recommend/similar 字段，故以文本形式追加到 overview。
        """
        items = []
        if isinstance(similar, dict):
            items = similar.get("data") or []
        elif isinstance(similar, list):
            items = similar
        lines = []
        for it in items[:limit]:
            if not isinstance(it, dict):
                continue
            t = it.get("display_title") or it.get("title") or ""
            if t:
                lines.append(f"- {t}")
        if not lines:
            return ""
        return "🎬 NeoDB 相似推荐：\n" + "\n".join(lines)

    @staticmethod
    def item_to_mediainfo(item: dict) -> MediaInfo:
        """
        把 NeoDB 条目转换为 MediaInfo。
        - 统一设置 source="neodb"、media_id="<category>.<uuid>"，便于详情页回链。
        - 若能从 external_resources 解析出 tmdb_id，则一并写入（电影/剧集可进一步被 TMDB 流程使用）。
        - 图书/游戏/音乐等无 tmdb 的类别，直接以 NeoDB 数据展示（封面/评分/简介）。
        """
        category = item.get("category") or "movie"
        uuid = item.get("uuid")
        mtype = NeoDBHelper.category_to_mtype(category)

        mi = MediaInfo()
        mi.type = mtype
        mi.title = item.get("display_title") or item.get("title") or ""
        year = item.get("year")
        if year is not None:
            # MediaInfo.year 字段类型为 str，必须转换为字符串，
            # 否则详情接口返回时触发 ResponseValidationError -> HTTP 500。
            mi.year = str(year)
        cover = item.get("cover_image_url")
        if cover:
            mi.cover = cover
            # 探索/推荐列表卡片通常优先取 poster_path；这里把可用的封面同步到
            # poster_path，避免列表缩略图因 poster_path 为空而不显示。
            # 详情页仍会由核心的 obtain_images 用 TMDB/fanart/douban 图片覆盖，
            # 若 TMDB 海报路径失效，本封面也能作为兜底。
            if not mi.poster_path:
                mi.poster_path = cover
        rating = item.get("rating")
        if rating is not None:
            try:
                mi.rating = float(rating)
            except (TypeError, ValueError):
                pass
        overview = item.get("brief") or item.get("description") or ""
        if overview and isinstance(overview, str):
            mi.overview = overview[:800]

        # 名称（含中文别名）
        names = [item.get("title")]
        for lt in item.get("localized_title") or []:
            if isinstance(lt, dict) and str(lt.get("lang", "")).startswith("zh"):
                names.append(lt.get("text"))
        mi.names = [n for n in names if n]

        # 标识
        mi.source = "neodb"
        if uuid:
            mi.media_id = f"{category}.{uuid}"

        tmdb_id = NeoDBHelper.extract_tmdb_id(item)
        if tmdb_id:
            mi.tmdb_id = tmdb_id

        douban_id = NeoDBHelper.extract_douban_id(item)
        if douban_id:
            mi.douban_id = douban_id

        imdb_id = NeoDBHelper.extract_imdb_id(item)
        if imdb_id:
            mi.imdb_id = imdb_id

        return mi

    @staticmethod
    def match_item(items: List[dict], name: str, year: Optional[int] = None) -> Optional[dict]:
        """
        在搜索结果中挑选与给定名称/年份最匹配的条目。
        优先精确匹配标题且年份接近；其次退化为首个标题匹配项。
        """
        if not items or not name:
            return None
        name_low = name.lower().strip()
        fallback = None
        for it in items:
            titles = [it.get("title")]
            for lt in it.get("localized_title") or []:
                if isinstance(lt, dict):
                    titles.append(lt.get("text"))
            titles = [t for t in titles if t]
            title_hit = any(name_low == t.lower() for t in titles) or \
                any(name_low in t.lower() for t in titles)
            if not title_hit:
                continue
            it_year = it.get("year")
            year_ok = True
            if year is not None and it_year:
                try:
                    year_ok = abs(int(it_year) - int(year)) <= 1
                except (TypeError, ValueError):
                    year_ok = True
            if year_ok:
                return it
            if fallback is None:
                fallback = it
        return fallback
