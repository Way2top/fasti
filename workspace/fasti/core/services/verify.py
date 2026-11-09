"""完整性自检应用服务：verify + 私有检查函数（§11.3.1 C/D）。"""


import os
import tempfile
from .. import errors, fold, store, validate
from .. import export as export_module
from .. import importer as importer_module
from .. import profile as profile_module
from ._shared import (_load_model)


def _check(check_id, status, detail, location=None):
    return {"id": check_id, "status": status, "detail": detail, "location": location}


def _canonical_body(path):
    text = store.read_text(path)
    offset = 0
    for line in text.split("\n"):
        if line.startswith("## "):
            return text[offset:], True
        offset += len(line) + 1
    return "", False


def _verify_consistency(data_dir):
    paths = store.paths(data_dir)
    files = [paths["dist_agents"], paths["dist_claude"], paths["dist_cursor"]]
    if not all(os.path.isfile(path) for path in files):
        return _check("derived.consistency", "skip", "宿主产物缺失（尚未渲染），跳过一致性比对")
    bodies = []
    found_any = False
    for path in files:
        body, found = _canonical_body(path)
        found_any = found_any or found
        bodies.append(body)
    if not found_any:
        return _check("derived.consistency", "pass", "三份宿主产物均无正文（空画像），视为一致")
    if bodies[0] != bodies[1] or bodies[0] != bodies[2]:
        return _check("derived.consistency", "fail",
                      "三份宿主产物的 canonical body 不一致（首个 '## ' 之后字节不同）")
    return _check("derived.consistency", "pass",
                  "三份宿主产物 canonical body 逐字节一致（%d B）"
                  % len(bodies[0].encode("utf-8")))


def _verify_determinism(events, model):
    try:
        model2 = fold.fold_events(list(events))
    except errors.CoreError as exc:
        return _check("derived.determinism", "fail", "二次折叠失败：%s" % exc.message)
    texts = [
        profile_module.render_profile_body(model),
        profile_module.render_host_body(model),
        profile_module.render_history_body(model),
    ]
    texts2 = [
        profile_module.render_profile_body(model2),
        profile_module.render_host_body(model2),
        profile_module.render_history_body(model2),
    ]
    if texts != texts2:
        return _check("derived.determinism", "fail", "同一事件流两次折叠/渲染结果不一致")
    return _check("derived.determinism", "pass",
                  "两次折叠与渲染逐字节一致（profile/host/history 共 %d B）"
                  % sum(len(text.encode("utf-8")) for text in texts))


def _verify_freshness(data_dir, raw, model):
    digest = store.bytes_digest(raw)
    manifest = store.load_manifest(data_dir, strict=False)
    if manifest is None:
        return _check("derived.freshness", "pass", "无 state/manifest.json：派生状态未知（待重算/待渲染）")
    notes = []
    if manifest.get("last_recompute_store_digest") != digest:
        notes.append("画像待重算")
    if manifest.get("last_render_store_digest") != digest:
        notes.append("宿主产物待更新")
    detail = "；".join(notes) if notes else "派生状态与事件日志一致"
    return _check("derived.freshness", "pass", detail)


def _verify_roundtrip(data_dir):
    try:
        with tempfile.TemporaryDirectory(prefix="fasti-verify-") as tmp:
            raw, _, model = _load_model(data_dir)
            out1 = os.path.join(tmp, "export1")
            export_module.export_bundle(data_dir, model, raw, out1)
            store2 = os.path.join(tmp, "store2")
            importer_module.import_bundle(store2, out1, overwrite=False, backup=False)
            raw2, _, model2 = _load_model(store2)
            out2 = os.path.join(tmp, "export2")
            export_module.export_bundle(store2, model2, raw2, out2)
            if raw != raw2:
                return _check("export.roundtrip", "fail", "导出→导入→再导出后 data/events.jsonl 字节不一致")
            first = export_module.build_bundle_files(model, raw)
            second = export_module.build_bundle_files(model2, raw2)
            if set(first) != set(second):
                return _check("export.roundtrip", "fail", "两次导出的文件集合不一致")
            for rel_path in sorted(first):
                if first[rel_path] != second[rel_path]:
                    return _check("export.roundtrip", "fail",
                                  "导出→导入→再导出的文件不一致：%s" % rel_path,
                                  location=rel_path)
            return _check("export.roundtrip", "pass",
                          "导出→导入→再导出往返无损（%d 个文件逐字节一致）" % len(first))
    except errors.CoreError as exc:
        return _check("export.roundtrip", "fail", "往返校验失败：%s" % exc.message,
                      location=exc.location)


def verify(data_dir, deep=False):
    data_dir = os.path.abspath(data_dir)
    checks = []
    try:
        raw, events, model = _load_model(data_dir)
    except errors.CoreError as exc:
        if exc.code == "STORE_FORMAT_UNSUPPORTED":
            # 格式门错误必须原样上报（退出码 5），不得降级为「检查失败」
            raise
        checks.append(_check("store.load", "fail", exc.message, exc.location))
        for check_id in ("store.invariants", "sql.constraints", "derived.determinism",
                         "derived.consistency", "derived.freshness"):
            checks.append(_check(check_id, "skip", "store.load 失败，无法继续"))
        if deep:
            checks.append(_check("export.roundtrip", "skip", "store.load 失败，无法继续"))
        return {"ok": False, "checks": checks}
    checks.append(_check("store.load", "pass",
                         "事件日志可解析（%d 条事件）" % len(events)))

    problems = validate.check_invariants(model)
    if problems:
        checks.append(_check("store.invariants", "fail", problems[0],
                             location=None))
    else:
        checks.append(_check("store.invariants", "pass", "领域不变量全部通过"))

    ok_sql, sql_detail = validate.check_sql_constraints(model)
    checks.append(_check("sql.constraints", "pass" if ok_sql else "fail", sql_detail))

    checks.append(_verify_determinism(events, model))

    try:
        checks.append(_verify_consistency(data_dir))
    except errors.CoreError as exc:
        checks.append(_check("derived.consistency", "fail", exc.message, exc.location))

    checks.append(_verify_freshness(data_dir, raw, model))

    if deep:
        checks.append(_verify_roundtrip(data_dir))

    ok = all(check["status"] != "fail" for check in checks)
    return {"ok": ok, "checks": checks}
