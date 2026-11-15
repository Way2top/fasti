"""C-09 配置（ADR-029 / api-spec conventions.extract.configuration）。

`<data-dir>/config.json`（0600）保存 llm 与 extract 配置；环境变量优先：
FASTI_LLM_BASE_URL / FASTI_LLM_MODEL / FASTI_LLM_API_KEY / FASTI_EXTRACT_DISABLED=1。
api_key 永不回显：状态只返回 api_key_set 与 api_key_masked('****abcd')。
"""

import copy
import json
import os
import tempfile

from . import constants
from . import errors
from . import store

CONFIG_REL = "config.json"
LEGACY_CONFIG_KEYS = ("auto_trigger_on_open", "auto_trigger_min_interval_seconds")
_MIGRATION = {}


def _write_raw_atomic(data_dir, raw_cfg):
    """原子写 config.json（0600）；失败抛 OSError。"""
    raw = (json.dumps(raw_cfg, sort_keys=True, ensure_ascii=False, indent=2)
           + "\n").encode("utf-8")
    handle = tempfile.NamedTemporaryFile(mode="wb", dir=data_dir, prefix=".config.",
                                         suffix=".tmp", delete=False)
    tmp_path = handle.name
    try:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        os.replace(tmp_path, config_path(data_dir))
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    try:
        os.chmod(config_path(data_dir), 0o600)
    except OSError:
        pass


def _migrate_raw(data_dir, raw):
    """config schema 1→2：删 legacy 键、置 2、原子重写；返回 (raw2, warning)。

    幂等：已是 schema 2 且无 legacy 键 ⇒ 不重写。失败 ⇒ 仅内存迁移 + 告警，不崩。
    """
    migrated = (raw.get("schema_version") != constants.EXTRACT_CONFIG_SCHEMA_VERSION
                or any(key in (raw.get("extract") or {}) for key in LEGACY_CONFIG_KEYS))
    if not migrated:
        previous = _MIGRATION.get(data_dir) or {}
        _MIGRATION[data_dir] = {"migrated": bool(previous.get("migrated")),
                                "persisted": True, "warning": None}
        return raw, None
    raw = copy.deepcopy(raw)
    extract = raw.get("extract") if isinstance(raw.get("extract"), dict) else {}
    for key in LEGACY_CONFIG_KEYS:
        extract.pop(key, None)
    raw["extract"] = extract
    raw["schema_version"] = constants.EXTRACT_CONFIG_SCHEMA_VERSION
    warning = None
    persisted = True
    try:
        _write_raw_atomic(data_dir, raw)
    except OSError as exc:
        persisted = False
        warning = "配置 schema 迁移未落盘（内存已迁移，下次重试）：%s" % exc
    _MIGRATION[data_dir] = {"migrated": True, "persisted": persisted,
                            "warning": warning}
    return raw, warning


def default_config():
    return {
        "schema_version": constants.EXTRACT_CONFIG_SCHEMA_VERSION,
        "llm": {
            "base_url": "",
            "model": "",
            "api_key": "",
            "timeout_seconds": constants.EXTRACT_LLM_READ_TIMEOUT,
            "max_retries": constants.EXTRACT_LLM_MAX_RETRIES,
            "json_mode": True,
        },
        "extract": {
            "enabled": False,
            "auto_write": False,
            "consent": {"version": constants.EXTRACT_CONSENT_VERSION,
                        "accepted_at": None, "text_sha256": None},
            "max_messages_per_run": constants.EXTRACT_MAX_MESSAGES_PER_RUN,
            "max_sessions_per_run": constants.EXTRACT_MAX_SESSIONS_PER_RUN,
            "auto_write_daily_limit": constants.EXTRACT_AUTO_WRITE_DAILY,
            "auto_write_per_run_limit": constants.EXTRACT_AUTO_WRITE_PER_RUN,
            "sensitive_filter": True,
            "sources": {"dsh_home": "~/.dsh", "allow_plain": False},
        },
    }


def config_path(data_dir):
    return os.path.join(data_dir, CONFIG_REL)


def _deep_merge(base, patch):
    result = copy.deepcopy(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config(data_dir):
    """读取配置（缺失 => 默认值）并执行 schema 1→2 迁移；env 覆盖优先。

    JSON 损坏/只读目录：告警但不崩，返回内存中可用配置（不写盘）。
    """
    cfg = default_config()
    path = config_path(data_dir)
    warning = None
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError) as exc:
            warning = "config.json 不可解析，使用默认配置（未写盘）：%s" % exc
            _MIGRATION[data_dir] = {"migrated": False, "persisted": False,
                                    "warning": warning}
            cfg["_config_warning"] = warning
            raw = None
        if raw is not None and not isinstance(raw, dict):
            warning = "config.json 必须是 JSON 对象，使用默认配置（未写盘）"
            _MIGRATION[data_dir] = {"migrated": False, "persisted": False,
                                    "warning": warning}
            cfg["_config_warning"] = warning
            raw = None
        if raw is not None:
            raw, migrate_warning = _migrate_raw(data_dir, raw)
            warning = warning or migrate_warning
            if warning:
                cfg["_config_warning"] = warning
            cfg = _deep_merge(cfg, raw)
    else:
        _MIGRATION[data_dir] = {"migrated": False, "persisted": True, "warning": None}
    env = os.environ
    if env.get("FASTI_LLM_BASE_URL"):
        cfg["llm"]["base_url"] = env["FASTI_LLM_BASE_URL"]
    if env.get("FASTI_LLM_MODEL"):
        cfg["llm"]["model"] = env["FASTI_LLM_MODEL"]
    if env.get("FASTI_LLM_API_KEY"):
        cfg["llm"]["api_key"] = env["FASTI_LLM_API_KEY"]
    if env.get("FASTI_EXTRACT_DISABLED") == "1":
        cfg["extract"]["enabled"] = False
    return cfg


def _validate_config(cfg):
    llm = cfg.get("llm") or {}
    extract = cfg.get("extract") or {}
    problems = []
    base_url = llm.get("base_url") or ""
    if base_url and not (base_url.startswith("http://") or base_url.startswith("https://")):
        problems.append(("llm.base_url", "必须为 http:// 或 https:// URL"))
    timeout = llm.get("timeout_seconds", 60)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 600:
        problems.append(("llm.timeout_seconds", "应为 1..600 的整数"))
    retries = llm.get("max_retries", 2)
    if isinstance(retries, bool) or not isinstance(retries, int) or not 0 <= retries <= 5:
        problems.append(("llm.max_retries", "应为 0..5 的整数"))
    if not isinstance(llm.get("json_mode", True), bool):
        problems.append(("llm.json_mode", "应为布尔值"))
    for key in ("enabled", "auto_write", "sensitive_filter"):
        if not isinstance(extract.get(key), bool):
            problems.append(("extract." + key, "应为布尔值"))
    for key in ("max_messages_per_run", "max_sessions_per_run",
                "auto_write_daily_limit", "auto_write_per_run_limit"):
        value = extract.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            problems.append(("extract." + key, "应为正整数"))
    consent = extract.get("consent") or {}
    if not isinstance(consent, dict):
        problems.append(("extract.consent", "应为对象"))
    sources = extract.get("sources") or {}
    if not isinstance(sources, dict):
        problems.append(("extract.sources", "应为对象"))
    elif not isinstance(sources.get("dsh_home"), str):
        problems.append(("extract.sources.dsh_home", "应为字符串路径"))
    if problems:
        raise errors.make("EXTRACT_LLM_CONFIG_INVALID",
                          "配置非法：%s" % "；".join("%s %s" % item for item in problems),
                          details={"fields": [item[0] for item in problems]})


def missing_llm_fields(cfg):
    llm = cfg.get("llm") or {}
    missing = []
    if not (llm.get("base_url") or "").strip():
        missing.append("llm.base_url")
    if not (llm.get("model") or "").strip():
        missing.append("llm.model")
    if not (llm.get("api_key") or "").strip():
        missing.append("llm.api_key")
    return missing


def save_config(data_dir, cfg):
    """原子写 config.json（0600）；不写入下划线内部键。"""
    cfg = {key: value for key, value in cfg.items() if not key.startswith("_")}
    _validate_config(cfg)
    store.ensure_dir(data_dir)
    _write_raw_atomic(data_dir, cfg)
    return cfg


def mask_api_key(api_key):
    if not api_key:
        return None
    tail = api_key[-4:] if len(api_key) >= 4 else api_key
    return "****" + tail


def config_status(data_dir):
    cfg = load_config(data_dir)
    _validate_config(cfg)
    llm = cfg["llm"]
    extract = cfg["extract"]
    consent = extract.get("consent") or {}
    base_url = llm.get("base_url") or ""
    host = ""
    if base_url:
        host = base_url.split("://", 1)[-1].split("/", 1)[0]
    missing = missing_llm_fields(cfg)
    return {
        "enabled": bool(extract.get("enabled")),
        "auto_write": bool(extract.get("auto_write")),
        "consent": {
            "version": consent.get("version"),
            "accepted_at": consent.get("accepted_at"),
            "accepted": bool(consent.get("accepted_at")),
            "text": constants.EXTRACT_CONSENT_TEXT,
        },
        "config_ok": not missing,
        "config_missing": missing,
        "api_key_set": bool(llm.get("api_key")),
        "api_key_masked": mask_api_key(llm.get("api_key")),
        "model": llm.get("model") or None,
        "base_url_host": host or None,
        "json_mode": bool(llm.get("json_mode", True)),
        "config_version": cfg.get("schema_version"),
        "config_migrated": bool((_MIGRATION.get(data_dir) or {}).get("migrated")),
        "config_persisted": bool((_MIGRATION.get(data_dir) or {}).get("persisted", True)),
        "config_warning": (_MIGRATION.get(data_dir) or {}).get("warning"),
        "max_messages_per_run": extract.get("max_messages_per_run"),
        "max_sessions_per_run": extract.get("max_sessions_per_run"),
        "auto_write_daily_limit": extract.get("auto_write_daily_limit"),
        "auto_write_per_run_limit": extract.get("auto_write_per_run_limit"),
        "sensitive_filter": bool(extract.get("sensitive_filter")),
        "sources": dict(extract.get("sources") or {}),
        "recall_limits": {
            "full_corpus_bytes": constants.EXTRACT_RECALL_FULL_BYTES,
            "retrieval_top_k": constants.EXTRACT_RECALL_TOP_K,
            "retrieval_max_bytes": constants.EXTRACT_RECALL_MAX_BYTES,
            "historical_top_k": constants.EXTRACT_HISTORICAL_TOP_K,
            "historical_max_bytes": constants.EXTRACT_HISTORICAL_MAX_BYTES,
        },
    }


def update_config(data_dir, patch):
    """应用 patch（llm.* / extract.*，不含 consent）并原子保存；返回 cfg。"""
    if not isinstance(patch, dict):
        raise errors.make("VALIDATION_INVALID_HANDLING", "patch 必须是对象",
                          details={"field": "patch"})
    patch = copy.deepcopy(patch)
    for section in ("llm", "extract"):
        if section in patch and not isinstance(patch[section], dict):
            raise errors.make("VALIDATION_INVALID_HANDLING",
                              "%s 必须是对象" % section, details={"field": section})
    if "extract" in patch:
        patch["extract"].pop("consent", None)
    cfg = _deep_merge(load_config(data_dir), patch)
    enabled = cfg["extract"].get("enabled")
    consent = cfg["extract"].get("consent") or {}
    if enabled is True and not consent.get("accepted_at"):
        raise errors.make("EXTRACT_CONSENT_REQUIRED",
                          "首次启用自动提取必须先接受一次性隐私告知"
                          "（fasti extract consent --accept）；会话内容将发送给外部 LLM（联网）")
    return save_config(data_dir, cfg)


def set_consent(data_dir, accepted):
    cfg = load_config(data_dir)
    if accepted:
        from . import clock
        cfg["extract"]["consent"] = {
            "version": constants.EXTRACT_CONSENT_VERSION,
            "accepted_at": clock.now_recorded_at(),
            "text_sha256": _text_sha256(constants.EXTRACT_CONSENT_TEXT),
        }
    else:
        cfg["extract"]["enabled"] = False
        cfg["extract"]["consent"] = {"version": constants.EXTRACT_CONSENT_VERSION,
                                     "accepted_at": None, "text_sha256": None}
    return save_config(data_dir, cfg)


def _text_sha256(text):
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def require_run_ready(data_dir):
    """extract_run 前置校验：enabled + consent + LLM 配置完整。返回 (cfg, status)。"""
    status = config_status(data_dir)
    if not status["enabled"]:
        raise errors.make(
            "EXTRACT_DISABLED",
            "自动提取已关闭：请先运行 fasti extract config --enable 开启",
            details={"location": "extract.enabled"})
    if not status["consent"]["accepted"]:
        raise errors.make(
            "EXTRACT_CONSENT_REQUIRED",
            "首次启用自动提取必须先接受一次性隐私告知（会话内容将发送给外部 LLM，联网）")
    if status["config_missing"]:
        raise errors.make(
            "EXTRACT_LLM_CONFIG_MISSING",
            "缺少 LLM 必需配置：%s（可用环境变量或 fasti extract config 设置；密钥不回显）"
            % "、".join(status["config_missing"]),
            details={"fields": status["config_missing"]})
    return load_config(data_dir), status
