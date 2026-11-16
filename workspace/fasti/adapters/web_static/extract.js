(function () {
  "use strict";
  var TOKEN = new URLSearchParams(window.location.search).get("t") || "";
  var config = {};

  function call(path, options) {
    options = options || {};
    var headers = { "X-Fasti-Token": TOKEN };
    if (options.body) { headers["Content-Type"] = "application/json"; }
    return fetch(path, {
      method: options.method || "GET", headers: headers,
      body: options.body ? JSON.stringify(options.body) : undefined,
      cache: "no-store", credentials: "omit"
    }).then(function (response) {
      return response.json().catch(function () { return null; }).then(function (payload) {
        if (!response.ok || !payload || payload.ok === false) {
          throw (payload && payload.error) || { code: "HTTP_" + response.status,
                                                message: "请求失败" };
        }
        return payload.data;
      });
    });
  }

  function text(id, value) {
    var node = document.getElementById(id);
    if (node) { node.textContent = value; }
  }

  function msg(value, bad) {
    var node = document.getElementById("extract-msg");
    node.textContent = value;
    node.className = bad ? "msg bad" : "msg";
  }

  function renderStatus(data) {
    config = data;
    text("extract-status",
      "状态：" + (data.enabled ? "已启用" : "已关闭") +
      "；自动写入：" + (data.auto_write ? "开" : "关") +
      "；同意：" + (data.consent.accepted ? "已接受" : "未接受") +
      "；配置：" + (data.config_ok ? "有效" : "缺失 " + data.config_missing.join(",")));
    text("extract-consent-text", data.consent.text);
    document.getElementById("extract-enable").checked = !!data.enabled;
    document.getElementById("extract-auto-write").checked = !!data.auto_write;
    document.getElementById("extract-accept").checked = !!data.consent.accepted;
    document.getElementById("cfg-base-url").value = "";
    document.getElementById("cfg-base-url").placeholder =
      data.base_url_host ? ("当前：" + data.base_url_host) : "http://127.0.0.1:11434/v1";
    document.getElementById("cfg-model").placeholder =
      data.model ? ("当前：" + data.model) : "model 名称";
    text("extract-key-state", data.api_key_set ? ("已设置 " + data.api_key_masked)
                                               : "未设置");
    renderQueue(data.queue ? data.queue.pending || 0 : 0);
  }

  function renderQueue(count) {
    call("/api/extract/candidates?status=pending").then(function (data) {
      var list = document.getElementById("extract-queue");
      list.innerHTML = "";
      if (!data.items.length) {
        list.appendChild(document.createTextNode("（暂无待审候选）"));
        return;
      }
      data.items.forEach(function (candidate) {
        var row = document.createElement("li");
        var content = document.createElement("div");
        var conflict = candidate.relation.llm === "update" ? "更新候选" :
          (candidate.relation.llm === "conflict" ? "冲突候选" :
           (candidate.system.similar ? "系统相似" : ""));
        content.textContent = candidate.content + "（" + candidate.category_key +
          " · extraction " + candidate.extraction_confidence +
          " · relation " + (candidate.relation.llm || "-") +
          " · 系统相似 " + (candidate.system.similar ? "是" : "否") +
          " · 召回 " + candidate.recall.mode + " · " + (conflict || "无冲突标记") + "）";
        row.appendChild(content);
        var evidence = document.createElement("div");
        evidence.className = "small";
        evidence.textContent = "来源：session " +
          (candidate.session_id || "-") + " · message_ids " +
          ((candidate.evidence && candidate.evidence.message_ids) || []).join(", ");
        row.appendChild(evidence);
        var actions = document.createElement("div");
        actions.className = "row";
        var approve = document.createElement("button");
        approve.type = "button"; approve.textContent = "通过（新增）";
        approve.addEventListener("click", function () { approveCandidate(candidate, "new"); });
        actions.appendChild(approve);
        var evolve = document.createElement("button");
        evolve.type = "button"; evolve.className = "secondary";
        evolve.textContent = "作为演进通过";
        evolve.addEventListener("click", function () { approveCandidate(candidate, "evolve"); });
        actions.appendChild(evolve);
        var correct = document.createElement("button");
        correct.type = "button"; correct.className = "secondary";
        correct.textContent = "作为纠正通过";
        correct.addEventListener("click", function () { approveCandidate(candidate, "correct"); });
        actions.appendChild(correct);
        var detail = document.createElement("button");
        detail.type = "button"; detail.className = "secondary"; detail.textContent = "详情";
        detail.addEventListener("click", function () { showDetail(candidate.candidate_id); });
        actions.appendChild(detail);
        var edit = document.createElement("button");
        edit.type = "button"; edit.className = "secondary"; edit.textContent = "编辑后通过";
        edit.addEventListener("click", function () {
          var content = window.prompt("编辑后的内容（保留原始/最终审计）", candidate.content);
          if (content === null || !content.trim()) { return; }
          var validFrom = window.prompt("生效时间（YYYY-MM / YYYY-MM-DD / 时刻；留空=当前）", "") || undefined;
          var changeType = candidate.relation.llm === "update" ? "correct" : "new";
          if (candidate.relation.llm === "update") {
            changeType = window.prompt("更新类：请选择 evolve（演进）或 correct（纠正）", "correct");
            if (changeType !== "evolve" && changeType !== "correct") { return; }
          }
          call("/api/extract/candidates/" + encodeURIComponent(candidate.candidate_id) +
               "/approve", { method: "POST", body: { change_type: changeType,
                                                   content: content,
                                                   valid_from: validFrom } })
            .then(function (data) {
              msg("编辑后已通过：" + data.fact.fact_id.replace(/-/g, "").slice(0, 12));
              refresh();
            })
            .catch(function (err) { msg(err.message || err.code, true); });
        });
        actions.appendChild(edit);
        var reject = document.createElement("button");
        reject.type = "button"; reject.className = "secondary"; reject.textContent = "拒绝";
        reject.addEventListener("click", function () {
          call("/api/extract/candidates/" + encodeURIComponent(candidate.candidate_id) +
               "/reject", { method: "POST", body: { reason: "" } })
            .then(function () { msg("已拒绝"); refresh(); })
            .catch(function (err) { msg(err.message || err.code, true); });
        });
        actions.appendChild(reject);
        row.appendChild(actions);
        list.appendChild(row);
      });
    }).catch(function (err) { text("extract-queue", err.message || err.code); });
  }

  function showDetail(candidateId) {
    call("/api/extract/candidates/" + encodeURIComponent(candidateId))
      .then(function (data) {
        var candidate = data.candidate;
        var lines = ["候选 " + candidate.candidate_id,
                     "状态：" + candidate.status + "；内容：" + candidate.content,
                     "粒度：" + (candidate.granularity === "summary" ?
                       ("summary（subject=" + (candidate.subject || "-") + "）") :
                       (candidate.granularity || "未知")),
                     "召回：mode=" + candidate.recall.mode + "，bytes=" +
                     candidate.recall.corpus_bytes + "，truncated=" +
                     candidate.recall.truncated];
        (data.recall_details || []).forEach(function (item) {
          lines.push("  · fact " + item.fact_id.replace(/-/g, "").slice(0, 12) +
                     " | " + item.category_key + (item.disabled ? "|关闭" : "") +
                     " | " + item.kind + " | " + item.content +
                     " | 原因：" + ((item.reasons || []).join(",") || "-"));
        });
        if (data.target_fact) {
          lines.push("目标事实（update/conflict）：fact " +
                     data.target_fact.fact_id.replace(/-/g, "").slice(0, 12) +
                     " | " + data.target_fact.category_key + " | " +
                     data.target_fact.content);
        }
        (data.evidence_preview || []).forEach(function (preview) {
          lines.push("证据 " + preview.message_id + "（#" + preview.ordinal + "）：" +
                     preview.snippet);
        });
        window.alert(lines.join("\n"));
      })
      .catch(function (err) { msg(err.message || err.code, true); });
  }

  function approveCandidate(candidate, changeType) {
    var body = { change_type: changeType };
    if (changeType === "new") {
      body.valid_from = window.prompt("生效时间（YYYY-MM / YYYY-MM-DD / 时刻；留空用当前时刻）", "") || undefined;
    } else {
      body.valid_from = window.prompt("新的生效时间（YYYY-MM / YYYY-MM-DD / 时刻）", "") || undefined;
    }
    call("/api/extract/candidates/" + encodeURIComponent(candidate.candidate_id) + "/approve",
         { method: "POST", body: body })
      .then(function (data) { msg("已通过：" + data.fact.fact_id.slice(0, 12)); refresh(); })
      .catch(function (err) {
        if (err.code === "CONFLICT_SIMILAR_FACT") {
          msg("检测到相似记录，需要确认令牌（请用 CLI extract approve --confirm-duplicate）", true);
        } else { msg(err.message || err.code, true); }
      });
  }

  function summaryMergeLine(run) {
    var merge = run && run.summary_merge;
    if (!merge) { return null; }                    // 旧 run：不显示，避免 null → null
    return "summary 合并：" + merge.before + " → " + merge.after +
      "（自动合并 " + merge.auto_merged + "，提示 " +
      ((merge.suggestion_groups || []).length) + " 组）";
  }

  function loadRuns() {
    call("/api/extract/runs?limit=10").then(function (data) {
      var box = document.getElementById("extract-runs");
      box.innerHTML = "";
      if (!data.items.length) {
        box.textContent = "（暂无运行记录）";
        return;
      }
      data.items.forEach(function (run) {
        var row = document.createElement("div");
        var head = document.createElement("button");
        head.type = "button"; head.className = "secondary";
        var coverage = run.coverage || {};
        head.textContent = (run.started_at || "-") + " · " + run.status +
          (run.legacy ? " · 旧记录信息不完整" : "") +
          " · 会话 " + ((run.counters || {}).sessions_processed || 0) +
          " / 进入抽取 " + ((run.counters || {}).sessions_entered_extraction || 0) +
          " / 候选 " + ((run.counters || {}).candidates_created || 0) +
          " / 未扫描 " + (coverage.unscanned_sessions !== undefined ?
                          coverage.unscanned_sessions : "-");
        head.addEventListener("click", function () {
          call("/api/extract/runs/" + encodeURIComponent(run.run_id))
            .then(function (detail) {
              var item = detail.run;
              var lines = ["运行 " + item.run_id,
                           "状态：" + item.status +
                             (item.legacy ? "（旧记录信息不完整）" : "")];
              var coverage = item.coverage || {};
              if (coverage.unscanned_sessions !== undefined &&
                  coverage.unscanned_sessions !== null) {
                lines.push("未扫描会话：" + coverage.unscanned_sessions +
                           "；预计还需 " + coverage.estimated_runs_remaining + " 次");
              }
              var mergeLine = summaryMergeLine(item);
              if (mergeLine) { lines.push(mergeLine); }
              var skipped = 0;
              (item.sessions || []).forEach(function (session) {
                if (session.status === "no-new-messages") { skipped++; return; }
                var title = session.title || {};
                var counts = session.candidates || {};
                var detail = "抽取 " + (counts.extracted !== undefined ? counts.extracted : "-") +
                  " → 保留 " + (counts.kept !== undefined ? counts.kept : "-");
                if (counts.dropped_by_session_cap) {
                  detail += "；上限丢弃 " + counts.dropped_by_session_cap;
                }
                if (counts.dropped_duplicate_summary) {
                  detail += "；重复 summary " + counts.dropped_duplicate_summary;
                }
                if (counts.duplicates) { detail += "；重复 " + counts.duplicates; }
                lines.push((title.text || "不可用") + "（" +
                           (title.source || "unavailable") + "）| " +
                           (session.decision || "-") + " | " +
                           (session.reason || "-") + " | 消息 " +
                           (session.user_message_count !== undefined ?
                            session.user_message_count : "-") + " | " + detail);
              });
              if (!(item.sessions || []).length) {
                lines.push("本次运行没有处理任何会话");
              } else if (skipped) {
                lines.push("另有 " + skipped + " 个会话已处理过、本次无新消息");
              }
              window.alert(lines.join("\n"));
            })
            .catch(function (err) { msg(err.message || err.code, true); });
        });
        row.appendChild(head);
        var link = document.createElement("button");
        link.type = "button"; link.textContent = "查看本次候选";
        link.addEventListener("click", function () {
          call("/api/extract/candidates?status=all&run_id=" +
               encodeURIComponent(run.run_id)).then(function (data) {
            msg("本次候选 " + data.items.length + " 条（ID：run " + run.run_id + "）");
          }).catch(function (err) { msg(err.message || err.code, true); });
        });
        row.appendChild(link);
        box.appendChild(row);
      });
    }).catch(function (err) { text("extract-runs", err.message || err.code); });
  }

  function refresh() {
    call("/api/extract/status").then(function (data) {
      renderStatus(data);
    }).catch(function (err) {
      text("extract-status", "读取失败：" + (err.message || err.code));
    });
  }

  document.getElementById("extract-save").addEventListener("click", function () {
    var patch = { extract: {
      enabled: document.getElementById("extract-enable").checked,
      auto_write: document.getElementById("extract-auto-write").checked } };
    var base = document.getElementById("cfg-base-url").value.trim();
    var model = document.getElementById("cfg-model").value.trim();
    var key = document.getElementById("cfg-api-key").value;
    if (base || model || key) {
      patch.llm = {};
      if (base) { patch.llm.base_url = base; }
      if (model) { patch.llm.model = model; }
      if (key) { patch.llm.api_key = key; }
    }
    call("/api/extract/config", { method: "POST", body: { patch: patch } })
      .then(function (data) { renderStatus(data); msg("配置已保存"); })
      .catch(function (err) { msg(err.message || err.code, true); });
  });

  document.getElementById("extract-accept").addEventListener("change", function (event) {
    call("/api/extract/consent", { method: "POST", body: { accepted: event.target.checked } })
      .then(function (data) { renderStatus(data); msg("同意状态已更新"); })
      .catch(function (err) { msg(err.message || err.code, true); });
  });

  document.getElementById("extract-run").addEventListener("click", function () {
    msg("正在提取（会调用外部 LLM）…");
    call("/api/extract/run", { method: "POST", body: { trigger: "manual" } })
      .then(function (data) {
        var counters = data.run.counters;
        msg("完成：会话 " + counters.sessions_processed + " / 消息 " +
            counters.messages_processed + "；候选 " + counters.candidates_created +
            "（自动写 " + counters.auto_written + "，待审 " + counters.queued + "）");
        if (data.run.watermark_rebuilt) {
          msg("提示：检测到水位缺失/损坏，已从事实证据重建水位");
        }
        refresh();
      })
      .catch(function (err) {
        if (err.code === "EXTRACT_DISABLED") { msg("提取已关闭", true); }
        else { msg(err.message || err.code, true); }
        refresh();
      });
  });

  refresh();
  loadRuns();
})();
