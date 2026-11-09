"""宿主接入放置（C11 install_hosts / C-05：全局优先 + Cursor 降级）。

- agents → 用户级全局 `~/.dsh/AGENTS.md`
- claude → 用户级全局 `~/.claude/CLAUDE.md`
- cursor → 项目级降级 `<project>/.cursor/rules/fasti.mdc`（必须提供 --project）

冲突保护：目标已存在且不是本工具创建的软链 ⇒ blocked，不覆盖用户手写文件。
本模块不做网络访问、不启动进程。
"""

import os

from . import constants
from . import errors
from . import store


def _dist_artifact_path(data_dir, host):
    if host == "agents":
        return store.paths(data_dir)["dist_agents"]
    if host == "claude":
        return store.paths(data_dir)["dist_claude"]
    if host == "cursor":
        return store.paths(data_dir)["dist_cursor"]
    return None


def _target_path(host, project_dir):
    if host == "cursor":
        if not project_dir:
            return None
        return os.path.join(store.expand_home(project_dir),
                            ".cursor", "rules", constants.CURSOR_RULE_FILENAME)
    global_path = constants.HOST_GLOBAL_PATHS.get(host)
    return store.expand_home(global_path) if global_path else None


def _is_our_symlink(target, dist_path):
    if not os.path.islink(target):
        return False
    try:
        return os.path.realpath(target) == os.path.realpath(dist_path)
    except OSError:
        return False


def _same_content(target, dist_path):
    try:
        return store.sha256_file_hex(target) == store.sha256_file_hex(dist_path)
    except errors.CoreError:
        return False


def _place_symlink(target, dist_path):
    parent = os.path.dirname(target)
    store.ensure_dir(parent)
    if os.path.lexists(target):
        os.remove(target)
    os.symlink(dist_path, target)


def _place_copy(target, dist_path):
    parent = os.path.dirname(target)
    store.ensure_dir(parent)
    raw = store.read_bytes(dist_path)
    store.write_bytes_atomic(target, raw)


def install_hosts(data_dir, hosts, mode, project_dir, dry_run):
    if mode not in ("symlink", "copy"):
        raise errors.make("USAGE_INVALID_ARGUMENT", "未知 mode：%r（可选 symlink / copy）" % (mode,))
    if not hosts:
        raise errors.make("USAGE_INVALID_ARGUMENT", "至少指定一个宿主")
    for host in hosts:
        if host not in constants.HOST_NAMES:
            raise errors.make("USAGE_INVALID_ARGUMENT", "未知宿主：%r" % (host,))

    actions = []
    warnings = []
    for host in hosts:
        dist_path = _dist_artifact_path(data_dir, host)
        if not os.path.isfile(dist_path):
            raise errors.make(
                "NOT_FOUND_PATH",
                "宿主产物不存在：%s；请先运行 fasti render" % dist_path,
                location=dist_path,
            )
        target = _target_path(host, project_dir)
        scope = "project" if host == "cursor" else "global"
        if target is None:
            actions.append({
                "host": host,
                "scope": scope,
                "target": "",
                "status": "skipped",
                "message": "Cursor 无文件形式用户级全局规则（降级路径）；需 --project <DIR> 做项目级放置",
            })
            continue

        if os.path.lexists(target):
            if _is_our_symlink(target, dist_path):
                status = "unchanged"
                message = "已指向当前产物（无需变更）"
                if mode == "copy":
                    status = "replaced"
                    message = "已从软链改为复制"
            elif mode == "copy" and os.path.isfile(target) and _same_content(target, dist_path):
                status = "unchanged"
                message = "目标内容与当前产物一致（无需变更）"
            else:
                status = "blocked"
                message = "目标已存在且不是本工具创建的软链，未覆盖；请手工合并或先移走该文件"
            if status == "blocked":
                actions.append({"host": host, "scope": scope, "target": target,
                                "status": status, "message": message})
                continue
        else:
            status = "created"
            message = "已放置" if mode == "symlink" else "已复制"

        if host == "cursor":
            message = "降级路径：" + message
        if dry_run:
            message = "dry-run：" + message

        if not dry_run and status in ("created", "replaced"):
            try:
                if mode == "symlink":
                    _place_symlink(target, dist_path)
                else:
                    _place_copy(target, dist_path)
            except OSError as exc:
                raise errors.make("IO_WRITE_FAILED", "放置宿主产物失败：%s（%s）" % (target, exc),
                                  location=target)
        actions.append({"host": host, "scope": scope, "target": target,
                        "status": status, "message": message})

    if "cursor" in hosts and project_dir is None:
        warnings.append("Cursor 无文件形式全局规则，属降级路径：需 fasti install-host --host cursor --project <DIR>")
    return {"actions": actions, "warnings": warnings}
