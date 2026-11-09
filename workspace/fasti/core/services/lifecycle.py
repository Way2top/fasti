"""C0s 生命周期应用服务（v2）：init / status / export / import / install-host。

唯一写入 v2 格式（format_version 2）的地方之一；v1 数据加载由 `_shared._load_model` 的
格式门拒绝（STORE_FORMAT_UNSUPPORTED，ADR-024）。
"""

import os

from .. import clock
from .. import constants
from .. import export as export_module
from .. import importer as importer_module
from .. import install as install_module
from ._shared import (_load_model, _make_event, is_initialized)

__all__ = ["init_store", "get_status", "export_bundle", "import_bundle", "install_hosts"]


from .. import errors
from ..category_templates import _load_templates


def init_store(data_dir):
    data_dir = os.path.abspath(data_dir)
    from .. import store

    store.ensure_store_dirs(data_dir)
    with store.store_lock(data_dir):
        if store.is_initialized(data_dir):
            existing = store.read_events_bytes(data_dir)
            if existing.strip():
                raise errors.make(
                    "CONFLICT_STORE_EXISTS",
                    "数据目录已有事件日志，拒绝覆盖：%s" % data_dir,
                    location=store.paths(data_dir)["events"],
                )
        at = clock.now_recorded_at()
        events = [_make_event([], "store_initialized", {
            "format_version": constants.FORMAT_VERSION,
            "app_name": constants.APP_NAME,
            "app_version": constants.APP_VERSION,
        }, at)]
        categories = []
        for template in _load_templates():
            category = {
                "category_key": template["key"],
                "display_name": template["display_name"],
                "origin": "template",
                "template_order": template["template_order"],
                "enabled": True,
                "created_at": at,
            }
            events.append(_make_event(events, "category_added", category, at))
            categories.append(dict(category, deleted_at=None, deleted_handling=None,
                                   migrate_to_category_key=None))
        store.write_events(data_dir, events)
    return {
        "data_dir": data_dir,
        "categories": [{
            "key": category["category_key"],
            "display_name": category["display_name"],
            "origin": category["origin"],
            "template_order": category["template_order"],
            "enabled": category["enabled"],
            "created_at": category["created_at"],
            "deleted_at": None,
            "deleted_handling": None,
            "migrate_to_category_key": None,
        } for category in categories],
        "warnings": [],
        "_extra": {},
    }


def get_status(data_dir):
    data_dir = os.path.abspath(data_dir)
    from .. import status as status_module
    from .. import store

    _, _, model = _load_model(data_dir)
    raw = store.read_events_bytes(data_dir)
    return status_module.build_status(data_dir, model, raw)


def export_bundle(data_dir, out_dir=None):
    data_dir = os.path.abspath(data_dir)
    from .. import store

    with store.store_lock(data_dir):
        raw, _, model = _load_model(data_dir)
        result = export_module.export_bundle(data_dir, model, raw, out_dir)
    return result


def import_bundle(data_dir, bundle_dir, overwrite=False, backup=True):
    data_dir = os.path.abspath(data_dir)
    from .. import store

    with store.store_lock(data_dir):
        result = importer_module.import_bundle(data_dir, bundle_dir, overwrite, backup)
    return result


def install_hosts(data_dir, hosts, mode="symlink", project_dir=None, dry_run=False):
    data_dir = os.path.abspath(data_dir)
    return install_module.install_hosts(data_dir, hosts, mode, project_dir, dry_run)
