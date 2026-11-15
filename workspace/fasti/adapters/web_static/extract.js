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
        content.textContent = candidate.content + "（" + candidate.category_key +
          " · extraction " + candidate.extraction_confidence +
          " · relation " + (candidate.relation.llm || "-") +
          " · 系统相似 " + (candidate.system.similar ? "是" : "否") +
          " · 召回 " + candidate.recall.mode + "）";
        row.appendChild(content);
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

  function refresh() {
    call("/api/extract/status").then(renderStatus).catch(function (err) {
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
        refresh();
      })
      .catch(function (err) {
        if (err.code === "EXTRACT_DISABLED") { msg("提取已关闭", true); }
        else { msg(err.message || err.code, true); }
        refresh();
      });
  });

  refresh();
})();
