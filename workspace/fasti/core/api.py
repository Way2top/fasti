"""C0 核心 API 门面（UI 无关，唯一入口；实现下沉到 `core/services/`）。

约束（ADR-019 / ADR-021 / ADR-022）：
- 本模块**只做 re-export**，不含任何函数体；`fasti.core.api` 仍是适配层与测试的唯一 import 点；
- 核心层不 import argparse / http.server，不读 sys.argv、不 print / 不 sys.exit；
- 失败抛 `CoreError(code, message, details, location)`；`code` 取自 `core.errors.EXIT_CODES` 目录。
"""

from .services._shared import (
    WARN_RECOMPUTE,
    WARN_RENDER,
    fold_events,
    history_text,
    host_global_paths,
    is_initialized,
    profile_at_text,
    profile_text,
    render_history_body,
    render_host_body,
    render_profile_body,
    resolve_data_dir,
)
from .services.lifecycle import (
    export_bundle,
    get_status,
    import_bundle,
    init_store,
    install_hosts,
)
from .services.facts import (
    add_fact,
    change_fact,
    delete_fact,
    list_facts,
    show_fact,
)
from .services.categories import (
    add_category,
    delete_category,
    list_categories,
    set_category_enabled,
)
from .services.views import (
    get_history,
    get_profile,
    get_profile_at,
    recompute,
    render_hosts,
)
from .services.verify import verify
from .ids import short_id

__all__ = [
    # helpers / 常量
    "resolve_data_dir",
    "is_initialized",
    "host_global_paths",
    "fold_events",
    "render_profile_body",
    "render_host_body",
    "render_history_body",
    "profile_text",
    "profile_at_text",
    "history_text",
    "WARN_RENDER",
    "WARN_RECOMPUTE",
    "short_id",
    # 20 个应用服务函数（api-spec.yaml core_api.functions）
    "init_store",
    "get_status",
    "add_fact",
    "change_fact",
    "delete_fact",
    "list_facts",
    "show_fact",
    "list_categories",
    "add_category",
    "set_category_enabled",
    "delete_category",
    "get_profile",
    "get_profile_at",
    "get_history",
    "recompute",
    "render_hosts",
    "export_bundle",
    "import_bundle",
    "verify",
    "install_hosts",
]
