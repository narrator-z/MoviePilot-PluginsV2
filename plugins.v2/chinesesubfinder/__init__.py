# _*_ coding: utf-8 _*_
"""
字幕守卫（ChineseSubFinder）

在 MoviePilot 整理入库（TransferComplete）时，通知 ChineseSubFinder 下载中文字幕。

相比官方 jxxghp/MoviePilot-Plugins 中的同名插件，本版本重点修复「调用 ChineseSubFinder API 失败！」
这一长期报错问题：

  - 官方插件在收到非 200 响应时只记录一句「调用ChineseSubFinder API失败！」，没有任何原因，
    用户无法判断是地址错误、Token 不对，还是 CSF 端文件不存在。
  - 经核对 ChineseSubFinder（含 morningstar-ski 优化版）源码，/api/v1 外部接口由
    middle.CheckApiAuth() 保护，要求请求头 `Authorization: Bearer <ApiToken>`，
    且这里的 Token 是 CSF 设置中的「外部 API Token」（common.GetApiToken()），
    **不是** Web 登录密码 / 访问令牌。Token 缺失或不匹配会直接返回 HTTP 401，
    这正是官方插件「API 失败」的根因。

本版本改进：
  1. 提供「测试连接」按钮（插件 API），一次性校验 地址可达性 + Token 有效性，并返回明确结论；
  2. 调用失败时记录 HTTP 状态码与 CSF 返回的具体 message（如 AccessToken Error /
     api_key_enabled == false / physical video file not found），让原因一目了然；
  3. 新增「发送通知」开关，失败时通过 MoviePilot 通知推送原因，不再静默；
  4. 修复官方插件蓝光原盘分支的表达式错误（原 `\"%s.mp4\" % item_dest / item_dest.name`
     会触发 TypeError）；
  5. 不缓存失败请求（官方用 lru_cache 缓存了 None 结果，导致失败后永不重试）。
"""
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.core.context import MediaInfo
from app.core.event import eventmanager, Event
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import Notification, TransferInfo
from app.schemas.types import EventType, MediaType, NotificationType
from app.utils.http import RequestUtils

# /api/v1 外部接口路径（与 ChineseSubFinder 源码 router.Group("/api/v1") 一致）
_API_ADD_JOB = "api/v1/add-job"
_API_JOB_STATUS = "api/v1/job-status"
# 无需鉴权的健康检查路径
_HEALTH = "system-status"


class ChineseSubFinder(_PluginBase):
    # 插件名称
    plugin_name = "ChineseSubFinder"
    # 插件描述
    plugin_desc = "整理入库时通知 ChineseSubFinder 下载字幕（修复 API 失败、增加连接测试与诊断）"
    # 插件图标
    plugin_icon = "chinesesubfinder.png"
    # 插件版本
    plugin_version = "6.0.0"
    # 插件作者
    plugin_author = "narrator-z"
    # 作者主页
    author_url = "https://github.com/narrator-z"
    # 插件配置项ID前缀
    plugin_config_prefix = "chinesesubfinder_"
    # 加载顺序
    plugin_order = 5
    # 可使用的用户级别
    auth_level = 1

    # 私有属性
    _enabled = False
    _host = None
    _api_key = None
    _remote_path = None
    _local_path = None
    _task_priority_level = 3
    _notify = True
    # 最近一次测试结果（供详情页展示）
    _last_test: Dict[str, Any] = {}

    def init_plugin(self, config: dict = None):
        if config:
            self._enabled = config.get("enabled") or False
            self._api_key = config.get("api_key") or ""
            self._host = self.__normalize_host(config.get("host") or "")
            self._local_path = config.get("local_path") or ""
            self._remote_path = config.get("remote_path") or ""
            try:
                self._task_priority_level = int(config.get("task_priority_level") or 3)
            except (TypeError, ValueError):
                self._task_priority_level = 3
            self._notify = config.get("notify", True)
            if self._enabled and not self._api_key:
                logger.warning("【ChineseSubFinder】已启用但未配置 API Token，"
                               "请在 CSF 设置中开启外部 API 并填写其 ApiToken（不是 Web 登录密码）")

    @staticmethod
    def __normalize_host(host: str) -> str:
        if not host:
            return ""
        if not host.startswith("http"):
            host = "http://" + host
        if not host.endswith("/"):
            host = host + "/"
        return host

    def get_state(self) -> bool:
        return self._enabled

    def stop_service(self):
        pass

    # ----------------------------- 配置表单 -----------------------------

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {'model': 'enabled', 'label': '启用插件'}
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {
                                        'model': 'notify',
                                        'label': '发送通知',
                                        'hint': '调用失败时将原因通过 MoviePilot 通知推送'
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
                                        'model': 'host',
                                        'label': '服务器地址',
                                        'placeholder': 'http://192.168.1.10:19035',
                                        'hint': 'ChineseSubFinder 的 WebUI 地址，含端口'
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'api_key',
                                        'label': 'API Token',
                                        'hint': 'CSF 设置中的「外部 API Token」，不是 Web 登录密码'
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
                                        'model': 'local_path',
                                        'label': '本地路径',
                                        'hint': 'MoviePilot 看到的媒体路径前缀'
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'remote_path',
                                        'label': '远端路径',
                                        'hint': 'CSF 容器看到的相同媒体路径前缀（用于路径替换）'
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
                                        'model': 'task_priority_level',
                                        'label': '任务优先级',
                                        'type': 'number',
                                        'placeholder': '3',
                                        'hint': '传给 CSF 的 task_priority_level，默认 3'
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
                                        'text': 'API Token 填写 ChineseSubFinder 设置里的「外部 API Token」'
                                                '（common.ApiToken），并非 Web 登录密码。若未设置或留空，'
                                                '外部接口会返回 401「AccessToken Error / api_key_enabled == false」，'
                                                '这正是官方插件「调用 API 失败」的根因。配置后可用下方「测试连接」按钮验证。'
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
            "host": "",
            "api_key": "",
            "local_path": "",
            "remote_path": "",
            "task_priority_level": 3,
        }

    # ----------------------------- 详情页 -----------------------------

    def get_page(self) -> List[dict]:
        test = self._last_test or {}
        ok = test.get("success")
        msg = test.get("message") or "尚未测试"
        color = "success" if ok else ("error" if ok is False else "info")
        return [{
            'component': 'VRow',
            'content': [{
                'component': 'VCol',
                'props': {'cols': 12},
                'content': [{
                    'component': 'VAlert',
                    'props': {
                        'type': color,
                        'variant': 'tonal',
                        'text': f'最近连接测试：{msg}'
                    }
                }]
            }]
        }]

    # ----------------------------- 测试连接 API -----------------------------

    def get_api(self) -> List[Dict[str, Any]]:
        return [{
            "path": "/test",
            "endpoint": self.test,
            "methods": ["GET"],
            "summary": "测试 ChineseSubFinder 连接与认证"
        }]

    def test(self) -> Dict[str, Any]:
        """
        供「测试连接」按钮调用：校验服务器可达性 + Token 有效性。
        """
        success, message = self.__test_connection()
        self._last_test = {"success": success, "message": message}
        return {"success": success, "message": message}

    def __test_connection(self) -> Tuple[bool, str]:
        if not self._host:
            return False, "未配置服务器地址"
        if not self._api_key:
            return False, "未配置 API Token（CSF 外部 API Token，不是 Web 登录密码）"
        # 1) 可达性（无需鉴权）
        try:
            health = RequestUtils(timeout=10).get_res(self._host + _HEALTH)
            if not health:
                return False, "无法连接服务器（无响应，请检查地址与端口）"
            if health.status_code != 200:
                return False, f"服务器返回异常 HTTP {health.status_code}（请确认地址正确）"
        except Exception as e:
            return False, f"连接服务器失败：{e}"
        # 2) 认证（/api/v1 需 Bearer ApiToken）
        try:
            auth = RequestUtils(
                headers={"Authorization": "Bearer %s" % self._api_key},
                timeout=10,
            ).get_res(self._host + _API_JOB_STATUS + "?job_id=__mp_test__")
            if not auth:
                return False, "认证请求无响应"
            if auth.status_code == 200:
                return True, "连接并认证成功（地址可达、Token 有效）"
            body = ""
            try:
                body = auth.json().get("message", "") or auth.text
            except Exception:
                body = auth.text or ""
            return False, f"认证失败（HTTP {auth.status_code}）：{body}"
        except Exception as e:
            return False, f"认证请求异常：{e}"

    # ----------------------------- 入库事件 -----------------------------

    @eventmanager.register(EventType.TransferComplete)
    def download(self, event: Event):
        """
        整理入库完成后，通知 ChineseSubFinder 下载字幕。
        """
        if not self._enabled or not self._host or not self._api_key:
            return
        item = event.event_data
        if not item:
            return

        item_media: MediaInfo = item.get("mediainfo")
        item_transfer: TransferInfo = item.get("transferinfo")
        if not item_media or not item_transfer:
            return

        item_type = item_media.type
        item_bluray = item_transfer.is_bluray
        item_dest: Path = item_transfer.target_path
        item_file_list = item_transfer.file_list_new or []

        if item_bluray:
            # 蓝光原盘：CSF 对蓝光跳过文件存在性检查，直接传目录路径并标记 is_bluray
            item_file_list = [str(item_dest)]

        for file_path in item_file_list:
            file_path = str(file_path)
            # 路径替换：把 MoviePilot 侧的本地路径前缀换成 CSF 容器侧能访问的远端路径
            if self._local_path and self._remote_path and file_path.startswith(self._local_path):
                file_path = file_path.replace(self._local_path, self._remote_path).replace("\\", "/")

            self.__request_csf(
                file_path=file_path,
                item_type=0 if item_type == MediaType.MOVIE else 1,
                item_bluray=bool(item_bluray),
            )

    # ----------------------------- 调用 CSF -----------------------------

    def __request_csf(self, file_path: str, item_type: int, item_bluray: bool):
        req_url = "%s%s" % (self._host, _API_ADD_JOB)
        params = {
            "video_type": item_type,
            "physical_video_file_full_path": file_path,
            "task_priority_level": self._task_priority_level,
            "media_server_inside_video_id": "",
            "is_bluray": item_bluray,
        }
        logger.info("通知 ChineseSubFinder 下载字幕: %s (type=%s, bluray=%s)" % (
            file_path, item_type, item_bluray))
        try:
            res = RequestUtils(
                headers={"Authorization": "Bearer %s" % self._api_key},
                timeout=30,
            ).post(req_url, json=params)
            if res is None:
                self.__fail(file_path, "请求无响应（请检查服务器地址与网络）")
                return
            if res.status_code != 200:
                body = ""
                try:
                    body = res.json().get("message", "") or res.text
                except Exception:
                    body = res.text or ""
                self.__fail(file_path, "HTTP %s：%s" % (res.status_code, body))
                return
            # HTTP 200：解析返回（即使无字幕任务，CSF 也返回 200）
            try:
                data = res.json()
            except Exception:
                logger.info("ChineseSubFinder 任务添加成功（无 JSON 响应）：%s" % file_path)
                return
            job_id = data.get("job_id")
            message = data.get("message", "")
            if not job_id:
                # 典型情况：physical video file not found —— 文件在 CSF 侧不存在（路径映射问题）
                logger.warning("ChineseSubFinder 未添加任务：%s（%s）" % (file_path, message))
                self.__fail(
                    file_path,
                    "未添加任务：%s（检查路径映射，CSF 端需能访问该文件）" % message,
                )
                return
            logger.info("ChineseSubFinder 任务添加成功：%s (job_id=%s)" % (file_path, job_id))
            if self._notify:
                self.__notify("添加成功", file_path, "job_id=%s" % job_id)
        except Exception as e:
            logger.error("连接 ChineseSubFinder 出错：" + str(e))
            self.__fail(file_path, "连接出错：%s" % str(e))

    # ----------------------------- 通知 -----------------------------

    def __fail(self, file_path: str, reason: str):
        logger.error("调用 ChineseSubFinder API 失败！%s | %s" % (file_path, reason))
        if self._notify:
            self.__notify("调用失败", file_path, reason)

    def __notify(self, action: str, file_path: str, extra: str = ""):
        try:
            self.post_message(Notification(
                mtype=NotificationType.Plugin,
                title=f"【字幕守卫】{action}",
                text=f"文件：{file_path}\n{extra or ''}",
                source=self.plugin_name,
            ))
        except Exception as e:
            logger.warning("【ChineseSubFinder】发送通知失败：%s" % e)
