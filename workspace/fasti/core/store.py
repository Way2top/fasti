"""存储层：数据目录解析、事件日志读写、原子写、单写锁、备份（C2）。

本模块只做 IO 与编码，不含领域语义（语义在 fold.py / validate.py）。
所有写操作遵循 conventions.concurrency.atomic_write：
同目录 .tmp → flush → fsync(fd) → os.replace → fsync(dir)。
"""

import contextlib
import fcntl
import hashlib
import json
import os
import time

from . import constants
from . import errors


# ---------------------------------------------------------------- 路径解析
def expand_home(path):
    """展开 ~（尊重 HOME 环境变量）。"""
    return os.path.abspath(os.path.expanduser(path))


def resolve_data_dir(explicit=None):
    """唯一允许读取 FASTI_HOME 的函数（api-spec helpers.resolve_data_dir）。

    优先级：--data-dir > FASTI_HOME > ~/.fasti
    """
    if explicit:
        candidate = explicit
    else:
        env = os.environ.get(constants.ENV_HOME_VAR)
        if env:
            candidate = env
        else:
            candidate = os.path.join("~", constants.DEFAULT_DATA_DIR_NAME)
    return expand_home(candidate)


def paths(data_dir):
    data_dir = os.path.abspath(data_dir)
    return {
        "data_dir": data_dir,
        "events": os.path.join(data_dir, constants.EVENTS_FILE),
        "profile_current": os.path.join(data_dir, constants.PROFILE_CURRENT),
        "profile_history": os.path.join(data_dir, constants.PROFILE_HISTORY),
        "dist_agents": os.path.join(data_dir, constants.DIST_AGENTS),
        "dist_claude": os.path.join(data_dir, constants.DIST_CLAUDE),
        "dist_cursor": os.path.join(data_dir, constants.DIST_CURSOR),
        "dist_install": os.path.join(data_dir, constants.DIST_INSTALL),
        "manifest": os.path.join(data_dir, constants.MANIFEST_FILE),
        "lock": os.path.join(data_dir, constants.LOCK_FILE),
        "backups": os.path.join(data_dir, constants.BACKUPS_DIR),
        "exports": os.path.join(data_dir, constants.EXPORTS_DIR),
        "profile_dir": os.path.join(data_dir, "profile"),
        "dist_dir": os.path.join(data_dir, "dist"),
        "state_dir": os.path.join(data_dir, "state"),
    }


def artifact_abs_path(data_dir, rel_path):
    return os.path.join(os.path.abspath(data_dir), rel_path)


# ------------------------------------------------------------------ 目录
def ensure_dir(path, mode=0o700):
    if not os.path.isdir(path):
        os.makedirs(path, mode=mode, exist_ok=True)


def ensure_store_dirs(data_dir):
    """创建数据目录与运行期子目录（权限 0700）。"""
    p = paths(data_dir)
    ensure_dir(p["data_dir"])
    for key in ("profile_dir", "dist_dir", "state_dir", "backups", "exports"):
        ensure_dir(p[key])
    ensure_dir(os.path.join(p["dist_dir"], ".cursor", "rules"))


def is_initialized(data_dir):
    return os.path.isfile(paths(data_dir)["events"])


def require_initialized(data_dir):
    if not is_initialized(data_dir):
        raise errors.make("STORE_NOT_INITIALIZED")
    return paths(data_dir)


# ------------------------------------------------------------------ 读取
def read_bytes(path):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as exc:
        raise errors.make("IO_READ_FAILED", "读取文件失败：%s（%s）" % (path, exc),
                          location=path)


def read_text(path):
    return read_bytes(path).decode("utf-8")


def read_events_bytes(data_dir):
    return read_bytes(paths(data_dir)["events"])


def parse_event_line(raw_line, line_number, source):
    """解析单行事件 JSON；失败 ⇒ CORRUPT_EVENT_LOG，定位到行号。"""
    text = raw_line.strip()
    try:
        value = json.loads(text)
    except (ValueError, UnicodeDecodeError) as exc:
        raise errors.make("CORRUPT_EVENT_LOG", "事件日志第 %d 行不是合法 JSON：%s" % (line_number, exc),
                          location="%s:%d" % (source, line_number))
    if not isinstance(value, dict):
        raise errors.make("CORRUPT_EVENT_LOG", "事件日志第 %d 行不是 JSON 对象" % line_number,
                          location="%s:%d" % (source, line_number))
    return value


def read_events(data_dir):
    """读取事件日志（list[dict]）。"""
    require_initialized(data_dir)
    raw = read_events_bytes(data_dir)
    return parse_events_bytes(raw)


def parse_events_bytes(raw):
    text = raw.decode("utf-8")
    events = []
    for index, line in enumerate(text.split("\n"), start=1):
        if not line.strip():
            continue
        events.append(parse_event_line(line, index, constants.EVENTS_FILE))
    return events


# ------------------------------------------------------------------ 编码
def canonical_json(obj):
    """规范化 JSON：键排序、紧凑分隔符、非 ASCII 不转义。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def serialize_events(events):
    """事件序列 → 规范化字节（每行一个对象，文件末尾恰好一个 LF）。"""
    if not events:
        return b""
    lines = [canonical_json(event) for event in events]
    return ("\n".join(lines) + "\n").encode("utf-8")


def serialize_lines(rows):
    if not rows:
        return b""
    return ("\n".join(canonical_json(row) for row in rows) + "\n").encode("utf-8")


# ------------------------------------------------------------------ 原子写
def _fsync_dir(directory):
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def write_bytes_atomic(path, data):
    """同目录 .tmp → fsync → os.replace → fsync(dir)。"""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    ensure_dir(directory)
    tmp_path = path + ".tmp"
    try:
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp_path, path)
        _fsync_dir(directory)
    except OSError as exc:
        with contextlib.suppress(OSError):
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        raise errors.make("IO_WRITE_FAILED", "写入文件失败：%s（%s）" % (path, exc), location=path)


def write_text_atomic(path, text):
    write_bytes_atomic(path, text.encode("utf-8"))


def write_events(data_dir, events):
    raw = serialize_events(events)
    write_bytes_atomic(paths(data_dir)["events"], raw)
    return raw


def write_events_bytes(data_dir, raw):
    """导入专用：写入已校验的原始字节（不重新序列化）。"""
    write_bytes_atomic(paths(data_dir)["events"], raw)


# ------------------------------------------------------------------ 摘要
def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file_hex(path):
    return sha256_hex(read_bytes(path))


def store_digest(data_dir):
    """当前 events.jsonl 字节摘要，形如 'sha256:<hex>'。"""
    return "sha256:" + sha256_file_hex(paths(data_dir)["events"])


def bytes_digest(raw):
    return "sha256:" + sha256_hex(raw)


# ------------------------------------------------------------------ 写锁
@contextlib.contextmanager
def store_lock(data_dir):
    """state/.lock 排他 flock；最多等 5 秒，超时 ⇒ CONFLICT_STORE_LOCKED。"""
    p = paths(data_dir)
    ensure_dir(p["state_dir"])
    try:
        fd = os.open(p["lock"], os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        raise errors.make("IO_WRITE_FAILED", "无法创建写锁文件：%s（%s）" % (p["lock"], exc),
                          location=p["lock"])
    try:
        deadline = time.monotonic() + constants.LOCK_WAIT_SECONDS
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise errors.make("CONFLICT_STORE_LOCKED")
                time.sleep(constants.LOCK_POLL_SECONDS)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# ------------------------------------------------------------------ 清单
MANIFEST_REQUIRED = (
    "manifest_version", "store_digest", "last_recompute_store_digest",
    "last_render_store_digest", "artifacts", "last_backup", "last_import", "updated_at",
)


def load_manifest(data_dir, strict=True):
    """读取 state/manifest.json；不存在返回 None；损坏在 strict 时抛 CORRUPT_MANIFEST。"""
    path = paths(data_dir)["manifest"]
    if not os.path.isfile(path):
        return None
    try:
        value = json.loads(read_text(path))
    except (ValueError, UnicodeDecodeError) as exc:
        if not strict:
            return None
        raise errors.make("CORRUPT_MANIFEST", "派生状态清单无法解析：%s" % exc, location=path)
    if not isinstance(value, dict):
        if not strict:
            return None
        raise errors.make("CORRUPT_MANIFEST", "派生状态清单不是 JSON 对象", location=path)
    for key in MANIFEST_REQUIRED:
        if key not in value:
            if not strict:
                return None
            raise errors.make("CORRUPT_MANIFEST", "派生状态清单缺少字段 %s" % key, location=path,
                              details={"field": key})
    return value


def fresh_manifest(store_digest_value):
    return {
        "manifest_version": constants.MANIFEST_VERSION,
        "store_digest": store_digest_value,
        "last_recompute_store_digest": None,
        "last_render_store_digest": None,
        "artifacts": {},
        "last_backup": {"path": None, "at": None},
        "last_import": {"source": None, "at": None},
        "updated_at": None,
    }


def save_manifest(data_dir, manifest):
    manifest["updated_at"] = manifest.get("updated_at") or ""
    write_text_atomic(
        paths(data_dir)["manifest"],
        json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
    )


def delete_manifest(data_dir):
    path = paths(data_dir)["manifest"]
    with contextlib.suppress(FileNotFoundError):
        os.remove(path)


# ------------------------------------------------------------------ 备份
def backup_events(data_dir, timestamp):
    """写 state/backups/events-<timestamp>.jsonl（覆盖导入前自动备份）。"""
    p = paths(data_dir)
    ensure_dir(p["backups"])
    target = os.path.join(p["backups"], "events-%s.jsonl" % timestamp)
    try:
        raw = read_events_bytes(data_dir)
        write_bytes_atomic(target, raw)
    except errors.CoreError:
        raise
    except OSError as exc:
        raise errors.make("IMPORT_BACKUP_FAILED", "写自动备份失败：%s" % exc, location=target)
    return target
