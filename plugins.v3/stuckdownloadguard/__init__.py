# _*_ coding: utf-8 _*_
"""
下载守卫（StuckDownloadGuard）

监控下载管理模块中的下载任务：
  - 若某下载任务「进度≈0 且 下载速度=0（不含排队时间）」持续达到设定时长（默认 30 分钟），
    则降低其优先级并排至下载队列队尾；
  - 若连续多次（默认 3 次）仍无进展，则停止种子、清理下载任务，并尝试重新搜索下载
    （订阅来源的下载会通过订阅链重新搜索，非订阅来源仅做停止与清理）。

说明：
  - 为准确区分「排队中」与「下载中」，本插件直接调用下载器原生客户端（qBittorrent / Transmission）
    读取原始状态，而非使用被 MoviePilot 归一化后的状态（归一化会把 queueddl 也归为 downloading）。
  - 仅处理带有 MoviePilot 标签（settings.TORRENT_TAG）的种子，即由 MoviePilot 管理的下载任务。
"""
import json
import time
import traceback
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

try:
    from app.chain.subscribe import SubscribeChain
except ImportError:
    SubscribeChain = None
try:
    from app.chain.download import DownloadChain
except ImportError:
    DownloadChain = None
try:
    from app.chain.search import SearchChain
except ImportError:
    SearchChain = None
from app.db.downloadhistory_oper import DownloadHistoryOper
from app.db.systemconfig_oper import SystemConfigOper
from app.helper.thread import ThreadHelper
from app.log import logger
from app.plugins import _PluginBase
from app.core.config import settings
from app.schemas.types import NotificationType, SystemConfigKey
try:
    from app.schemas.types import MediaType
except ImportError:
    try:
        from app.schemas.media import MediaType
    except ImportError:
        MediaType = None

# qBittorrent 被视为「活跃下载中」的原始状态（这些状态下进度可能为 0 但仍在尝试下载/校验）
_QB_ACTIVE_STATES = {
    "downloading", "stalleddl", "metadl", "forceddl", "checkingdl", "allocating",
}
# qBittorrent 排队状态（等待队列，不计入卡顿时长）
_QB_QUEUED_STATES = {"queueddl"}
# Transmission 活跃下载状态（status 为整数）：1 校验等待 2 校验中 4 下载中
_TR_ACTIVE_STATES = {1, 2, 4}
# Transmission 排队状态：3 下载待处理（排队）
_TR_QUEUED_STATES = {3}

# 进度低于该百分比（含）视为「0 进度」
_PROGRESS_ZERO_THRESHOLD = 0.1


class StuckDownloadGuard(_PluginBase):
    # 插件名称
    plugin_name = "下载守卫"
    # 插件描述
    plugin_desc = "监控下载任务，长时间无进度则降级排至队尾，连续无效则停止并重新搜索"
    # 插件图标
    plugin_icon = "Qbittorrent_A.png"
    # 插件版本
    plugin_version = "1.1.0"
    # 插件作者
    plugin_author = "narrator-z"
    # 作者主页
    author_url = "https://github.com/narrator-z"
    # 插件配置项ID前缀
    plugin_config_prefix = "stuck_download_guard_"
    # 加载顺序
    plugin_order = 20
    # 可使用的用户级别
    auth_level = 1

    # 私有属性
    _scheduler = None
    _cron = None
    _enabled = False
    _onlyonce = False
    # 卡住多少分钟后降级
    _inactive_minutes = 30
    # 连续降级多少次后升级为停止/清理/重新搜索
    _max_retries = 3
    # 仅处理订阅来源的下载（重新搜索需要订阅来源才有效）
    _only_subscribe = True
    # 升级处理时是否同时删除已下载文件
    _delete_files = False
    # 升级处理时是否删除对应的下载历史记录
    _delete_history = True
    # 是否发送通知
    _notify = True
    # 长期0速度时是否自动「切换下载源/种子」（降级+换源重搜后替换原种子）
    _switch_source = True
    # 每个 hash 的监控状态：{hash: {title, downloader, active_stuck, last_ts, retries, switch_attempted}}
    _states: Dict[str, dict] = {}

    def init_plugin(self, config: dict = None):
        """
        初始化插件
        """
        # 读取持久化状态
        try:
            self._states = self.get_data("torrent_states") or {}
        except Exception:
            self._states = {}

        # 读取配置
        if config:
            self._enabled = config.get("enabled", False)
            self._cron = config.get("cron") or "*/5 * * * *"
            self._onlyonce = config.get("onlyonce", False)
            try:
                self._inactive_minutes = int(config.get("inactive_minutes") or 30)
            except (TypeError, ValueError):
                self._inactive_minutes = 30
            try:
                self._max_retries = int(config.get("max_retries") or 3)
            except (TypeError, ValueError):
                self._max_retries = 3
            self._only_subscribe = config.get("only_subscribe", True)
            self._delete_files = config.get("delete_files", False)
            self._delete_history = config.get("delete_history", True)
            self._notify = config.get("notify", True)
            self._switch_source = config.get("switch_source", True)

        if not self._enabled:
            return

        # 停止已有任务
        self.stop_service()

        # 启动定时任务
        self._scheduler = BackgroundScheduler(timezone=settings.TZ)
        if self._cron:
            logger.info(f"【{self.plugin_name}】监控服务启动，周期：{self._cron}")
            self._scheduler.add_job(self.monitor, CronTrigger.from_crontab(self._cron))

        if self._onlyonce:
            logger.info(f"【{self.plugin_name}】开始立即运行一次监控")
            self._scheduler.add_job(
                self.monitor, 'date',
                run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3)
            )
            # 关闭一次性开关
            self._onlyonce = False
            self.__update_config()

        if self._cron or self._onlyonce:
            self._scheduler.print_jobs()
            self._scheduler.start()

    def get_state(self) -> bool:
        return self._enabled

    def stop_service(self):
        """
        退出插件
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error(f"【{self.plugin_name}】停止插件错误: {str(e)}")

    def __update_config(self):
        """
        更新插件配置
        """
        self.update_config({
            "enabled": self._enabled,
            "cron": self._cron,
            "onlyonce": False,
            "inactive_minutes": self._inactive_minutes,
            "max_retries": self._max_retries,
            "only_subscribe": self._only_subscribe,
            "delete_files": self._delete_files,
            "delete_history": self._delete_history,
            "notify": self._notify,
            "switch_source": self._switch_source,
        })

    def __save_states(self):
        """
        持久化监控状态，避免插件重启后重新计时
        """
        try:
            self.save_data("torrent_states", self._states)
        except Exception as e:
            logger.warning(f"【{self.plugin_name}】保存监控状态失败: {e}")

    # ----------------------------- 下载器客户端 -----------------------------

    @staticmethod
    def __build_qb_client(cfg: dict):
        import qbittorrentapi
        host = cfg.get("host")
        if not host:
            raise Exception("缺少 host 配置")
        parsed = urlparse(host)
        qhost = parsed.hostname or host
        qport = cfg.get("port") or parsed.port or 8080
        client = qbittorrentapi.Client(
            host=qhost,
            port=qport,
            username=cfg.get("username"),
            password=cfg.get("password"),
            VERIFY_WEBUI_CERTIFICATE=False,
        )
        client.auth_log_in()
        return client

    @staticmethod
    def __build_tr_client(cfg: dict):
        from transmission_rpc import Client
        host = cfg.get("host")
        if not host:
            raise Exception("缺少 host 配置")
        parsed = urlparse(host)
        protocol = parsed.scheme or "http"
        thost = parsed.hostname or host
        tport = cfg.get("port") or parsed.port or 9091
        tpath = parsed.path or "/transmission/rpc"
        if not tpath or tpath == "/":
            tpath = "/transmission/rpc"
        client = Client(
            host=thost,
            port=tport,
            path=tpath,
            username=cfg.get("username"),
            password=cfg.get("password"),
            protocol=protocol,
        )
        # 触发一次连接校验
        client.session_stats()
        return client

    @staticmethod
    def __conf_value(conf, key, default=None):
        """
        兼容 dict 与 pydantic 对象两种配置形态
        """
        if isinstance(conf, dict):
            return conf.get(key, default)
        return getattr(conf, key, default)

    def __collect(self) -> Tuple[List[dict], Dict[str, dict]]:
        """
        收集所有已启用下载器的 MoviePilot 托管种子。
        :return: (种子列表, {下载器名称: {type, client}})
        """
        torrents: List[dict] = []
        clients: Dict[str, dict] = {}
        try:
            configs = SystemConfigOper().get(SystemConfigKey.Downloaders) or []
        except Exception as e:
            logger.error(f"【{self.plugin_name}】读取下载器配置失败：{e}")
            return torrents, clients
        for conf in configs:
            name = self.__conf_value(conf, "name")
            enabled = self.__conf_value(conf, "enabled", False)
            if not enabled:
                continue
            dtype = (self.__conf_value(conf, "type") or "").lower()
            cfg = self.__conf_value(conf, "config") or {}
            try:
                if dtype == "qbittorrent":
                    client = self.__build_qb_client(cfg)
                    items = self.__qb_torrents(name, dtype, client)
                elif dtype == "transmission":
                    client = self.__build_tr_client(cfg)
                    items = self.__tr_torrents(name, dtype, client)
                else:
                    continue
                clients[name] = {"type": dtype, "client": client}
                torrents.extend(items)
            except Exception as e:
                logger.warning(f"【{self.plugin_name}】获取下载器 {name} 种子失败：{e}")
        return torrents, clients

    def __qb_torrents(self, name: str, dtype: str, client) -> List[dict]:
        tag = settings.TORRENT_TAG
        try:
            raw = client.torrents_info(tag=tag) if tag else client.torrents_info()
        except Exception:
            raw = client.torrents_info()
        out = []
        for tor in raw or []:
            out.append({
                "hash": tor.get("hash"),
                "title": tor.get("name"),
                "downloader": name,
                "type": dtype,
                "raw_state": (tor.get("state") or "").lower(),
                "progress": (tor.get("progress") or 0) * 100,
                "dl_speed": tor.get("dlspeed") or 0,
            })
        return out

    def __tr_torrents(self, name: str, dtype: str, client) -> List[dict]:
        tag = settings.TORRENT_TAG
        try:
            raw = client.get_torrents()
        except Exception as e:
            logger.warning(f"【{self.plugin_name}】Transmission 获取种子失败：{e}")
            return []
        out = []
        for tor in raw or []:
            labels = getattr(tor, "labels", None) or []
            if tag and tag not in labels:
                continue
            out.append({
                "hash": getattr(tor, "hashString", None),
                "title": getattr(tor, "name", None),
                "downloader": name,
                "type": dtype,
                "raw_state": getattr(tor, "status", None),
                "progress": (getattr(tor, "percent_done", 0) or 0) * 100,
                "dl_speed": getattr(tor, "rate_download", 0) or 0,
            })
        return out

    @staticmethod
    def __classify_state(dtype: str, raw_state) -> Tuple[bool, bool]:
        """
        判断原始状态是否为「活跃下载中」/「排队中」。
        :return: (is_active, is_queued)
        """
        if dtype == "qbittorrent":
            st = str(raw_state or "").lower()
            if st in _QB_QUEUED_STATES:
                return False, True
            if st in _QB_ACTIVE_STATES:
                return True, False
            return False, False
        if dtype == "transmission":
            try:
                st = int(raw_state)
            except (TypeError, ValueError):
                return False, False
            if st in _TR_QUEUED_STATES:
                return False, True
            if st in _TR_ACTIVE_STATES:
                return True, False
            return False, False
        return False, False

    @staticmethod
    def __source_of(history) -> Optional[str]:
        """
        从下载历史记录中取出来源（source）。
        """
        if not history:
            return None
        note = getattr(history, "note", None)
        if isinstance(note, str):
            try:
                note = json.loads(note)
            except Exception:
                return None
        if isinstance(note, dict):
            return note.get("source")
        return None

    # ----------------------------- 核心监控逻辑 -----------------------------

    def monitor(self):
        """
        周期性监控下载任务
        """
        try:
            torrents, clients = self.__collect()
            if not torrents:
                return

            # 批量查询来源，用于 only_subscribe 过滤
            hashes = [t["hash"] for t in torrents if t.get("hash")]
            source_map: Dict[str, str] = {}
            try:
                histories = DownloadHistoryOper().get_by_hashes(hashes) or {}
                for h, hist in histories.items():
                    src = self.__source_of(hist)
                    if src:
                        source_map[h] = src
            except Exception as e:
                logger.warning(f"【{self.plugin_name}】查询下载历史失败：{e}")

            now = time.time()
            seen = set()
            for t in torrents:
                h = t.get("hash")
                if not h:
                    continue

                # 仅订阅来源开关
                if self._only_subscribe:
                    src = source_map.get(h)
                    if not src or not str(src).startswith("Subscribe|"):
                        continue

                seen.add(h)
                active, queued = self.__classify_state(t["type"], t["raw_state"])
                stuck = active and t["progress"] < _PROGRESS_ZERO_THRESHOLD and t["dl_speed"] <= 0

                rec = self._states.get(h)
                if not rec:
                    rec = {
                        "title": t["title"],
                        "downloader": t["downloader"],
                        "active_stuck": 0.0,
                        "last_ts": None,
                        "retries": 0,
                        "switch_attempted": False,
                    }
                    self._states[h] = rec

                rec["title"] = t["title"]
                rec["downloader"] = t["downloader"]

                if stuck:
                    if rec["last_ts"] is not None:
                        rec["active_stuck"] += now - rec["last_ts"]
                    rec["last_ts"] = now
                    if rec["active_stuck"] >= self._inactive_minutes * 60:
                        self.__handle_stuck(h, rec, t, clients)
                elif queued:
                    # 排队中：暂停计时（不计入卡顿时长），不清零
                    rec["last_ts"] = None
                else:
                    # 已开始下载 / 速度>0 / 做种或完成：清零退出监控
                    self._states.pop(h, None)

            # 清理已不存在的种子记录
            for h in list(self._states.keys()):
                if h not in seen:
                    self._states.pop(h, None)

            self.__save_states()
        except Exception as e:
            logger.error(f"【{self.plugin_name}】监控出错：{str(e)} - {traceback.format_exc()}")

    def __handle_stuck(self, hash_str: str, rec: dict, t: dict, clients: Dict[str, dict]):
        """
        达到卡顿时长后的处理：先降级排至队尾，再尝试「切换下载源/种子」
        （重新搜索更优种子并替换原种子）；若切换不可用且连续多次仍无效，则升级为停止/清理。
        """
        rec["retries"] = rec.get("retries", 0) + 1
        # 1) 降级：降优先级并排至下载队列队尾（避免抢占正常下载的带宽/队列）
        self.__demote_and_move_tail(clients, t["downloader"], hash_str)

        # 2) 切换下载源：每个卡顿周期仅尝试一次，避免每次都重搜
        switched = False
        if self._switch_source and not rec.get("switch_attempted"):
            switched = self.__switch_source(hash_str, rec, t, clients)
            rec["switch_attempted"] = True

        # 3) 判定：已切换成功 → 停止追踪原种子（原种子将被移除，新种子下一轮单独监控）
        if switched:
            self._states.pop(hash_str, None)
            return

        # 4) 切换失败且已达最大降级次数 → 升级为停止/清理（订阅源会顺带重新搜索）
        if rec["retries"] >= self._max_retries:
            self.__escalate(clients, t["downloader"], hash_str, rec)
            self._states.pop(hash_str, None)
            return

        # 5) 未达上限：重置计时，开启下一轮观察窗口
        rec["active_stuck"] = 0.0
        rec["last_ts"] = time.time()
        self.__notify(
            action="降级并排至队尾（暂未切换源）",
            title=rec.get("title"),
            hash_str=hash_str,
            extra=f"已连续卡住 {rec['retries']} 次（达到 {self._max_retries} 次将停止并清理）",
        )

    def __switch_source(self, hash_str: str, rec: dict, t: dict, clients: Dict[str, dict]) -> bool:
        """
        尝试「切换下载源/种子」：为长期0速度的种子寻找更优替代源并替换。
          - 订阅来源：通过订阅链重新搜索（MoviePilot 会挑选最优可用种子）；
          - 非订阅来源：按媒体身份跨索引器重搜，选取做种人更多的替代种子后添加并移除原种子。
        返回 True 表示已成功切换（原种子将被移除），False 表示本次无法切换。
        """
        history = None
        try:
            history = DownloadHistoryOper().get_by_hash(hash_str)
        except Exception as e:
            logger.warning(f"【{self.plugin_name}】查询下载历史失败：{e}")
        if not history:
            logger.info(f"【{self.plugin_name}】未找到下载历史，无法切换源：{hash_str}")
            return False

        source = self.__source_of(history)
        # 订阅来源：直接重搜订阅（MP 选最优种子）
        if source and str(source).startswith("Subscribe|"):
            if SubscribeChain is None:
                logger.warning(f"【{self.plugin_name}】订阅链不可用，无法切换源")
                return False
            try:
                subscribe = SubscribeChain().get_subscribe_by_source(source)
            except Exception as e:
                logger.error(f"【{self.plugin_name}】获取订阅失败：{e}")
                return False
            if not subscribe:
                logger.warning(f"【{self.plugin_name}】未找到对应订阅：{source}")
                return False
            sid = subscribe.id
            try:
                SubscribeChain().search(sid=sid)
            except Exception as e:
                logger.error(f"【{self.plugin_name}】订阅《{subscribe.name}》重新搜索失败：{e}")
                return False
            # 重搜成功后再移除原卡住种子，避免空窗
            self.__stop_torrent(clients, t["downloader"], hash_str)
            self.__remove_torrent(clients, t["downloader"], hash_str, delete_files=self._delete_files)
            if self._delete_history:
                try:
                    DownloadHistoryOper().delete_history(history.id)
                except Exception:
                    pass
            self.__notify(
                action="已切换下载源（订阅重搜）",
                title=rec.get("title"),
                hash_str=hash_str,
                extra=f"已为订阅《{subscribe.name}》触发重新搜索并移除原卡住种子",
            )
            return True

        # 非订阅来源：跨索引器重搜更优种子
        return self.__switch_nonsub(hash_str, rec, t, history, clients)

    def __switch_nonsub(self, hash_str: str, rec: dict, t: dict, history, clients: Dict[str, dict]) -> bool:
        """
        非订阅来源：按媒体身份跨索引器重搜，选取做种人更多的替代种子并替换原种子。
        """
        if SearchChain is None or DownloadChain is None:
            logger.warning(f"【{self.plugin_name}】搜索/下载链不可用，无法切换非订阅源")
            return False
        title = getattr(history, "title", None) or t.get("title") or ""
        year = getattr(history, "year", None)
        keyword = f"{title} {year}".strip() if year else title
        if not keyword:
            logger.info(f"【{self.plugin_name}】下载历史无标题信息，无法重搜：{hash_str}")
            return False

        mtype = self.__history_mtype(history)
        try:
            contexts = SearchChain().search_by_title(title=keyword, sites=None, cache_local=False)
        except Exception as e:
            logger.error(f"【{self.plugin_name}】重搜「{keyword}」失败：{e}")
            return False
        if not contexts:
            logger.info(f"【{self.plugin_name}】未搜索到「{keyword}」的替代种子")
            return False

        # 过滤：有做种人、且非原种子同名
        stuck_name = (getattr(history, "torrent_name", None) or t.get("title") or "").strip().lower()
        candidates = []
        for ctx in contexts:
            ti = getattr(ctx, "torrent_info", None)
            if not ti:
                continue
            seeders = getattr(ti, "seeders", 0) or 0
            if seeders <= 0:
                continue
            # 排除与原种子完全相同的（多半就是它本身）
            if (getattr(ti, "title", "") or "").strip().lower() == stuck_name:
                continue
            candidates.append((seeders, ctx, ti))
        if not candidates:
            logger.info(f"【{self.plugin_name}】无更优替代种子（均无名或做种人=0）")
            return False

        # 选取做种人最多的替代种子
        candidates.sort(key=lambda x: x[0], reverse=True)
        seeders, ctx, ti = candidates[0]
        new_hash = self.__download_single(ctx, ti)
        if not new_hash:
            return False

        # 新种子已添加，移除原卡住种子
        self.__stop_torrent(clients, t["downloader"], hash_str)
        self.__remove_torrent(clients, t["downloader"], hash_str, delete_files=self._delete_files)
        if self._delete_history:
            try:
                DownloadHistoryOper().delete_history(history.id)
            except Exception:
                pass
        self.__notify(
            action="已切换下载源（重搜更优种子）",
            title=rec.get("title"),
            hash_str=hash_str,
            extra=f"新种子：{getattr(ti, 'title', '')}（做种人 {seeders}）",
        )
        return True

    @staticmethod
    def __history_mtype(history):
        """从下载历史推导媒体类型，用于限定重搜范围；推导失败返回 None。"""
        if MediaType is None:
            return None
        raw = getattr(history, "type", None)
        if not raw:
            return None
        try:
            return MediaType(raw)
        except Exception:
            mapping = {
                "电影": "MOVIE", "电视剧": "TV", "剧集": "TV", "综艺": "TV",
                "动漫": "ANIME", "动画": "ANIME", "MOVIE": "MOVIE", "TV": "TV",
                "ANIME": "ANIME", "MOVIE": "MOVIE",
            }
            try:
                return MediaType(mapping.get(str(raw), "MOVIE"))
            except Exception:
                return None

    @staticmethod
    def __download_single(ctx, ti) -> Optional[str]:
        """
        通过下载链添加替代种子；返回新种子 hash（成功）或 None（失败）。
        """
        if DownloadChain is None:
            return None
        content = getattr(ti, "enclosure", None)
        if not content:
            logger.warning(f"【{self.plugin_name}】候选种子缺少下载链接（enclosure），跳过切换")
            return None
        try:
            dl = DownloadChain()
            result = dl.download_single(
                context=ctx,
                torrent_content=content,
                label=settings.TORRENT_TAG,
            )
            if isinstance(result, tuple):
                return result[0] if result and result[0] else None
            return result
        except Exception as e:
            logger.error(f"【{self.plugin_name}】添加替代种子失败：{e}")
            return None

    def __demote_and_move_tail(self, clients: Dict[str, dict], downloader: str, hash_str: str) -> bool:
        """
        降低优先级并排至下载队列队尾。
        """
        info = clients.get(downloader)
        if not info:
            logger.warning(f"【{self.plugin_name}】未找到下载器 {downloader} 的连接，跳过降级")
            return False
        dtype = info["type"]
        client = info["client"]
        try:
            if dtype == "qbittorrent":
                client.torrents_bottom_priority(hashes=[hash_str])
                try:
                    client.torrents_decrease_priority(hashes=[hash_str])
                except Exception as e:
                    logger.debug(f"【{self.plugin_name}】qBittorrent 降低优先级失败（可忽略）：{e}")
                return True
            if dtype == "transmission":
                try:
                    client.queue_bottom(ids=[hash_str])
                except Exception as e:
                    logger.warning(f"【{self.plugin_name}】Transmission 排至队尾失败：{e}")
                try:
                    client.change_torrent(ids=[hash_str], bandwidthPriority=-1)
                except Exception as e:
                    logger.warning(f"【{self.plugin_name}】Transmission 降低优先级失败：{e}")
                return True
        except Exception as e:
            logger.error(f"【{self.plugin_name}】降级失败：{e}")
        return False

    def __escalate(self, clients: Dict[str, dict], downloader: str, hash_str: str, rec: dict):
        """
        停止种子、清理下载任务，并尝试重新搜索下载。
        """
        self.__stop_torrent(clients, downloader, hash_str)
        self.__remove_torrent(clients, downloader, hash_str, delete_files=self._delete_files)
        self.__research(hash_str, rec)

    def __stop_torrent(self, clients: Dict[str, dict], downloader: str, hash_str: str):
        info = clients.get(downloader)
        if not info:
            return
        try:
            if info["type"] == "qbittorrent":
                info["client"].torrents_stop(hashes=[hash_str])
            elif info["type"] == "transmission":
                info["client"].stop_torrent(ids=[hash_str])
        except Exception as e:
            logger.error(f"【{self.plugin_name}】停止种子失败：{e}")

    def __remove_torrent(self, clients: Dict[str, dict], downloader: str, hash_str: str, delete_files: bool):
        info = clients.get(downloader)
        if not info:
            return
        try:
            if info["type"] == "qbittorrent":
                info["client"].torrents_delete(hashes=[hash_str], delete_files=delete_files)
            elif info["type"] == "transmission":
                info["client"].remove_torrent(ids=[hash_str], delete_data=delete_files)
            logger.info(f"【{self.plugin_name}】已清理下载任务：{hash_str}（删除文件：{delete_files}）")
        except Exception as e:
            logger.error(f"【{self.plugin_name}】清理下载任务失败：{e}")

    def __research(self, hash_str: str, rec: dict):
        """
        尝试重新搜索下载（仅订阅来源有效）。
        """
        history = None
        try:
            history = DownloadHistoryOper().get_by_hash(hash_str)
        except Exception as e:
            logger.warning(f"【{self.plugin_name}】查询下载历史失败：{e}")

        if not history:
            logger.info(f"【{self.plugin_name}】未找到下载历史，无法重新搜索：{hash_str}")
            return

        source = self.__source_of(history)
        if not source or not str(source).startswith("Subscribe|"):
            self.__notify(
                action="停止并清理（未重新搜索）",
                title=rec.get("title"),
                hash_str=hash_str,
                extra="该下载非订阅来源，无重新搜索目标，仅停止并清理下载任务",
            )
            return

        # 可选：删除下载历史，便于重新搜索时不被误判为已下载
        if self._delete_history:
            try:
                DownloadHistoryOper().delete_history(history.id)
            except Exception as e:
                logger.warning(f"【{self.plugin_name}】删除下载历史失败：{e}")

        # 获取对应订阅并触发重新搜索
        if SubscribeChain is None:
            self.__notify(
                action="停止并清理（重新搜索不可用）",
                title=rec.get("title"),
                hash_str=hash_str,
                extra="订阅链不可用，无法自动重新搜索，仅停止并清理下载任务",
            )
            return

        try:
            subscribe = SubscribeChain().get_subscribe_by_source(source)
        except Exception as e:
            logger.error(f"【{self.plugin_name}】获取订阅失败：{e}")
            return

        if not subscribe:
            logger.warning(f"【{self.plugin_name}】未找到对应订阅：{source}")
            self.__notify(
                action="停止并清理（未找到订阅）",
                title=rec.get("title"),
                hash_str=hash_str,
                extra=f"未找到来源对应的订阅：{source}",
            )
            return

        sid = subscribe.id
        logger.info(f"【{self.plugin_name}】为订阅《{subscribe.name}》(#{sid}) 触发重新搜索")
        self.__notify(
            action="停止并重新搜索",
            title=rec.get("title"),
            hash_str=hash_str,
            extra=f"已为订阅《{subscribe.name}》触发重新搜索",
        )
        try:
            ThreadHelper().submit(self.__run_subscribe_search, sid)
        except Exception as e:
            logger.error(f"【{self.plugin_name}】提交重新搜索任务失败：{e}")

    @staticmethod
    def __run_subscribe_search(sid: int):
        """
        在后台线程中执行订阅重新搜索，避免阻塞监控调度。
        """
        if SubscribeChain is None:
            return
        try:
            SubscribeChain().search(sid=sid)
        except Exception as e:
            logger.error(f"【StuckDownloadGuard】订阅 #{sid} 重新搜索失败：{e}")

    def __notify(self, action: str, title: Optional[str], hash_str: str, extra: str = ""):
        if not self._notify:
            return
        try:
            self.post_message(
                mtype=NotificationType.Plugin,
                title=f"【下载守卫】{action}",
                text=f"种子：{title or '未知'}\nHash：{hash_str}\n{extra or ''}",
                source=self.plugin_name,
            )
        except Exception as e:
            logger.warning(f"【{self.plugin_name}】发送通知失败：{e}")

    # ----------------------------- 配置与页面 -----------------------------

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面
        """
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {'model': 'enabled', 'label': '启用插件'}
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {
                                        'model': 'notify',
                                        'label': '发送通知',
                                        'hint': '执行降级/停止/重新搜索等动作时发送通知'
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {
                                        'model': 'onlyonce',
                                        'label': '立即运行一次',
                                        'hint': '打开后立即运行一次监控，否则需等到下一个周期'
                                    }
                                }]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'cron',
                                        'label': '监控周期',
                                        'placeholder': '*/5 * * * *',
                                        'hint': '支持5位cron表达式，默认每5分钟检查一次'
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'inactive_minutes',
                                        'label': '卡顿时长(分钟)',
                                        'type': 'number',
                                        'placeholder': '30',
                                        'hint': '进度≈0且下载速度为0（不含排队时间）持续达到该时长后降级'
                                    }
                                }]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'max_retries',
                                        'label': '连续降级次数',
                                        'type': 'number',
                                        'placeholder': '3',
                                        'hint': '连续多次降级仍无效后，停止并清理（订阅源会顺带重新搜索）'
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {
                                        'model': 'only_subscribe',
                                        'label': '仅处理订阅来源',
                                        'hint': '开启后只监控订阅来源的下载；关闭则覆盖全部下载任务'
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {
                                        'model': 'switch_source',
                                        'label': '自动切换下载源',
                                        'hint': '长期0速度时自动重新搜索更优种子并替换原种子（降级+换源）；关闭则仅降级排队'
                                    }
                                }]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {
                                        'model': 'delete_files',
                                        'label': '清理时删除文件',
                                        'hint': '升级处理（停止+清理）时是否同时删除已下载文件，默认关闭避免误删'
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {
                                        'model': 'delete_history',
                                        'label': '清理时删除下载记录',
                                        'hint': '升级处理时删除对应下载历史，便于重新搜索不被误判为已下载'
                                    }
                                }]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [{
                                    'component': 'VAlert',
                                    'props': {
                                        'type': 'info',
                                        'variant': 'tonal',
                                'text': '仅处理带 MoviePilot 标签的种子。下载器需为 qBittorrent 或 Transmission。'
                                        '长期0速度时：先「降级」排至队尾，再「切换下载源」——'
                                        '订阅来源走订阅链重搜、非订阅来源跨索引器重搜更优种子并替换原种子；'
                                        '若切换不可用且连续多次无效，则停止并清理（订阅源会顺带重新搜索）。'
                                    }
                                }]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "notify": True,
            "onlyonce": False,
            "cron": "*/5 * * * *",
            "inactive_minutes": 30,
            "max_retries": 3,
            "only_subscribe": True,
            "switch_source": True,
            "delete_files": False,
            "delete_history": True,
        }

    def get_page(self) -> List[dict]:
        """
        拼装插件详情页面，展示当前被监控的卡住任务
        """
        rows = []
        for h, rec in (self._states or {}).items():
            minutes = int((rec.get("active_stuck") or 0) // 60)
            seconds = int((rec.get("active_stuck") or 0) % 60)
            rows.append({
                'component': 'tr',
                'content': [
                    {'component': 'td', 'text': rec.get("title") or "未知"},
                    {'component': 'td', 'text': rec.get("downloader") or ""},
                    {'component': 'td', 'text': f"{minutes}分{seconds}秒"},
                    {'component': 'td', 'text': str(rec.get("retries") or 0)},
                    {'component': 'td', 'text': h},
                ]
            })

        if not rows:
            return [{
                'component': 'VRow',
                'content': [{
                    'component': 'VCol',
                    'props': {'cols': 12},
                    'content': [{
                        'component': 'VAlert',
                        'props': {
                            'type': 'success',
                            'variant': 'tonal',
                            'text': '当前没有处于「进度为0且速度为0」监控中的下载任务。'
                        }
                    }]
                }]
            }]

        return [{
            'component': 'VRow',
            'content': [{
                'component': 'VCol',
                'props': {'cols': 12},
                'content': [{
                    'component': 'VTable',
                    'props': {'hover': True},
                    'content': [
                        {
                            'component': 'thead',
                            'content': [{
                                'component': 'tr',
                                'content': [
                                    {'component': 'th', 'props': {'class': 'text-start ps-4'}, 'text': '种子名称'},
                                    {'component': 'th', 'props': {'class': 'text-start ps-4'}, 'text': '下载器'},
                                    {'component': 'th', 'props': {'class': 'text-start ps-4'}, 'text': '已卡住时长'},
                                    {'component': 'th', 'props': {'class': 'text-start ps-4'}, 'text': '连续降级次数'},
                                    {'component': 'th', 'props': {'class': 'text-start ps-4'}, 'text': 'Hash'},
                                ]
                            }]
                        },
                        {'component': 'tbody', 'content': rows}
                    ]
                }]
            }]
        }]

    def get_api(self) -> List[Dict[str, Any]]:
        return []
