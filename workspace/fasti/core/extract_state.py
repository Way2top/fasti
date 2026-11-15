"""C-09 过程状态（ADR-031 / api-spec file_formats.extract_state）。

`<data-dir>/state/extract/`：
- `watermarks.json`（原子重写；按 session 记录 seq/message 水位）
- `candidates.jsonl`（追加型候选快照，按 candidate_id 折叠；不进 events）
- `runs.jsonl`（追加型运行记录；无原文/密钥）

本模块只做 IO 与确定性 ID；决策/写入在 services/extract.py。
"""

import hashlib
import json
import os
import tempfile
import unicodedata

from . import constants
from . import errors
from . import store

STATE_REL = os.path.join("state", "extract")
WATERMARKS_REL = os.path.join(STATE_REL, "watermarks.json")
CANDIDATES_REL = os.path.join(STATE_REL, "candidates.jsonl")
RUNS_REL = os.path.join(STATE_REL, "runs.jsonl")
LOCK_REL = os.path.join(STATE_REL, ".run.lock")


def state_dir(data_dir):
    return os.path.join(data_dir, STATE_REL)


def _path(data_dir, rel):
    return os.path.join(data_dir, rel)


def ensure_state_dir(data_dir):
    store.ensure_dir(state_dir(data_dir))


# ---------------------------------------------------------------- 确定性 ID
def candidate_id_for(session_id, message_ids, content, category_key, change_type):
    payload = {
        "session_id": session_id,
        "message_ids": list(message_ids),
        "content": content,
        "category_key": category_key,
        "change_type": change_type,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=False).encode("utf-8")).hexdigest()
    return "cand-" + digest[:20]


def _normalized_content(content):
    text = unicodedata.normalize("NFKC", content or "").lower()
    return "".join(char for char in text if not char.isspace())


def evidence_key_for(session_id, message_ids, content):
    parts = [session_id, ",".join(sorted(message_ids)), _normalized_content(content)]
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 水位
def empty_watermarks():
    return {"schema_version": constants.EXTRACT_STATE_SCHEMA_VERSION,
            "updated_at": None, "sessions": {}}


def load_watermarks(data_dir):
    """→ (watermarks dict, rebuilt: bool)。缺失/损坏 ⇒ 空水位 + rebuilt=True（由事实证据重建）。"""
    path = _path(data_dir, WATERMARKS_REL)
    if not os.path.isfile(path):
        return empty_watermarks(), True
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return empty_watermarks(), True
    if not isinstance(data, dict) or not isinstance(data.get("sessions"), dict):
        return empty_watermarks(), True
    return data, False


def save_watermarks(data_dir, watermarks):
    ensure_state_dir(data_dir)
    path = _path(data_dir, WATERMARKS_REL)
    raw = json.dumps(watermarks, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False).encode("utf-8") + b"\n"
    _write_atomic(path, raw)


def rebuild_from_evidence(facts, accepted_candidates=None):
    """水位丢失时从事实 extraction 证据与已接受候选重建（AC-16-9）。

    `facts` 为读模型中带 extraction 的事实（或事件 data 列表），接受
    `{session_id, message_ids, message_ordinals}`；返回空水位结构的 sessions 部分。
    """
    sessions = {}

    def _feed(session_id, message_ids, message_ordinals, path=None):
        if not session_id:
            return
        entry = sessions.setdefault(session_id, {
            "path": path, "path_hash": None, "last_seq": 0,
            "last_message_id": None, "last_event_sha256": None,
            "processed_user_messages": 0, "updated_at": None, "run_id": None,
            "rebuilt": True,
        })
        ordinals = [int(item) for item in (message_ordinals or []) if isinstance(item, int)]
        if ordinals and max(ordinals) > entry["processed_user_messages"]:
            entry["processed_user_messages"] = max(ordinals)
        ids = list(message_ids or [])
        if ids:
            entry["last_message_id"] = ids[-1]
        entry["updated_at"] = entry["updated_at"] or None

    for fact in facts or []:
        extraction = fact.get("extraction") if isinstance(fact, dict) else None
        if not extraction:
            continue
        _feed(extraction.get("session_id"), extraction.get("message_ids"),
              extraction.get("message_ordinals"))
    for candidate in accepted_candidates or []:
        if candidate.get("status") not in ("approved", "duplicate"):
            continue
        evidence = candidate.get("evidence") or {}
        _feed(candidate.get("session_id"), evidence.get("message_ids"),
              candidate.get("message_ordinals"))
    return sessions


def merge_rebuilt_watermarks(data_dir, facts, accepted_candidates=None):
    """把事实证据重建结果合并进现有水位（不覆盖已更新的更高水位）；标记 rebuilt。"""
    watermarks, _missing = load_watermarks(data_dir)
    rebuilt = rebuild_from_evidence(facts, accepted_candidates)
    changed = False
    for session_id, entry in rebuilt.items():
        existing = watermarks["sessions"].get(session_id)
        if existing is None:
            watermarks["sessions"][session_id] = entry
            changed = True
            continue
        if entry["processed_user_messages"] > existing.get("processed_user_messages", 0):
            existing["processed_user_messages"] = entry["processed_user_messages"]
            existing["last_message_id"] = entry.get("last_message_id")
            changed = True
        existing["rebuilt"] = True
    if changed:
        from . import clock
        watermarks["updated_at"] = clock.now_recorded_at()
        save_watermarks(data_dir, watermarks)
    return watermarks, changed


# ---------------------------------------------------------------- 候选
def load_candidates(data_dir):
    """→ {candidate_id: candidate}（按 candidate_id 折叠，最后快照为准）。"""
    path = _path(data_dir, CANDIDATES_REL)
    folded = {}
    if not os.path.isfile(path):
        return folded
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                candidate = json.loads(line)
            except ValueError:
                continue
            candidate_id = candidate.get("candidate_id")
            if candidate_id:
                folded[candidate_id] = candidate
    return folded


def list_candidates(data_dir, status="pending"):
    folded = load_candidates(data_dir)
    if status in (None, "all", ""):
        items = list(folded.values())
    else:
        items = [item for item in folded.values() if item.get("status") == status]
    items.sort(key=lambda item: (item.get("created_at") or "", item.get("candidate_id") or ""),
               reverse=True)
    counts = {"pending": 0, "approved": 0, "rejected": 0, "duplicate": 0, "dropped": 0}
    for item in folded.values():
        counts[item.get("status")] = counts.get(item.get("status"), 0) + 1
    return {"items": items, "counts": counts}


def get_candidate(data_dir, candidate_id):
    candidate = load_candidates(data_dir).get(candidate_id)
    if candidate is None:
        raise errors.make("EXTRACT_CANDIDATE_NOT_FOUND",
                          "提取候选不存在：%s" % candidate_id,
                          details={"candidate_id": candidate_id})
    return candidate


def append_candidate(data_dir, candidate):
    ensure_state_dir(data_dir)
    raw = (json.dumps(candidate, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False) + "\n").encode("utf-8")
    with open(_path(data_dir, CANDIDATES_REL), "ab") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    return candidate


def _now():
    from . import clock
    return clock.now_recorded_at()


def new_candidate(candidate_id, session_id, message_ids, message_ordinals, chunk_id,
                  run_id, content, category_key, change_type, extraction_confidence,
                  recall, decision, evidence_key, relation=None, system=None,
                  suggested_category_key=None, target_fact_id=None, status="pending"):
    now = _now()
    return {
        "candidate_id": candidate_id,
        "schema_version": constants.EXTRACT_STATE_SCHEMA_VERSION,
        "created_at": now,
        "updated_at": now,
        "status": status,
        "session_id": session_id,
        "message_ids": list(message_ids),
        "message_ordinals": list(message_ordinals),
        "chunk_id": chunk_id,
        "run_id": run_id,
        "content": content,
        "suggested_category_key": suggested_category_key,
        "category_key": category_key,
        "change_type": change_type,
        "target_fact_id": target_fact_id,
        "extraction_confidence": extraction_confidence,
        "relation": relation or {"llm": None, "target_fact_id": None,
                                 "relation_confidence": None, "rationale": None,
                                 "unverified": False},
        "system": system or {"similar": False, "matches": [], "layer_mismatch": False},
        "recall": recall,
        "decision": decision,
        "evidence": {"message_ids": list(message_ids)},
        "review": {"status": "pending", "reviewed_at": None, "original_content": None,
                   "final_content": None, "edits": {}, "reject_reason": None},
        "write": {"fact_id": None, "event_seq": None},
        "dedupe": {"evidence_key": evidence_key, "existing_fact_id": None},
    }


def update_candidate(data_dir, candidate):
    candidate["updated_at"] = _now()
    append_candidate(data_dir, candidate)
    return candidate


# ---------------------------------------------------------------- 运行记录
def append_run(data_dir, run):
    ensure_state_dir(data_dir)
    raw = (json.dumps(run, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False) + "\n").encode("utf-8")
    with open(_path(data_dir, RUNS_REL), "ab") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    return run


def load_runs(data_dir, limit=20):
    path = _path(data_dir, RUNS_REL)
    runs = []
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    runs.append(json.loads(line))
                except ValueError:
                    continue
    return runs[-max(1, min(int(limit or 20), 100)):][::-1]


def last_run(data_dir):
    runs = load_runs(data_dir, 1)
    return runs[0] if runs else None


# ---------------------------------------------------------------- 并发锁
class run_lock(object):
    """extract 运行独占锁（state/extract/.run.lock）；跨进程用 O_CREAT|O_EXCL。"""

    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.path = _path(data_dir, LOCK_REL)
        self.fd = None

    def __enter__(self):
        ensure_state_dir(self.data_dir)
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise errors.make("EXTRACT_RUN_IN_PROGRESS",
                              "已有提取运行进行中；请稍后再试或查看状态")
        os.write(self.fd, ("%d\n" % os.getpid()).encode("ascii"))
        os.fsync(self.fd)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.fd is not None:
            try:
                os.close(self.fd)
            finally:
                self.fd = None
        try:
            os.unlink(self.path)
        except OSError:
            pass
        return False


def is_running(data_dir):
    return os.path.exists(_path(data_dir, LOCK_REL))


# ---------------------------------------------------------------- 原子写
def _write_atomic(path, raw):
    directory = os.path.dirname(path)
    handle = tempfile.NamedTemporaryFile(mode="wb", dir=directory, prefix=".extract.",
                                         suffix=".tmp", delete=False)
    tmp_path = handle.name
    try:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
