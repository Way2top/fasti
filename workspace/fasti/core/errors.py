"""稳定错误码与 CoreError（ADR-018）。

核心层只保证**错误码**稳定：
- CLI 适配器负责「错误码 → 退出码」映射；
- 网页适配器负责「错误码 → HTTP 状态码」映射；
两者都不得改变错误码语义。
"""

from . import constants

# 错误码 → 退出码（api-spec conventions.error_catalog，固定，不得自创映射）
EXIT_CODES = {
    # 用法
    "USAGE_INVALID_ARGUMENT": 2,
    "USAGE_UNKNOWN_HOST": 2,
    # 未找到
    "NOT_FOUND_FACT": 3,
    "NOT_FOUND_CATEGORY": 3,
    "NOT_FOUND_LINE": 3,
    "NOT_FOUND_PATH": 3,
    # 状态冲突
    "STORE_NOT_INITIALIZED": 5,
    "STORE_FORMAT_UNSUPPORTED": 5,
    "CONFLICT_STORE_EXISTS": 5,
    "CONFLICT_IMPORT_TARGET_NOT_EMPTY": 5,
    "CONFLICT_STORE_LOCKED": 5,
    "CONFLICT_PORT_IN_USE": 5,
    # 领域校验
    "VALIDATION_MISSING_VALID_FROM": 4,
    "VALIDATION_INVALID_MONTH": 4,
    "VALIDATION_CHANGE_TYPE_REQUIRED": 4,
    "VALIDATION_INVALID_CHANGE_TYPE": 4,
    "VALIDATION_UNEXPECTED_VALID_FROM": 4,
    "VALIDATION_MISSING_CONTENT": 4,
    "VALIDATION_CONTENT_MULTILINE": 4,
    "VALIDATION_CONTENT_TOO_LONG": 4,
    "VALIDATION_CATEGORY_NOT_ENABLED": 4,
    "VALIDATION_CATEGORY_DELETED": 4,
    "VALIDATION_INVALID_ID": 4,
    "VALIDATION_CATEGORY_KEY_TAKEN": 4,
    "VALIDATION_CATEGORY_KEY_INVALID": 4,
    "VALIDATION_CATEGORY_NAME_TAKEN": 4,
    "VALIDATION_CATEGORY_ALREADY_DELETED": 4,
    "VALIDATION_AMBIGUOUS_REFERENCE": 4,
    "VALIDATION_VALID_FROM_NOT_AFTER_PREVIOUS": 4,
    "VALIDATION_EVOLVE_TARGET_NOT_HEAD": 4,
    "VALIDATION_FACT_DELETED": 4,
    "VALIDATION_FACT_ALREADY_DELETED": 4,
    "VALIDATION_INVALID_HANDLING": 4,
    "VALIDATION_MISSING_MIGRATE_TARGET": 4,
    "VALIDATION_INVALID_MIGRATE_TARGET": 4,
    # 数据损坏 / 导入
    "CORRUPT_EVENT_LOG": 6,
    "CORRUPT_EVENT_SEQUENCE": 6,
    "CORRUPT_MANIFEST": 6,
    "IMPORT_INVALID_MANIFEST": 4,
    "IMPORT_UNSUPPORTED_FORMAT_VERSION": 4,
    "IMPORT_INVALID_RECORD": 4,
    "IMPORT_SEMANTIC_INVALID": 4,
    "IMPORT_PAYLOAD_CHECKSUM_MISMATCH": 4,
    "IMPORT_BACKUP_FAILED": 7,
    # 网页适配器（HTTP 403）
    "WEB_FORBIDDEN": 2,
    # IO / 内部
    "IO_READ_FAILED": 7,
    "IO_WRITE_FAILED": 7,
    "INTERNAL_UNEXPECTED": 99,
}

# 默认提示语：AC 要求逐字包含的关键词都写在这里（见 api-spec error_catalog.trigger）
DEFAULT_MESSAGES = {
    "USAGE_INVALID_ARGUMENT": "参数不合法",
    "USAGE_UNKNOWN_HOST": "未知宿主；可选值：all / agents / claude / cursor",
    "NOT_FOUND_FACT": "事实不存在",
    "NOT_FOUND_CATEGORY": "类别不存在",
    "NOT_FOUND_LINE": "命题线不存在",
    "NOT_FOUND_PATH": "路径不存在",
    "STORE_NOT_INITIALIZED": "数据目录未初始化，请先运行 fasti init",
    "STORE_FORMAT_UNSUPPORTED": "数据目录是旧格式（format_version != 2）：不支持向后兼容，请按迁移指引迁移到 v2（architecture.md §12.5）后重试",
    "CONFLICT_STORE_EXISTS": "数据目录已有事件日志，拒绝覆盖",
    "CONFLICT_IMPORT_TARGET_NOT_EMPTY": "导入目标非空：请先备份，再显式使用 --overwrite 覆盖",
    "CONFLICT_STORE_LOCKED": "另一个 fasti 写命令正在执行，写锁在 5 秒内未能获取",
    "CONFLICT_PORT_IN_USE": "监听端口已被占用；请改用 --port 0（由系统分配）或其他端口",
    "VALIDATION_MISSING_VALID_FROM": "缺少生效时间：请提供「从何时起为真」（--valid-from YYYY-MM）",
    "VALIDATION_INVALID_MONTH": "生效时间格式不合法，应为 YYYY-MM（或 YYYY-MM-DD）",
    "VALIDATION_CHANGE_TYPE_REQUIRED": "必须选择变更类型：演进（evolve）或纠正（correct）",
    "VALIDATION_INVALID_CHANGE_TYPE": "变更类型不合法；可选值：evolve（演进）/ correct（纠正）",
    "VALIDATION_UNEXPECTED_VALID_FROM": "纠正不得改变生效时间，请移除 --valid-from",
    "VALIDATION_MISSING_CONTENT": "缺少内容：内容不能为空",
    "VALIDATION_CONTENT_MULTILINE": "内容必须是单行，不能包含换行符",
    "VALIDATION_CONTENT_TOO_LONG": "内容过长，超出长度上限",
    "VALIDATION_CATEGORY_NOT_ENABLED": "目标类别未启用，无法录入事实",
    "VALIDATION_CATEGORY_DELETED": "目标类别已删除（不存在或未启用），无法录入事实",
    "VALIDATION_INVALID_ID": "ID 形式不合法：请提供完整 UUIDv7（36 字符）或 4~32 位十六进制短前缀",
    "VALIDATION_CATEGORY_KEY_TAKEN": "类别 key 已被占用",
    "VALIDATION_CATEGORY_KEY_INVALID": "类别 key 不合法：需匹配 ^[a-z][a-z0-9_-]{0,31}$",
    "VALIDATION_CATEGORY_NAME_TAKEN": "类别名称已被占用",
    "VALIDATION_CATEGORY_ALREADY_DELETED": "类别已删除，不能再执行该操作",
    "VALIDATION_AMBIGUOUS_REFERENCE": "引用可匹配多个对象，请使用更精确的 ID",
    "VALIDATION_VALID_FROM_NOT_AFTER_PREVIOUS": "新的生效时间必须晚于被演进段的生效时间",
    "VALIDATION_EVOLVE_TARGET_NOT_HEAD": "演进只能针对该命题线当前最新的事实段",
    "VALIDATION_FACT_DELETED": "该事实已删除，不能执行变更",
    "VALIDATION_FACT_ALREADY_DELETED": "该事实已删除，不能重复删除",
    "VALIDATION_INVALID_HANDLING": "存量事实处置方式不合法；可选值：migrate（迁移）/ delete（一并删除）",
    "VALIDATION_MISSING_MIGRATE_TARGET": "删除类别时必须提供迁移目标（--migrate-to）",
    "VALIDATION_INVALID_MIGRATE_TARGET": "迁移目标不合法：必须存在、未删除、已启用，且不是被删除类别",
    "CORRUPT_EVENT_LOG": "事件日志无法解析",
    "CORRUPT_EVENT_SEQUENCE": "事件序号不连续或首事件不是 store_initialized",
    "CORRUPT_MANIFEST": "派生状态清单 state/manifest.json 无法解析",
    "IMPORT_INVALID_MANIFEST": "导入 bundle 的 manifest.json 缺失或字段不合法",
    "IMPORT_UNSUPPORTED_FORMAT_VERSION": (
        "不支持的导出格式版本（当前支持版本 %d）：v1 导出包需先完成 UUIDv7 迁移"
        "（见 architecture.md §12.5），再用当前版本重新导出" % constants.FORMAT_VERSION
    ),
    "IMPORT_INVALID_RECORD": "导入记录结构不合法",
    "IMPORT_SEMANTIC_INVALID": "导入事件语义校验失败",
    "IMPORT_PAYLOAD_CHECKSUM_MISMATCH": "结构化数据校验失败：payload sha256 与 manifest 不一致",
    "IMPORT_BACKUP_FAILED": "覆盖导入前写自动备份失败，已中止（现有数据未改动）",
    "WEB_FORBIDDEN": "会话令牌无效或缺失",
    "IO_READ_FAILED": "读取文件失败",
    "IO_WRITE_FAILED": "写入文件失败",
    "INTERNAL_UNEXPECTED": "未预期内部错误",
}


class CoreError(Exception):
    """核心层唯一异常类型：稳定错误码 + 人类可读消息 + 可选定位。"""

    def __init__(self, code, message=None, details=None, location=None):
        self.code = code
        self.message = message if message is not None else DEFAULT_MESSAGES.get(code, code)
        self.details = details
        self.location = location
        super().__init__(self.message)

    @property
    def exit_code(self):
        return EXIT_CODES.get(self.code, 99)

    def to_dict(self):
        out = {"code": self.code, "message": self.message}
        if self.details is not None:
            out["details"] = self.details
        if self.location is not None:
            out["location"] = self.location
        return out


def make(code, message=None, details=None, location=None):
    """便捷构造 CoreError。"""
    return CoreError(code, message=message, details=details, location=location)
