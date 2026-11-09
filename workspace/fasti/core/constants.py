"""常量：命名落点、版本、容量预算、路径表。

命名落点集中在此文件（architecture.md ADR-017 落点 2）：
将来若再改名只需改这里的常量与模板文件。
"""

# --- 命名（ADR-017 / C-03） -------------------------------------------------
APP_NAME = "Fasti"
COMMAND = "fasti"
DEFAULT_DATA_DIR_NAME = ".fasti"
ENV_HOME_VAR = "FASTI_HOME"
CURSOR_RULE_FILENAME = "fasti.mdc"
EXPORT_PREFIX = "fasti-export-"
LICENSE = "Apache-2.0"
L0_SPEC_NAME = "Open Memory Ledger (OML)"

# --- 版本 -------------------------------------------------------------------
APP_VERSION = "0.1.0"
CONTRACT_VERSION = "1.4.0"
FORMAT_VERSION = 3
MANIFEST_VERSION = 1
BUNDLE_VERSION = 1
SCHEMA_VERSION = 3

# --- 内容约束（api-spec conventions.content_rules / AS-08） ------------------
# C-08 相似检测（ADR-027）：多级常量，不提供用户配置项
SIMILARITY_THRESHOLD = 0.60          # Tier1：措辞高度接近
SIMILARITY_TOPIC_THRESHOLD = 0.25    # Tier2：粗相似下限
SIMILARITY_MIN_SHARED_TOKENS = 2     # Tier2：共享 ASCII 实体词门槛
SIMILARITY_TOKEN_ALGORITHM = "ngram-entity-v2"

UUID7_CANONICAL_PATTERN = (r'^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}'
                          r'-[0-9a-f]{12}$')
CONTENT_MIN_LEN = 1
CONTENT_MAX_LEN = 2000
NOTE_MAX_LEN = 500
CATEGORY_NAME_MIN_LEN = 1
CATEGORY_NAME_MAX_LEN = 40
CATEGORY_KEY_RE = r"^[a-z][a-z0-9_-]{0,31}$"

# --- 容量预算（ADR-016） -----------------------------------------------------
HOST_BODY_RECOMMENDED_BYTES = 8192
HOST_BODY_WARN_BYTES = 16384

# --- 锁 ---------------------------------------------------------------------
LOCK_WAIT_SECONDS = 5.0
LOCK_POLL_SECONDS = 0.02

# --- 宿主全局位置（C-05：全局优先；ADR-011 / architecture §3.4） --------------
# None ⇒ 该宿主没有文件形式的用户级全局规则（Cursor：User Rules 是设置项）。
HOST_GLOBAL_PATHS = {
    "agents": "~/.dsh/AGENTS.md",
    "claude": "~/.claude/CLAUDE.md",
    "cursor": None,
}
HOST_SHARED_PATHS = {
    "agents": "~/.agents/AGENTS.md",
}
HOST_NAMES = ("agents", "claude", "cursor")
HOST_CHOICES = ("all", "agents", "claude", "cursor")

# --- 数据目录内部相对路径 ----------------------------------------------------
EVENTS_FILE = "events.jsonl"
PROFILE_CURRENT = "profile/current.md"
PROFILE_HISTORY = "profile/history.md"
DIST_AGENTS = "dist/AGENTS.md"
DIST_CLAUDE = "dist/CLAUDE.md"
DIST_CURSOR = "dist/.cursor/rules/fasti.mdc"
DIST_INSTALL = "dist/INSTALL.md"
MANIFEST_FILE = "state/manifest.json"
LOCK_FILE = "state/.lock"
BACKUPS_DIR = "state/backups"
EXPORTS_DIR = "exports"

ARTIFACT_PATHS = {
    "profile_current": PROFILE_CURRENT,
    "profile_history": PROFILE_HISTORY,
    "host_agents": DIST_AGENTS,
    "host_claude": DIST_CLAUDE,
    "host_cursor": DIST_CURSOR,
    "install_doc": DIST_INSTALL,
}

# --- 默认模板类别（templates/categories.json 的加载形参；init 时转成事件） ----
DEFAULT_TEMPLATE_ORDER = 1  # 仅占位：真实顺序见 templates/categories.json
