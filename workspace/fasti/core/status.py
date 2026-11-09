"""状态报告（C9）：新鲜度 / 外部篡改检测 / 计数 / 宿主接入状态（只读）。"""

import os

from . import constants
from . import profile as profile_module
from . import render
from . import store


def _digest_of_file(path):
    try:
        return store.sha256_file_hex(path)
    except Exception:  # noqa: BLE001 - 探测失败视为不可读，不阻塞 status
        return None


def _profile_freshness(data_dir, digest, manifest):
    paths = store.paths(data_dir)
    files = [paths["profile_current"], paths["profile_history"]]
    if not all(os.path.isfile(path) for path in files):
        return "missing"
    if manifest is None:
        return "not-rendered"
    last = manifest.get("last_recompute_store_digest")
    if last is None:
        return "not-rendered"
    return "up-to-date" if last == digest else "pending-recompute"


def _host_freshness(data_dir, digest, manifest):
    paths = store.paths(data_dir)
    files = [paths["dist_agents"], paths["dist_claude"], paths["dist_cursor"]]
    if not all(os.path.isfile(path) for path in files):
        return "missing"
    if manifest is None:
        return "not-rendered"
    last = manifest.get("last_render_store_digest")
    if last is None:
        return "not-rendered"
    return "up-to-date" if last == digest else "pending-render"


def _artifact_states(data_dir, manifest):
    out = []
    if not manifest:
        return out
    artifacts = manifest.get("artifacts") or {}
    for rel_path in sorted(artifacts):
        entry = artifacts[rel_path]
        target = store.artifact_abs_path(data_dir, rel_path)
        if not os.path.isfile(target):
            state = "missing"
            size = 0
        else:
            state = "up-to-date"
            try:
                raw = store.read_bytes(target)
            except Exception:  # noqa: BLE001
                state = "missing"
                raw = b""
            size = len(raw)
            recorded = entry.get("sha256")
            if recorded and store.sha256_hex(raw) != recorded:
                state = "modified-externally"
        out.append({"path": rel_path, "state": state, "bytes": size})
    return out


def _host_access(data_dir):
    """各宿主接入状态（C-05：全局优先；Cursor 无文件形式全局位置 → 降级）。"""
    paths = store.paths(data_dir)
    dist_map = {
        "agents": paths["dist_agents"],
        "claude": paths["dist_claude"],
        "cursor": paths["dist_cursor"],
    }
    entries = []
    for host in constants.HOST_NAMES:
        note = None
        target = None
        scope = "none"
        if host == "cursor":
            note = "Cursor 无文件形式用户级全局规则（User Rules 是设置项），属降级路径：需 fasti install-host --host cursor --project <DIR>"
            candidates = []
        else:
            global_path = constants.HOST_GLOBAL_PATHS.get(host)
            candidates = [store.expand_home(global_path)] if global_path else []
            shared = constants.HOST_SHARED_PATHS.get(host)
            if shared:
                candidates.append(store.expand_home(shared))
        for candidate in candidates:
            if os.path.isfile(candidate):
                target = candidate
                scope = "global"
                break
        current = False
        if target and os.path.isfile(dist_map[host]):
            target_digest = _digest_of_file(target)
            dist_digest = _digest_of_file(dist_map[host])
            current = bool(target_digest and dist_digest and target_digest == dist_digest)
        entry = {"host": host, "scope": scope, "target": target, "current": current}
        if note:
            entry["note"] = note
        entries.append(entry)
    return entries


def _host_sizes(data_dir, model):
    paths = store.paths(data_dir)
    sizes = {"agents_bytes": 0, "claude_bytes": 0, "cursor_bytes": 0,
             "host_body_bytes": render.host_body_bytes(model)}
    for key, path in (("agents_bytes", paths["dist_agents"]),
                      ("claude_bytes", paths["dist_claude"]),
                      ("cursor_bytes", paths["dist_cursor"])):
        if os.path.isfile(path):
            sizes[key] = os.path.getsize(path)
    return sizes


def build_status(data_dir, model, raw_events):
    digest = store.bytes_digest(raw_events)
    manifest = store.load_manifest(data_dir, strict=True)
    freshness = {
        "profile": _profile_freshness(data_dir, digest, manifest),
        "host_artifacts": _host_freshness(data_dir, digest, manifest),
    }
    backups = (manifest or {}).get("last_backup") or {"path": None, "at": None}
    sizes = _host_sizes(data_dir, model)
    warnings = []
    host_body_size = sizes["host_body_bytes"]
    if host_body_size > constants.HOST_BODY_WARN_BYTES:
        warnings.append(
            "宿主产物正文 %d B 超过告警阈值 %d B；可用 category disable/delete 降载"
            % (host_body_size, constants.HOST_BODY_WARN_BYTES)
        )
    if freshness["profile"] in ("pending-recompute", "missing", "not-rendered"):
        warnings.append("当前画像未就绪：运行 fasti recompute")
    if freshness["host_artifacts"] in ("pending-render", "missing", "not-rendered"):
        warnings.append("宿主产物待更新：运行 fasti render")
    return {
        "initialized": True,
        "data_dir": os.path.abspath(data_dir),
        "counts": model.counts(),
        "freshness": freshness,
        "artifacts": _artifact_states(data_dir, manifest),
        "host_access": _host_access(data_dir),
        "backups": backups,
        "host_sizes": sizes,
        "warnings": warnings,
    }


def profile_text_for_status(model, category_id=None, trace=False):
    """便捷：状态/页面需要的画像文本（核心层渲染）。"""
    return profile_module.render_profile_text(
        profile_module.build_profile_view(model, category_id=category_id, trace=trace))
