# Standalone logic test for ChineseSubFinder (stubs MoviePilot deps)
import sys, types, os

def fake_module(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m

# ---- app.plugins._PluginBase ----
app = fake_module("app")
app.plugins = fake_module("app.plugins")
class _PluginBase:
    def __init__(self): pass
    def get_data(self, *a, **k): return None
    def save_data(self, *a, **k): pass
    def update_config(self, *a, **k): pass
    def post_message(self, *a, **k): pass
    def stop_service(self): pass
app.plugins._PluginBase = _PluginBase

# ---- eventmanager / Event ----
core = fake_module("app.core")
app.core = core
event = fake_module("app.core.event")
def _register(*a, **k):
    def deco(f): return f
    return deco
event.eventmanager = type("EM", (), {"register": staticmethod(_register)})()
class _Event: pass
event.Event = _Event
core.event = event

# ---- config settings ----
cfg = fake_module("app.core.config")
class _Settings: TEMP_PATH = "/tmp"
cfg.settings = _Settings()
core.config = cfg

# ---- schemas ----
schemas = fake_module("app.schemas")
class Notification: pass
schemas.Notification = Notification
class TransferInfo: pass
schemas.TransferInfo = TransferInfo
types_mod = fake_module("app.schemas.types")
class EventType: TransferComplete = "TransferComplete"
class MediaType: MOVIE = "MOVIE"; TV = "TV"
class NotificationType: Plugin = "Plugin"
types_mod.EventType = EventType
types_mod.MediaType = MediaType
types_mod.NotificationType = NotificationType
schemas.types = types_mod

# ---- context MediaInfo ----
ctx = fake_module("app.core.context")
class MediaInfo: pass
ctx.MediaInfo = MediaInfo
core.context = ctx

# ---- log ----
log = fake_module("app.log")
class _Logger:
    def info(self,*a,**k): pass
    def warning(self,*a,**k): pass
    def error(self,*a,**k): pass
    def debug(self,*a,**k): pass
log.logger = _Logger()
app.log = log

# ---- http RequestUtils (stub) ----
http_mod = fake_module("app.utils.http")
class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or ("" if payload is None else str(payload))
    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload
class _ReqUtils:
    captured = {}
    queue = []
    def __init__(self, headers=None, timeout=None, **k):
        self.headers = headers
        self.timeout = timeout
    def post(self, url, json=None, **k):
        _ReqUtils.captured = {"url": url, "json": json, "headers": self.headers}
        # pop the configured response (for auth test), else default 200 ok
        if _ReqUtils.queue:
            return _ReqUtils.queue.pop(0)
        return _Resp(200, {"job_id": "JOB1", "message": "ok"})
    def get_res(self, url, **k):
        if _ReqUtils.queue:
            return _ReqUtils.queue.pop(0)
        return _Resp(200)
http_mod.RequestUtils = _ReqUtils
app.utils = fake_module("app.utils")
app.utils.http = http_mod

# ---- load plugin ----
repo = r"E:/github/MoviePilot-PluginsV2/plugins.v2"
import importlib.util
spec = importlib.util.spec_from_file_location(
    "chinesesubfinder", os.path.join(repo, "chinesesubfinder", "__init__.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
cls = mod.ChineseSubFinder

g = cls()
g.init_plugin({
    "enabled": True,
    "host": "192.168.1.10:19035",
    "api_key": "MYTOKEN",
    "local_path": "/movies",
    "remote_path": "/data/movies",
    "task_priority_level": 3,
    "notify": False,
})

# 1) host normalization
assert g._host == "http://192.168.1.10:19035/", g._host
print("host normalize OK ->", g._host)

# 2) request body + path mapping on TransferComplete
from pathlib import Path
class FakeMI:
    type = MediaType.MOVIE
class FakeTI:
    target_path = Path("/movies/A/2001.mkv")
    is_bluray = False
    file_list_new = ["/movies/A/2001.mkv"]
ev = type("E", (), {"event_data": {"mediainfo": FakeMI(), "transferinfo": FakeTI()}})()
g.download(ev)
c = _ReqUtils.captured
assert c["url"].endswith("api/v1/add-job"), c["url"]
assert c["headers"]["Authorization"] == "Bearer MYTOKEN", c["headers"]
j = c["json"]
assert j["video_type"] == 0, j
assert j["physical_video_file_full_path"] == "/data/movies/A/2001.mkv", j  # mapped
assert j["task_priority_level"] == 3, j
assert j["media_server_inside_video_id"] == "", j
assert j["is_bluray"] is False, j
print("request body + path mapping OK ->", j["physical_video_file_full_path"])

# 3) connection test: success
_ReqUtils.queue = [_Resp(200), _Resp(200)]
ok, msg = g._ChineseSubFinder__test_connection()
assert ok is True and "成功" in msg, (ok, msg)
print("test_connection success OK ->", msg)

# 4) connection test: auth 401 with message
_ReqUtils.queue = [_Resp(200), _Resp(401, {"message": "AccessToken Error"})]
ok, msg = g._ChineseSubFinder__test_connection()
assert ok is False and "AccessToken Error" in msg, (ok, msg)
print("test_connection 401 OK ->", msg)

# 5) file-not-found (200 but no job_id) should be treated as failure
_ReqUtils.queue = []
_ReqUtils.captured = {}
class FakeTI2:
    target_path = Path("/movies/B/show.mkv")
    is_bluray = False
    file_list_new = ["/movies/B/show.mkv"]
ev2 = type("E", (), {"event_data": {"mediainfo": FakeMI(), "transferinfo": FakeTI2()}})()
# make post return 200 with no job_id
_ReqUtils.queue = [_Resp(200, {"message": "physical video file not found"})]
g.download(ev2)
# no exception; we just verify it didn't crash and captured the body
assert _ReqUtils.captured["json"]["video_type"] == 0
print("file-not-found handled OK (no crash)")

print("ALL CSF TESTS PASSED")
