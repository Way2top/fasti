"""C-09 会话源：DSH 根会话发现、zstd 解压、user/message 过滤与消息边界分块（ADR-028）。

- 发现：`<dsh-home>/sessions/*/*/session.v4.jsonl.zstd`（允许未压缩 `session.v4.jsonl`，配置 allow_plain）
- 解压：优先 stdlib `compression.zstd`（Python 3.14+）；否则 `subprocess` 调 `zstd -dc -- <path>`
  （shell=False、超时 30s）；zstd 环境缺失 ⇒ EXTRACT_ZSTD_UNAVAILABLE（整次失败，不静默全跳过）
- 只取 `user/message`（data.role=user）的 text 段落；助手/工具/系统消息不抽取、不发送
- 分块：不跨会话、不切消息，单条 ≤1500 字符、单块 ≤20 条 / ≤4000 字符
"""

import hashlib
import json
import os
import shutil
import subprocess

from . import constants
from . import errors

SESSION_SUFFIX = ".jsonl.zstd"
PLAIN_SUFFIX = ".jsonl"
TRUNCATION_MARKER = "…[TRUNCATED]"


def _truncate(text, limit):
    if len(text) <= limit:
        return text, False
    return text[:limit] + TRUNCATION_MARKER, True
_MAX_DECOMPRESSED = 64 * 1024 * 1024
_ZSTD_STDERR_LIMIT = 4096


def _stdlib_zstd():
    try:
        from compression import zstd  # Python 3.14+
        return zstd
    except ImportError:
        return None


def _decompress_bytes(path):
    """→ 解压后 bytes；环境不支持 zstd ⇒ EXTRACT_ZSTD_UNAVAILABLE。"""
    zstd = _stdlib_zstd()
    if zstd is not None:
        try:
            with zstd.open(path, "rb") as handle:
                data = handle.read(_MAX_DECOMPRESSED + 1)
        except Exception as exc:  # noqa: BLE001 - 解压失败按会话不可读处理
            raise errors.make("EXTRACT_SESSION_UNREADABLE",
                              "会话解压失败：%s" % os.path.basename(path),
                              details={"path": path}, location="session")
        if len(data) > _MAX_DECOMPRESSED:
            raise errors.make("EXTRACT_SESSION_UNREADABLE",
                              "会话解压后超过 64MB：%s" % os.path.basename(path),
                              details={"path": path}, location="session")
        return data
    binary = shutil.which("zstd")
    if not binary:
        raise errors.make(
            "EXTRACT_ZSTD_UNAVAILABLE",
            "无法解压会话：缺少 zstd 支持（请安装 zstd，或使用 Python>=3.14）",
            details={"hint": "install zstd or use Python>=3.14"})
    try:
        proc = subprocess.run([binary, "-dc", "--", path], shell=False,
                              capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise errors.make("EXTRACT_SESSION_UNREADABLE",
                          "会话解压超时（30s）：%s" % os.path.basename(path),
                          details={"path": path}, location="session")
    except OSError as exc:
        raise errors.make("EXTRACT_ZSTD_UNAVAILABLE",
                          "无法运行 zstd：%s" % exc, details={"binary": binary})
    if proc.returncode != 0:
        stderr = (proc.stderr or b"")[:_ZSTD_STDERR_LIMIT].decode("utf-8", "replace")
        raise errors.make("EXTRACT_SESSION_UNREADABLE",
                          "会话解压失败（zstd exit %d）：%s" % (proc.returncode, stderr.strip()),
                          details={"path": path, "returncode": proc.returncode},
                          location="session")
    if len(proc.stdout) > _MAX_DECOMPRESSED:
        raise errors.make("EXTRACT_SESSION_UNREADABLE",
                          "会话解压后超过 64MB：%s" % os.path.basename(path),
                          details={"path": path}, location="session")
    return proc.stdout


def read_session_lines(path, allow_plain=False):
    """→ [(raw_line_bytes)]；未压缩仅当 allow_plain=true 且文件名为 .jsonl。"""
    name = os.path.basename(path)
    if name.endswith(PLAIN_SUFFIX) and not name.endswith(SESSION_SUFFIX):
        if not allow_plain:
            raise errors.make("EXTRACT_SESSION_UNREADABLE",
                              "未压缩会话需要 allow_plain=true：%s" % name,
                              details={"path": path}, location="session")
        try:
            with open(path, "rb") as handle:
                data = handle.read(_MAX_DECOMPRESSED + 1)
        except OSError as exc:
            raise errors.make("EXTRACT_SESSION_UNREADABLE",
                              "会话不可读：%s" % exc, details={"path": path},
                              location="session")
    else:
        data = _decompress_bytes(path)
    if len(data) > _MAX_DECOMPRESSED:
        raise errors.make("EXTRACT_SESSION_UNREADABLE",
                          "会话超过 64MB：%s" % name, details={"path": path},
                          location="session")
    return [line for line in data.split(b"\n") if line.strip()]


def _parse_line(raw):
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise errors.make("EXTRACT_SESSION_UNREADABLE", "会话行不是合法 JSON",
                          location="session")


def _session_header(events):
    for event in events:
        if isinstance(event, dict) and event.get("type") == "session":
            return event
    return None


def _is_root_session(header):
    if not isinstance(header, dict):
        return False
    data = header.get("data") if isinstance(header.get("data"), dict) else header
    if data.get("delegationDepth", 0) == 0 and data.get("isSeeded") is False:
        version = data.get("version", 4)
        return version in (4, "4", None)
    return False


def _first_value(data, keys, default=None):
    for key in keys:
        value = data.get(key)
        if value:
            return value
    return default


def _text_of(content):
    """content 为 blocks 数组：拼接 type=text 的段落。"""
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text") or "")
    return "".join(parts)


def load_session(path, allow_plain=False):
    """读取一个会话 → 摘要 dict；非根会话返回 None；失败抛 EXTRACT_SESSION_*。"""
    raws = read_session_lines(path, allow_plain=allow_plain)
    events = []
    for index, raw in enumerate(raws, start=1):
        event = _parse_line(raw)
        if isinstance(event, dict):
            events.append((index, raw, event))
    header = _session_header([event for _index, _raw, event in events])
    if not _is_root_session(header):
        return None
    header_data = header.get("data") if isinstance(header.get("data"), dict) else header
    directory = os.path.dirname(path)
    session_id = _first_value(header_data, ("session_id", "sessionId", "id"))
    if not session_id:
        session_id = os.path.basename(directory) or hashlib.sha256(
            path.encode("utf-8")).hexdigest()[:16]
    cwd = _first_value(header_data, ("cwd", "workingDirectory", "workspace"))
    titles = []
    title_kinds_seen = []
    for _line_number, _raw, event in events:
        if event.get("type") != "session/title":
            continue
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        title = data.get("title")
        source = data.get("source") if isinstance(data.get("source"), dict) else {}
        kind = source.get("kind")
        if kind:
            title_kinds_seen.append(kind)
        if isinstance(title, str) and title.strip():
            titles.append((kind, title.strip()))
    provider_title = None
    for kind, title in reversed(titles):
        if kind == "provider":
            provider_title = title
            break
    messages = []
    source_counts = {}
    ordinal = 0
    max_seq = 0
    for line_number, _raw, event in events:
        seq = event.get("seq", line_number)
        if isinstance(seq, int):
            max_seq = max(max_seq, seq)
        if event.get("type") != "user/message":
            continue
        data = event.get("data") if isinstance(event.get("data"), dict) else None
        if not data or data.get("role") != "user":
            continue
        source = data.get("source") if isinstance(data.get("source"), dict) else {}
        kind = source.get("kind")
        source_counts[kind or "<missing>"] = source_counts.get(kind or "<missing>", 0) + 1
        if kind not in constants.USER_MESSAGE_SOURCE_KINDS:
            continue  # DSH 注入上下文（time-context/skill-catalog/...）不算用户消息
        text = _text_of(data.get("content"))
        if not text.strip():
            continue
        ordinal += 1
        raw_bytes = raws[line_number - 1]
        messages.append({
            "seq": seq if isinstance(seq, int) else line_number,
            "event_sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "message_id": data.get("id") or "msg-%s" % seq,
            "ordinal": ordinal,
            "text": text,
        })
    first_text = ""
    for message in messages[:5]:
        text = message["text"].strip()
        if text.startswith("<system-reminder>") \
                or text.lower().startswith("current runtime context"):
            continue  # 防御性：跳过伪装成 user 的注入消息
        first_text = message["text"]
        break
    first_message, first_truncated = _truncate(
        first_text, constants.EXTRACT_FIRST_MESSAGE_MAX_CHARS)
    title_text = None
    title_truncated = False
    title_source = "first_message"
    if provider_title is not None:
        title_text, title_truncated = _truncate(
            provider_title, constants.EXTRACT_TITLE_MAX_CHARS)
        title_source = "provider"
    return {
        "session_id": str(session_id),
        "cwd": cwd,
        "path": path,
        "max_seq": max_seq,
        "messages": messages,
        "divert_input": {
            "title": title_text,
            "title_source": title_source,
            "title_truncated": title_truncated,
            "first_user_message": first_message,
            "first_message_truncated": first_truncated,
            "user_message_count": len(messages),
            "title_kinds_seen": title_kinds_seen,
            "user_message_source_counts": source_counts,
        },
    }


def discover_sessions(dsh_home, allow_plain=False):
    """→ [path]（sessions/*/*/session.v4.jsonl[.zstd]，排序稳定）。"""
    root = os.path.join(os.path.expanduser(dsh_home), "sessions")
    if not os.path.isdir(root):
        return []
    paths = []
    for workspace_dir in sorted(os.listdir(root)):
        workspace_path = os.path.join(root, workspace_dir)
        if not os.path.isdir(workspace_path):
            continue
        for session_dir in sorted(os.listdir(workspace_path)):
            base = os.path.join(workspace_path, session_dir)
            if not os.path.isdir(base):
                continue
            zstd_path = os.path.join(base, "session.v4" + SESSION_SUFFIX)
            if os.path.isfile(zstd_path):
                paths.append(zstd_path)
                continue
            if allow_plain:
                plain_path = os.path.join(base, "session.v4" + PLAIN_SUFFIX)
                if os.path.isfile(plain_path):
                    paths.append(plain_path)
    return paths


def truncate_message(text, limit=None):
    limit = limit or constants.EXTRACT_PER_MESSAGE_CHARS
    if len(text) <= limit:
        return text
    return text[:limit] + TRUNCATION_MARKER


def chunk_messages(session, per_message_chars=None, chunk_chars=None, chunk_messages=None):
    """按消息边界分块（不跨会话、不切消息）→ [{chunk_id, session_id, messages, chars}]。"""
    per_message_chars = per_message_chars or constants.EXTRACT_PER_MESSAGE_CHARS
    chunk_chars = chunk_chars or constants.EXTRACT_CHUNK_CHARS
    chunk_messages = chunk_messages or constants.EXTRACT_CHUNK_MESSAGES
    chunks = []
    current = []
    current_chars = 0
    for message in session["messages"]:
        text = truncate_message(message["text"], per_message_chars)
        if current and (len(current) >= chunk_messages
                        or current_chars + len(text) > chunk_chars):
            chunks.append(_make_chunk(session["session_id"], current))
            current = []
            current_chars = 0
        current.append({**message, "chunk_text": text})
        current_chars += len(text)
    if current:
        chunks.append(_make_chunk(session["session_id"], current))
    return chunks


def _make_chunk(session_id, messages):
    ids = ",".join(message["message_id"] for message in messages)
    chunk_id = hashlib.sha256((session_id + "\0" + ids).encode("utf-8")).hexdigest()[:16]
    return {
        "chunk_id": chunk_id,
        "session_id": session_id,
        "messages": messages,
        "chars": sum(len(message["chunk_text"]) for message in messages),
    }
