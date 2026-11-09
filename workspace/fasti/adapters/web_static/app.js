(function () {
  "use strict";
  var TOKEN = new URLSearchParams(window.location.search).get("t") || "";
  var categoryNames = {};
  var pendingAdd = null;
  var dupCandidates = [];
  var dupConfirmToken = "";

  function displayTime(value) {
    if (!value) { return ""; }
    var day = value.slice(8, 10);
    var clockPart = value.slice(11, 19);
    if (clockPart === "00:00:00") {
      return day === "01" ? value.slice(0, 7) : value.slice(0, 10);
    }
    return value.slice(0, 19);
  }

  function call(path, options) {
    options = options || {};
    var headers = { "X-Fasti-Token": TOKEN };
    if (options.body) { headers["Content-Type"] = "application/json"; }
    return fetch(path, {
      method: options.method || "GET",
      headers: headers,
      body: options.body ? JSON.stringify(options.body) : undefined,
      cache: "no-store",
      credentials: "omit"
    }).then(function (response) {
      return response.json().catch(function () { return null; }).then(function (payload) {
        if (!response.ok || !payload || payload.ok === false) {
          var err = (payload && payload.error) || { code: "HTTP_" + response.status, message: "请求失败" };
          throw err;
        }
        return payload.data;
      });
    });
  }

  function clearErrors(form) {
    Array.prototype.forEach.call(form.querySelectorAll(".err"), function (node) { node.textContent = ""; });
  }

  function showError(form, err) {
    clearErrors(form);
    var message = (err.code ? "[" + err.code + "] " : "") + (err.message || "请求失败");
    var field = err.details && err.details.field;
    var slot = field ? form.querySelector('.err[data-field="' + field + '"]') : null;
    if (slot) { slot.textContent = message; } else {
      var msg = form.querySelector(".msg");
      if (msg) { msg.textContent = message; msg.className = "msg bad"; }
    }
  }

  function showOk(msgNode, text) {
    msgNode.textContent = text;
    msgNode.className = "msg";
    setTimeout(function () { msgNode.textContent = ""; }, 6000);
  }

  function loadStatus() {
    return call("/api/status").then(function (data) {
      var box = document.getElementById("status-box");
      var counts = data.counts;
      var lines = [];
      lines.push("事件 " + counts.events + " · 类别 " + counts.categories_total + "（启用 " + counts.categories_enabled +
        "）· 命题线 " + counts.lines + " · 事实 " + counts.facts + " · 已删除 " + counts.deleted_facts + " · 修订 " + counts.revisions);
      lines.push("当前画像：" + freshText(data.freshness.profile, "recompute"));
      lines.push("宿主产物：" + freshText(data.freshness.host_artifacts, "render"));
      var access = data.host_access.map(function (entry) {
        if (entry.host === "cursor" && entry.scope === "none") { return "cursor 降级（需项目级放置）"; }
        if (entry.scope === "global") { return entry.host + " 全局已接入" + (entry.current ? "" : "（内容不是最新）"); }
        return entry.host + " 未接入";
      }).join(" · ");
      lines.push("宿主接入：" + access);
      if (data.warnings && data.warnings.length) { lines.push("提示：" + data.warnings.join("；")); }
      box.textContent = lines.join("\n");
    }).catch(function (err) {
      document.getElementById("status-box").textContent = "状态读取失败：" + (err.message || err.code);
    });
  }

  function freshText(value, action) {
    if (value === "up-to-date") { return "已是最新"; }
    if (value === "pending-recompute") { return "待重算（点「重算画像」）"; }
    if (value === "pending-render") { return "待更新（点「渲染宿主产物」）"; }
    if (value === "missing") { return "缺失（点「" + (action === "recompute" ? "重算画像" : "渲染宿主产物") + "」）"; }
    if (value === "not-rendered") { return "尚未生成（点「" + (action === "recompute" ? "重算画像" : "渲染宿主产物") + "」）"; }
    return value;
  }

  function loadProfile() {
    return call("/api/profile").then(function (data) {
      document.getElementById("profile-text").textContent = data.text;
    }).catch(function (err) {
      document.getElementById("profile-text").textContent = "读取失败：" + (err.message || err.code);
    });
  }

  function loadHistory() {
    return call("/api/history").then(function (data) {
      document.getElementById("history-text").textContent = data.text;
    }).catch(function (err) {
      document.getElementById("history-text").textContent = "读取失败：" + (err.message || err.code);
    });
  }

  function loadCategories() {
    return call("/api/categories").then(function (data) {
      var alive = data.items.filter(function (item) { return !item.deleted_at; });
      categoryNames = {};
      alive.forEach(function (item) { categoryNames[item.key] = item.display_name; });
      var select = document.getElementById("add-category");
      select.innerHTML = "";
      var placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "请选择类别…";
      select.appendChild(placeholder);
      alive.forEach(function (item) {
        var option = document.createElement("option");
        option.value = item.key;
        option.textContent = item.display_name + (item.enabled ? "" : "（已关闭）");
        option.disabled = !item.enabled;
        select.appendChild(option);
      });
      var rows = document.getElementById("category-rows");
      rows.innerHTML = "";
      data.items.forEach(function (item) { rows.appendChild(categoryRow(item)); });
      refreshExistingFacts(select.value);
    }).catch(function (err) {
      document.getElementById("category-rows").innerHTML = "<tr><td colspan='5'>读取失败：" +
        (err.message || err.code) + "</td></tr>";
    });
  }

  function refreshExistingFacts(categoryKey) {
    var block = document.getElementById("existing-facts");
    var list = document.getElementById("existing-facts-list");
    if (!categoryKey) { block.style.display = "none"; list.innerHTML = ""; return; }
    return call("/api/facts?category=" + encodeURIComponent(categoryKey) + "&state=current")
      .then(function (data) {
        list.innerHTML = "";
        if (!data.items.length) { block.style.display = "none"; return; }
        data.items.forEach(function (fact) {
          var row = document.createElement("li");
          var button = document.createElement("button");
          button.type = "button";
          button.className = "link";
          button.textContent = displayTime(fact.valid_from) + " · " + fact.content;
          button.addEventListener("click", function () { selectExistingFact(fact); });
          row.appendChild(button);
          list.appendChild(row);
        });
        block.style.display = "block";
      })
      .catch(function () { block.style.display = "none"; });
  }

  function selectExistingFact(fact) {
    var form = document.getElementById("change-form");
    var select = document.getElementById("change-fact");
    var found = false;
    Array.prototype.forEach.call(select.options, function (option) {
      if (option.value === fact.fact_id) { found = true; }
    });
    if (!found) {
      var option = document.createElement("option");
      option.value = fact.fact_id;
      option.textContent = fact.fact_id.replace(/-/g, "").slice(0, 12) + " · " + fact.content;
      select.appendChild(option);
    }
    select.value = fact.fact_id;
    form.content.value = fact.content;
    form.change_type.value = "evolve";
    form.valid_from.value = "";
    document.getElementById("add-msg").textContent =
      "已选「" + fact.content + "」：请在上方选择演进/纠正并提交（不会新增重复事实）";
    form.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function showDupModal(err) {
    dupCandidates = (err.details && err.details.candidates) || [];
    dupConfirmToken = (err.details && err.details.confirm_token) || "";
    var list = document.getElementById("dup-candidates");
    list.innerHTML = "";
    dupCandidates.forEach(function (candidate) {
      var row = document.createElement("li");
      row.textContent = "相似度 " + candidate.similarity + " · " + candidate.content +
        "（fact " + candidate.fact_id.replace(/-/g, "").slice(0, 12) + "）";
      list.appendChild(row);
    });
    document.getElementById("dup-modal").style.display = "flex";
  }

  function hideDupModal() {
    document.getElementById("dup-modal").style.display = "none";
    pendingAdd = null;
  }

  function categoryRow(item) {
    var tr = document.createElement("tr");
    var cells = [
      item.key,
      item.display_name,
      item.origin === "template" ? "默认模板" : "自定义",
      item.deleted_at ? "已删除" : (item.enabled ? "启用" : "已关闭")
    ];
    cells.forEach(function (text) {
      var td = document.createElement("td");
      td.textContent = text;
      tr.appendChild(td);
    });
    var actions = document.createElement("td");
    if (!item.deleted_at) {
      var toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "secondary";
      toggle.textContent = item.enabled ? "关闭" : "启用";
      toggle.addEventListener("click", function () { toggleCategory(item, !item.enabled); });
      actions.appendChild(toggle);
      var remove = document.createElement("button");
      remove.type = "button";
      remove.className = "secondary";
      remove.style.marginLeft = "6px";
      remove.textContent = "删除";
      remove.addEventListener("click", function () { deleteCategory(item); });
      actions.appendChild(remove);
    }
    tr.appendChild(actions);
    return tr;
  }

  function toggleCategory(item, enabled) {
    call("/api/categories/" + encodeURIComponent(item.key) + "/enabled", {
      method: "POST",
      body: { enabled: enabled }
    }).then(refreshAll).catch(function (err) { alert((err.code || "") + " " + (err.message || "")); });
  }

  function deleteCategory(item) {
    var handling = window.prompt("删除类别「" + item.display_name + "」\n存量事实处置方式：输入 migrate（迁移到其他类别）或 delete（一并删除）", "migrate");
    if (!handling) { return; }
    if (handling !== "migrate" && handling !== "delete") { alert("处置方式只能是 migrate 或 delete"); return; }
    var payload = { handling: handling };
    if (handling === "migrate") {
      var candidates = Object.keys(categoryNames).filter(function (key) { return key !== item.key; });
      var target = window.prompt("迁移目标类别（输入 category_key，可选：" + candidates.join(" / ") + "）", candidates[0] || "");
      if (!target) { return; }
      payload.migrate_to = target;
    }
    call("/api/categories/" + encodeURIComponent(item.key) + "/delete", {
      method: "POST",
      body: payload
    }).then(refreshAll).catch(function (err) { alert((err.code || "") + " " + (err.message || "")); });
  }

  function loadFacts() {
    return call("/api/facts?state=all").then(function (data) {
      var select = document.getElementById("change-fact");
      select.innerHTML = "";
      var placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = data.items.length ? "请选择事实…" : "（暂无可修订事实，请先新增）";
      select.appendChild(placeholder);
      data.items.forEach(function (fact) {
        var option = document.createElement("option");
        option.value = fact.fact_id;
        var categoryName = categoryNames[fact.category_key] || fact.category_key;
        var content = fact.content.length > 40 ? fact.content.slice(0, 40) + "…" : fact.content;
        option.textContent = fact.fact_id.replace(/-/g, "").slice(0, 12) + " · " + categoryName + " · 自 " + displayTime(fact.valid_from) + " · " + content;
        select.appendChild(option);
      });
    });
  }

  function loadAt() {
    var form = document.getElementById("at-form");
    var at = form.at.value.trim();
    if (!at) { showError(form, { code: "VALIDATION_MISSING_VALID_FROM", message: "请填写查询时点（YYYY-MM / YYYY-MM-DD / YYYY-MM-DDTHH:MM[:SS]）", details: { field: "at" } }); return; }
    clearErrors(form);
    call("/api/profile/at?at=" + encodeURIComponent(at)).then(function (data) {
      document.getElementById("at-text").textContent = data.text;
    }).catch(function (err) {
      showError(form, err);
      document.getElementById("at-text").textContent = "";
    });
  }

  function refreshAll() {
    return loadCategories().then(loadFacts).then(function () {
      return Promise.all([loadStatus(), loadProfile(), loadHistory()]);
    });
  }

  document.getElementById("add-form").addEventListener("submit", function (event) {
    event.preventDefault();
    var form = event.currentTarget;
    var msg = document.getElementById("add-msg");
    clearErrors(form);
    var body = {
      category: form.category.value,
      content: form.content.value,
      valid_from: form.valid_from.value
    };
    var missing = null;
    if (!body.category) { missing = { field: "category", message: "请选择类别" }; }
    else if (!body.content.trim()) { missing = { field: "content", message: "请填写内容" }; }
    else if (!body.valid_from.trim()) { missing = { field: "valid_from", message: "请填写从何时起为真（YYYY-MM / YYYY-MM-DD / YYYY-MM-DDTHH:MM[:SS]）" }; }
    if (missing) {
      showError(form, {
        code: missing.field === "valid_from" ? "VALIDATION_MISSING_VALID_FROM" : "VALIDATION_MISSING_CONTENT",
        message: missing.message,
        details: { field: missing.field }
      });
      return;
    }
    call("/api/facts", { method: "POST", body: body }).then(function (data) {
      form.reset();
      refreshExistingFacts(form.category.value);
      showOk(msg, "已保存 " + data.fact.fact_id + "（画像已变更，记得点击「渲染宿主产物」）");
      refreshAll();
    }).catch(function (err) {
      if (err.code === "CONFLICT_SIMILAR_FACT") {
        pendingAdd = body;
        showDupModal(err);
      } else {
        showError(form, err);
      }
    });
  });

  document.querySelector("#add-form select[name=category]").addEventListener("change", function (event) {
    refreshExistingFacts(event.currentTarget.value);
  });

  document.getElementById("dup-confirm").addEventListener("click", function () {
    var body = pendingAdd;
    var modal = document.getElementById("dup-modal");
    if (!body || !dupConfirmToken) { hideDupModal(); return; }
    body = JSON.parse(JSON.stringify(body));
    body.confirm_token = dupConfirmToken;
    modal.style.display = "none";
    var form = document.getElementById("add-form");
    call("/api/facts", { method: "POST", body: body }).then(function (data) {
      form.reset();
      refreshExistingFacts(form.category.value);
      showOk(document.getElementById("add-msg"),
             "已确认新增 " + data.fact.fact_id + "（审计痕迹已保留）");
      pendingAdd = null;
      refreshAll();
    }).catch(function (err) { showError(form, err); pendingAdd = null; });
  });

  function switchToChange(changeType) {
    var form = document.getElementById("change-form");
    if (!dupCandidates.length) { hideDupModal(); return; }
    var candidate = dupCandidates[0];
    var select = document.getElementById("change-fact");
    var found = false;
    Array.prototype.forEach.call(select.options, function (option) {
      if (option.value === candidate.fact_id) { found = true; }
    });
    if (!found) {
      var option = document.createElement("option");
      option.value = candidate.fact_id;
      option.textContent = candidate.fact_id.replace(/-/g, "").slice(0, 12) + " · " + candidate.content;
      select.appendChild(option);
    }
    select.value = candidate.fact_id;
    form.change_type.value = changeType;
    form.content.value = pendingAdd ? pendingAdd.content : candidate.content;
    form.valid_from.value = pendingAdd ? pendingAdd.valid_from : "";
    document.getElementById("dup-modal").style.display = "none";
    document.getElementById("change-msg").textContent =
      "已预填「" + candidate.content + "」：请确认变更类型与生效时间后提交";
    form.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  document.getElementById("dup-evolve").addEventListener("click", function () { switchToChange("evolve"); });
  document.getElementById("dup-correct").addEventListener("click", function () { switchToChange("correct"); });
  document.getElementById("dup-cancel").addEventListener("click", hideDupModal);

  document.getElementById("change-form").addEventListener("submit", function (event) {
    event.preventDefault();
    var form = event.currentTarget;
    var msg = document.getElementById("change-msg");
    clearErrors(form);
    var factId = form.fact_id.value;
    if (!factId) { showError(form, { code: "NOT_FOUND_FACT", message: "请选择要修订的事实", details: { field: "fact_id" } }); return; }
    var changeType = form.change_type.value;
    if (!changeType) { showError(form, { code: "VALIDATION_CHANGE_TYPE_REQUIRED", message: "必须选择变更类型：演进（evolve）或纠正（correct）", details: { field: "change_type" } }); return; }
    var content = form.content.value;
    if (!content.trim()) { showError(form, { code: "VALIDATION_MISSING_CONTENT", message: "请填写新内容", details: { field: "content" } }); return; }
    var body = { change_type: changeType, content: content };
    if (changeType === "evolve") {
      if (!form.valid_from.value.trim()) {
        showError(form, { code: "VALIDATION_MISSING_VALID_FROM", message: "演进必须填写新的生效时间（YYYY-MM / YYYY-MM-DD / YYYY-MM-DDTHH:MM[:SS]）", details: { field: "valid_from" } });
        return;
      }
      body.valid_from = form.valid_from.value;
    }
    if (changeType === "correct" && form.note.value.trim()) { body.note = form.note.value; }
    call("/api/facts/" + encodeURIComponent(factId) + "/change", { method: "POST", body: body }).then(function (data) {
      form.reset();
      var action = data.change_type === "evolve" ? "已演进为新的生效段" : "已纠正（内容修订，区间不变）";
      if (data.auto_bumped) {
        action += "；已在同月/同日之后，按当前时刻 " + displayTime(data.fact.valid_from) + " 记录";
      }
      if (data.warnings && data.warnings.length) { showOk(msg, action + "（" + data.warnings.join("；") + "）"); } else { showOk(msg, action); }
      showOk(msg, action + "：画像已变更，记得点击「渲染宿主产物」");
      refreshAll();
    }).catch(function (err) { showError(form, err); });
  });

  document.getElementById("delete-fact").addEventListener("click", function () {
    var form = document.getElementById("change-form");
    var factId = form.fact_id.value;
    var msg = document.getElementById("change-msg");
    if (!factId) { showError(form, { code: "NOT_FOUND_FACT", message: "请先选择要删除的事实", details: { field: "fact_id" } }); return; }
    if (!window.confirm("删除事实 " + factId + "？（墓碑保留在历史中，当前画像不再出现）")) { return; }
    call("/api/facts/" + encodeURIComponent(factId) + "/delete", { method: "POST", body: {} }).then(function () {
      showOk(msg, "已删除（历史保留）");
      refreshAll();
    }).catch(function (err) { showError(form, err); });
  });

  document.getElementById("cat-add-form").addEventListener("submit", function (event) {
    event.preventDefault();
    var form = event.currentTarget;
    var msg = document.getElementById("cat-add-msg");
    clearErrors(form);
    if (!form.name.value.trim()) {
      showError(form, { code: "VALIDATION_MISSING_CONTENT", message: "请填写类别名称", details: { field: "name" } });
      return;
    }
    call("/api/categories", {
      method: "POST",
      body: { name: form.name.value, key: form.key.value || null, enabled: true }
    }).then(function (data) {
      form.reset();
      showOk(msg, "已新增类别 " + data.category.display_name);
      refreshAll();
    }).catch(function (err) { showError(form, err); });
  });

  document.getElementById("at-form").addEventListener("submit", function (event) {
    event.preventDefault();
    loadAt();
  });

  document.getElementById("refresh-profile").addEventListener("click", function () {
    loadProfile();
    loadStatus();
  });

  document.getElementById("recompute").addEventListener("click", function () {
    var msg = document.getElementById("render-msg");
    call("/api/recompute", { method: "POST", body: {} }).then(function (data) {
      showOk(msg, "已重算画像" + (data.host_pending ? "；宿主产物仍待更新" : "；宿主产物已是最新"));
      refreshAll();
    }).catch(function (err) { msg.textContent = (err.code || "") + " " + (err.message || ""); msg.className = "msg bad"; });
  });

  document.getElementById("render").addEventListener("click", function () {
    var msg = document.getElementById("render-msg");
    var detail = document.getElementById("render-detail");
    call("/api/render", { method: "POST", body: { host: "all" } }).then(function (data) {
      showOk(msg, "宿主产物已更新；宿主新会话生效");
      detail.style.display = "block";
      detail.textContent = data.artifacts.map(function (item) {
        return item.path + "（" + item.bytes + " B）";
      }).join("\n") + "\nhost_body " + data.host_body_bytes + " B";
      refreshAll();
    }).catch(function (err) { msg.textContent = (err.code || "") + " " + (err.message || ""); msg.className = "msg bad"; });
  });

  document.getElementById("at-text").textContent = "";
  refreshAll();
})();
