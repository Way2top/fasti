"""宿主产物与 profile/*.md 写出（C6）+ manifest 更新。

渲染文本由纯函数生成（profile.py），本模块只负责落盘与清单：
- 三份宿主产物共享同一段 canonical host body（ADR-010）；
- 允许的差异只出现在首个 `## ` 之前的头部（H1 / 自动生成说明 / Cursor frontmatter）；
- 产物正文不含生成时间或绝对路径（确定性，ADR-008）。
"""

from . import constants
from . import profile
from . import store

HOST_AGENTS_HEADER = (
    "# 个人画像（面向 AGENTS.md 宿主）\n"
    "\n"
    "> 本文件由 fasti 自动生成，请勿手工修改；修改请使用 fasti 命令后重新渲染。\n"
    "> 数据源：events.jsonl（本工具数据目录）\n"
    "\n"
)
HOST_CLAUDE_HEADER = (
    "# 个人画像（面向 CLAUDE.md 宿主）\n"
    "\n"
    "> 本文件由 fasti 自动生成，请勿手工修改；修改请使用 fasti 命令后重新渲染。\n"
    "> 数据源：events.jsonl（本工具数据目录）\n"
    "\n"
)
HOST_CURSOR_HEADER = (
    "---\n"
    "description: 个人画像（由 fasti 自动生成，请勿手工修改）\n"
    "alwaysApply: true\n"
    "---\n"
    "\n"
    "# 个人画像（面向 Cursor 规则）\n"
    "\n"
    "> 本文件由 fasti 自动生成，请勿手工修改；修改请使用 fasti 命令后重新渲染。\n"
    "> 数据源：events.jsonl（本工具数据目录）\n"
    "\n"
)

INSTALL_DOC = """# Fasti 宿主接入说明

> 本文件由 fasti 自动生成，请勿手工修改；修改事实后重新运行 fasti render。

## 0. 产物位置（都在数据目录 dist/ 下）

- `dist/AGENTS.md` → DSH
- `dist/CLAUDE.md` → Claude Code
- `dist/.cursor/rules/fasti.mdc` → Cursor

## 1. 主接入路径：用户级全局位置（一次放置、所有项目生效）

### DSH

- 命令：`fasti install-host --host agents`
- 位置：`~/.dsh/AGENTS.md`（DSH 用户全局指令链，与项目无关）
- 验证：新开一个会话，问「我是谁、我在做什么」，应能说出技术背景、当前项目、沟通偏好各 1 项。

### Claude Code

- 命令：`fasti install-host --host claude`
- 位置：`~/.claude/CLAUDE.md`（用户级记忆，所有项目生效）
- 验证：同上问句。

## 2. 降级路径：Cursor（无文件形式的用户级全局规则）

Cursor 的 User Rules 位于 Customize → Rules（是设置项，不是文件），因此只能降级为项目级放置：

- 命令：`fasti install-host --host cursor --project <项目目录>`
- 位置：`<项目目录>/.cursor/rules/fasti.mdc`
- 注意：这是**降级路径**，每个使用 Cursor 的项目各需放置一次，不要误以为已全局生效。

两种方式二选一（避免重复注入）：

1. 项目级 `.mdc` 文件（推荐；软链后随渲染同步）；
2. 把本文件对应的 host body 手工粘进 Cursor 的 User Rules（一次性；更新后需重粘）。

## 3. 生效时机

宿主在**新会话**开始时读取上述文件；运行中的会话不保证热更新。
因此：修改事实 → `fasti render` → 宿主**新开会话**才用上新值。

## 4. 其它说明

- 项目级放置是次选（P1 项目背景场景）：可把 dist 产物复制或软链到项目根的 `AGENTS.md` / `CLAUDE.md`。
- `fasti install-host --dry-run` 可以先预览将执行的动作。
- 若目标位置已存在用户手写的文件，`install-host` 不会覆盖，请手工合并。
- 状态检查：`fasti status` 会显示各宿主接入状态（全局已接入 / 仅项目级 / 未接入）。
"""

HOST_KINDS = ("host_agents", "host_claude", "host_cursor")
PROFILE_KINDS = ("profile_current", "profile_history")


# ------------------------------------------------------------------ 组装文本
def compose_host_text(kind, host_body):
    if kind == "host_agents":
        return HOST_AGENTS_HEADER + host_body
    if kind == "host_claude":
        return HOST_CLAUDE_HEADER + host_body
    if kind == "host_cursor":
        return HOST_CURSOR_HEADER + host_body
    raise ValueError("未知宿主产物类型：%r" % (kind,))


def artifact_texts(model):
    """生成全部派生文本（纯函数结果，不写盘）。"""
    host_body = profile.render_host_body(model)
    return {
        "profile_current": profile.render_profile_body(model),
        "profile_history": profile.render_history_body(model),
        "host_agents": compose_host_text("host_agents", host_body),
        "host_claude": compose_host_text("host_claude", host_body),
        "host_cursor": compose_host_text("host_cursor", host_body),
        "install_doc": INSTALL_DOC,
    }


def install_doc_text():
    return INSTALL_DOC


# ------------------------------------------------------------------ 写盘
def _manifest_artifact(kind, raw, source_digest, written_at):
    return {
        "kind": kind,
        "sha256": store.sha256_hex(raw),
        "bytes": len(raw),
        "source_digest": source_digest,
        "written_at": written_at,
    }


def write_artifacts(data_dir, model, kinds, store_digest_value, written_at, phase):
    """写指定产物并更新 manifest。

    phase="recompute" ⇒ 更新 last_recompute_store_digest；
    phase="render"    ⇒ 更新 last_render_store_digest。
    返回 [{path, kind, bytes, sha256}]。
    """
    texts = artifact_texts(model)
    manifest = store.load_manifest(data_dir, strict=False)
    if manifest is None:
        manifest = store.fresh_manifest(store_digest_value)
    written = []
    for kind in kinds:
        rel_path = constants.ARTIFACT_PATHS[kind]
        raw = texts[kind].encode("utf-8")
        target = store.artifact_abs_path(data_dir, rel_path)
        store.write_bytes_atomic(target, raw)
        manifest["artifacts"][rel_path] = _manifest_artifact(kind, raw, store_digest_value, written_at)
        written.append({
            "path": rel_path,
            "kind": kind,
            "bytes": len(raw),
            "sha256": store.sha256_hex(raw),
        })
    manifest["store_digest"] = store_digest_value
    if phase == "recompute":
        manifest["last_recompute_store_digest"] = store_digest_value
    elif phase == "render":
        manifest["last_render_store_digest"] = store_digest_value
    else:
        raise ValueError("未知 phase：%r" % (phase,))
    manifest["updated_at"] = written_at
    store.save_manifest(data_dir, manifest)
    return written


def host_body_bytes(model):
    return len(profile.render_host_body(model).encode("utf-8"))


def host_body_text(model):
    return profile.render_host_body(model)
