"""C0s 画像视图应用服务（v2）：profile / profile_at / history / recompute / render。

所有视图由 `core/profile.py` 的纯函数生成；本模块只做引用解析、锁与落盘协调。
"""

import os

from .. import clock
from .. import errors
from .. import profile as profile_module
from .. import render
from .. import store
from .. import validate
from ._shared import (WARN_RECOMPUTE, _load_model, _resolve_category)

__all__ = ["get_profile", "get_profile_at", "get_history", "recompute", "render_hosts"]


def _category_key_arg(model, category):
    if category is None:
        return None
    category_obj = _resolve_category(model, category)
    if category_obj["deleted_at"] is not None:
        raise errors.make("NOT_FOUND_CATEGORY",
                          "类别已删除：%s" % category_obj["display_name"],
                          details={"category_key": category_obj["key"]})
    return category_obj["key"]


def get_profile(data_dir, category=None, trace=False):
    data_dir = os.path.abspath(data_dir)
    _, _, model = _load_model(data_dir)
    category_key = _category_key_arg(model, category)
    return profile_module.build_profile_view(model, category_key=category_key,
                                             trace=bool(trace))


def get_profile_at(data_dir, at, category=None):
    data_dir = os.path.abspath(data_dir)
    _, _, model = _load_model(data_dir)
    if at is None:
        raise errors.make("VALIDATION_MISSING_VALID_FROM",
                          "缺少查询时点：请提供 --at YYYY-MM",
                          details={"field": "at"})
    month = validate.require_month(at, field="at")
    if month is not None and not clock.is_month(month):
        raise errors.make("VALIDATION_INVALID_MONTH", details={"field": "at"})
    category_key = _category_key_arg(model, category)
    return profile_module.build_profile_at_view(model, month, category_key=category_key)


def get_history(data_dir, category=None):
    data_dir = os.path.abspath(data_dir)
    _, _, model = _load_model(data_dir)
    category_key = _category_key_arg(model, category)
    return profile_module.build_history_view(model, category_key=category_key)


def recompute(data_dir):
    data_dir = os.path.abspath(data_dir)
    with store.store_lock(data_dir):
        raw, _, model = _load_model(data_dir)
        problems = validate.check_invariants(model)
        if problems:
            raise errors.make("CORRUPT_EVENT_LOG",
                              "读取模型不变量校验失败：%s" % problems[0],
                              details={"problems": problems[:5]})
        digest = store.bytes_digest(raw)
        written = render.write_artifacts(data_dir, model, render.PROFILE_KINDS, digest,
                                         clock.now_recorded_at(), "recompute")
        manifest = store.load_manifest(data_dir, strict=False) or store.fresh_manifest(digest)
        host_pending = manifest.get("last_render_store_digest") != digest
    return {
        "artifacts": [{"path": item["path"], "bytes": item["bytes"],
                       "sha256": item["sha256"]} for item in written],
        "host_pending": host_pending,
        "warnings": [WARN_RECOMPUTE] if host_pending else [],
    }


def render_hosts(data_dir, host="all"):
    from .. import constants

    data_dir = os.path.abspath(data_dir)
    if host not in constants.HOST_CHOICES:
        raise errors.make("USAGE_UNKNOWN_HOST", details={"host": host})
    with store.store_lock(data_dir):
        raw, _, model = _load_model(data_dir)
        digest = store.bytes_digest(raw)
        at = clock.now_recorded_at()
        manifest = store.load_manifest(data_dir, strict=False)
        if manifest is None or manifest.get("last_recompute_store_digest") != digest:
            render.write_artifacts(data_dir, model, render.PROFILE_KINDS, digest, at,
                                   "recompute")
        if host == "all":
            kinds = list(render.HOST_KINDS)
        else:
            kinds = ["host_" + host]
        kinds = kinds + ["install_doc"]
        written = render.write_artifacts(data_dir, model, kinds, digest, at, "render")
        host_body_bytes = render.host_body_bytes(model)
        warnings = []
        if host_body_bytes > constants.HOST_BODY_WARN_BYTES:
            warnings.append(
                "宿主产物正文 %d B 超过告警阈值 %d B；可用 category disable/delete 降载"
                % (host_body_bytes, constants.HOST_BODY_WARN_BYTES)
            )
    return {
        "artifacts": [{"path": item["path"], "bytes": item["bytes"],
                       "sha256": item["sha256"]} for item in written],
        "host_body_bytes": host_body_bytes,
        "warnings": warnings,
    }
