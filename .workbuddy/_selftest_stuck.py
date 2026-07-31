# Standalone logic test for StuckDownloadGuard (stubs MoviePilot deps)
import sys, types, time, importlib, importlib.util, os

def fake_module(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m

app = fake_module("app")
app.plugins = fake_module("app.plugins")

class _PluginBase:
    def __init__(self): pass
    def get_data(self, key=None, plugin_id=None): return None
    def save_data(self, key, value, plugin_id=None): pass
    def update_config(self, config, plugin_id=None): pass
    def post_message(self, *a, **k): pass
    def stop_service(self): pass
app.plugins._PluginBase = _PluginBase

core = fake_module("app.core")
cfg = fake_module("app.core.config")
class _Settings:
    TZ = "UTC"
    TORRENT_TAG = "MOVIEPILOT"
cfg.settings = _Settings()
core.config = cfg

schemas = fake_module("app.schemas")
class Notification: pass
schemas.Notification = Notification
types_mod = fake_module("app.schemas.types")
class NotificationType: Plugin = "插件"
class SystemConfigKey: Downloaders = "Downloaders"
types_mod.NotificationType = NotificationType
types_mod.SystemConfigKey = SystemConfigKey
schemas.types = types_mod

db = fake_module("app.db")
dho = fake_module("app.db.downloadhistory_oper")
class DownloadHistoryOper:
    @staticmethod
    def get_by_hashes(hashes): return {}
    @staticmethod
    def get_by_hash(h): return None
    @staticmethod
    def delete_history(hid): pass
dho.DownloadHistoryOper = DownloadHistoryOper
sys.modules["app.db.downloadhistory_oper"] = dho
sco = fake_module("app.db.systemconfig_oper")
class SystemConfigOper:
    @staticmethod
    def get(key): return []
sco.SystemConfigOper = SystemConfigOper
sys.modules["app.db.systemconfig_oper"] = sco

hlp = fake_module("app.helper")
th = fake_module("app.helper.thread")
class ThreadHelper:
    @staticmethod
    def submit(fn, *a, **k): return fn(*a, **k)
th.ThreadHelper = ThreadHelper
sys.modules["app.helper.thread"] = th

log = fake_module("app.log")
class _Logger:
    def info(self,*a,**k): pass
    def warning(self,*a,**k): pass
    def error(self,*a,**k): pass
    def debug(self,*a,**k): pass
log.logger = _Logger()

scsub = fake_module("app.chain.subscribe")
scsub.SubscribeChain = None
sys.modules["app.chain.subscribe"] = scsub

pytz_mod = fake_module("pytz")
pytz_mod.timezone = lambda name: name
sys.modules["pytz"] = pytz_mod
apsb = fake_module("apscheduler.schedulers.background")
class _BS:
    def __init__(self, *a, **k): pass
    def add_job(self, *a, **k): pass
    def print_jobs(self): pass
    def start(self): pass
    def shutdown(self): pass
    def remove_all_jobs(self): pass
    @property
    def running(self): return False
apsb.BackgroundScheduler = _BS
sys.modules["apscheduler.schedulers.background"] = apsb
apsc = fake_module("apscheduler.triggers.cron")
class _Cron:
    @staticmethod
    def from_crontab(c): return c
apsc.CronTrigger = _Cron
sys.modules["apscheduler.triggers.cron"] = apsc

# mutable clock
class Clock:
    val = 1_000_000.0
time.time = lambda: Clock.val

repo = r"E:/github/MoviePilot-PluginsV2/plugins.v2"
spec = importlib.util.spec_from_file_location("stuckdownloadguard", os.path.join(repo, "stuckdownloadguard", "__init__.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
cls = mod.StuckDownloadGuard

g = cls()
g._states = {}
g._inactive_minutes = 30
g._max_retries = 2
g._only_subscribe = False
g._delete_files = False
g._delete_history = False
g._notify = False

calls = {"demote": 0, "stop": 0, "remove": 0, "research": 0}
def fake_demote(self, clients, downloader, h): calls["demote"] += 1
def fake_stop(self, clients, downloader, h): calls["stop"] += 1
def fake_remove(self, clients, downloader, h, delete_files): calls["remove"] += 1
def fake_research(self, h, rec): calls["research"] += 1
g._StuckDownloadGuard__demote_and_move_tail = fake_demote.__get__(g)
g._StuckDownloadGuard__stop_torrent = fake_stop.__get__(g)
g._StuckDownloadGuard__remove_torrent = fake_remove.__get__(g)
g._StuckDownloadGuard__research = fake_research.__get__(g)

TOR = {"hash":"HASH1","title":"Test.Movie.2024.1080p","downloader":"qb-main","type":"qbittorrent","raw_state":"stalleddl","progress":0.0,"dl_speed":0}
CLIENTS = {"qb-main": {"type":"qbittorrent","client":object()}}
def fake_collect(self): return [dict(TOR)], CLIENTS
g._StuckDownloadGuard__collect = fake_collect.__get__(g)

assert cls._StuckDownloadGuard__classify_state("qbittorrent","stalleddl") == (True, False)
assert cls._StuckDownloadGuard__classify_state("qbittorrent","queueddl") == (False, True)
assert cls._StuckDownloadGuard__classify_state("qbittorrent","pauseddl") == (False, False)
assert cls._StuckDownloadGuard__classify_state("transmission",3) == (False, True)
assert cls._StuckDownloadGuard__classify_state("transmission",4) == (True, False)
print("classify_state OK")

# Scenario A: 31 min stuck (across two polls) -> demote
Clock.val = 1_000_000.0
g._states = {}
calls.update({k:0 for k in calls})
g.monitor()                          # baseline: last_ts=t0, 0 accrued
Clock.val = 1_000_000.0 + 31*60
g.monitor()                          # accrue 31min -> demote
rec = g._states["HASH1"]
assert calls["demote"] == 1, calls["demote"]
assert rec["retries"] == 1 and rec["active_stuck"] == 0.0, rec
print("Scenario A OK -> demote, retries=1, timer reset")

# Scenario B: another 31 min stuck (2nd round) -> escalate (stop+remove+research)
g._states = {}
calls.update({k:0 for k in calls})
Clock.val = 2_000_000.0
g.monitor()                          # baseline
Clock.val = 2_000_000.0 + 31*60
g.monitor()                          # demote #1 -> retries=1, timer reset
Clock.val = 2_000_000.0 + 31*60 + 31*60
g.monitor()                          # accrue 31min -> retries=2 >= max_retries -> escalate
assert calls["stop"] == 1 and calls["remove"] == 1 and calls["research"] == 1, calls
assert "HASH1" not in g._states
print("Scenario B OK -> stop+remove+research, record cleared")

# Scenario C: queue time excluded
g._states = {}
calls.update({k:0 for k in calls})
T0 = 3_000_000.0
Clock.val = T0
TOR["raw_state"] = "stalleddl"
g.monitor()                          # baseline, last_ts=T0, active_stuck=0
Clock.val = T0 + 20*60
g.monitor()                          # stuck 20min -> accrue 20min (1200)
Clock.val = T0 + 40*60
TOR["raw_state"] = "queueddl"
g.monitor()                          # queued -> pause, last_ts=None, no accrue (still 1200)
assert g._states["HASH1"]["active_stuck"] == 1200, g._states["HASH1"]
Clock.val = T0 + 65*60
TOR["raw_state"] = "stalleddl"
g.monitor()                          # back to active: re-baseline, no accrue (still 1200)
assert g._states["HASH1"]["active_stuck"] == 1200, g._states["HASH1"]
assert calls["demote"] == 0, calls["demote"]  # queue time correctly excluded, no premature demote
Clock.val = T0 + 80*60
g.monitor()                          # stuck 15min more -> accrue -> 35min -> demote
assert calls["demote"] == 1, calls["demote"]
print(f"Scenario C OK -> queue excluded (pre-queue {1200/60:.0f}min preserved), demote after ~35 active min")

print("ALL TESTS PASSED")
