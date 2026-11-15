"""C-09 web 适配器：/api/extract/* 端点（只调用 fasti.core.api）。"""

from ..core import api, errors


def dispatch_get(handler, parts, query):
    data_dir = handler.context["data_dir"]
    if parts == ["api", "extract", "status"]:
        handler._ok(api.get_extract_status(data_dir))
        return True
    if parts == ["api", "extract", "config"]:
        handler._ok(api.get_extract_config(data_dir))
        return True
    if parts == ["api", "extract", "candidates"]:
        status = _query_one(query, "status") or "pending"
        handler._ok(api.list_extract_candidates(data_dir, status))
        return True
    if len(parts) == 4 and parts[:3] == ["api", "extract", "candidates"]:
        handler._ok(api.show_extract_candidate(data_dir, parts[3]))
        return True
    if parts == ["api", "extract", "runs"]:
        limit = _query_one(query, "limit") or "20"
        handler._ok(api.list_extract_runs(data_dir, int(limit)))
        return True
    return False


def dispatch_post(handler, parts, body):
    data_dir = handler.context["data_dir"]
    if parts == ["api", "extract", "config"]:
        patch = body.get("patch")
        if not isinstance(patch, dict):
            raise errors.make("USAGE_INVALID_ARGUMENT", "patch 必须是对象",
                              details={"field": "patch"})
        handler._ok(api.set_extract_config(data_dir, patch))
        return True
    if parts == ["api", "extract", "consent"]:
        accepted = body.get("accepted")
        if not isinstance(accepted, bool):
            raise errors.make("USAGE_INVALID_ARGUMENT", "accepted 必须是布尔值",
                              details={"field": "accepted"})
        handler._ok(api.set_extract_consent(data_dir, accepted))
        return True
    if parts == ["api", "extract", "run"]:
        trigger = body.get("trigger") or "manual"
        if trigger not in ("manual", "web_open"):
            raise errors.make("USAGE_INVALID_ARGUMENT", "trigger 只能是 manual/web_open",
                              details={"field": "trigger"})
        run = api.extract_run(data_dir, trigger=trigger)
        handler._ok({"run_id": run["run_id"], "status": "finished", "run": run})
        return True
    if len(parts) == 5 and parts[:3] == ["api", "extract", "candidates"] \
            and parts[4] == "approve":
        patch = {}
        for key in ("change_type", "content", "category", "valid_from"):
            if body.get(key) is not None:
                patch[key] = body[key]
        result = api.resolve_extract_candidate(
            data_dir, parts[3], "approve", patch,
            body.get("confirm_duplicate_token"))
        handler._ok({"candidate": result["candidate"], "fact": result["fact"],
                     "change_type": result["change_type"],
                     "warnings": result.get("warnings", [])})
        return True
    if len(parts) == 5 and parts[:3] == ["api", "extract", "candidates"] \
            and parts[4] == "reject":
        result = api.resolve_extract_candidate(
            data_dir, parts[3], "reject", {"reason": body.get("reason") or ""})
        handler._ok({"candidate": result["candidate"]})
        return True
    return False


def _query_one(query, name):
    values = query.get(name) if query else None
    if not values:
        return None
    return values[0]
