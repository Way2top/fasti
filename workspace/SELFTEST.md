# Fasti · P0 自测记录（SELFTEST）

> 角色：开发工程师（developer） · 日期：2026-11-09
> 依据：`artifacts/architecture.md` v1.1 / `artifacts/api-spec.yaml` v1.1 / `artifacts/database-schema.sql` v1 / `artifacts/product-spec.md` v3.0
> 性质：**以下全部命令均已实际执行**，输出为实测粘贴（仅隐去临时路径中的随机后缀、个别时间戳随执行时间变化）。

---

## 1. 环境与启动方式

| 项 | 实测值 |
|---|---|
| OS | macOS 26.5.2（Darwin arm64） |
| Python | 3.13.3（`Clang 17.0.0` 构建） |
| SQLite | 3.54.1（JSON1 内置；仅内存使用，不落地 `.db`） |
| 第三方依赖 | **无**（`pip install` 未执行任何包；`import` 扫描见 ST-44） |
| 启动方式 | 免安装：`python3 run.py <命令>`；或安装：`pip install --no-deps -e .` 后用 `fasti <命令>` |
| 数据目录 | 默认 `~/.fasti`；本轮自测全部使用 `--data-dir <临时目录>`，**未写入真实家目录**（install-host 用 `HOME` 沙箱） |

### 1.1 交付物（全部在 `workspace/` 下）

| 里程碑 | 文件 | 状态 |
|---|---|---|
| M1 核心数据层 | `fasti/core/{model,store,fold,ids,clock,errors}.py`、`fasti/core/api.py`（C0 薄门面）+ `fasti/core/services/*`（C0s 实现）、`fasti/core/validate*.py` + `fasti/core/category_templates.py` | 完成 |
| M2 画像与渲染 | `fasti/core/{profile,render}.py` | 完成 |
| M3 CLI 适配器 | `fasti/adapters/{cli,cli_parser,cli_io,cli_cmds_content,cli_cmds_store}.py`（22 命令） | 完成 |
| M4 网页表单 | `fasti/adapters/web.py` + `fasti/adapters/static_files.py` + `fasti/adapters/web_static/{index.html,style.css,app.js}` | 完成 |
| M5 导出/导入 | `fasti/core/{export,importer}.py` | 完成 |
| M6 自测与交付 | `workspace/SELFTEST.md`、`workspace/README.md`、`tests/_base.py` + 9 个 `tests/test_*.py` | 完成 |
| 附带 | `fasti/core/{status,install}.py`、`fasti/core/constants.py`、`fasti/core/templates/categories.json`、`fasti/core/schema/database-schema.sql`（`artifacts/` 的逐字节只读副本，sha256 `0ca3284c…457a`）、`pyproject.toml`、`run.py` | 完成 |

---

## 2. 自测用例表

> 「通过」= 实际执行符合预期；「部分」= 核心语义通过、但依赖真实宿主/设备/时间跨度的部分未覆盖（见 §6）。

| 编号 | 对应验收标准 | 操作（实际执行） | 实际结果 | 通过 |
|---|---|---|---|---|
| ST-01 | AC-10-1 / AC-8-1 | `init` | 数据目录创建；写入 1 条 `store_initialized` + 7 条 `category_added`，7 个模板类别默认启用（basics…goals） | ✅ |
| ST-02 | AC-1-2 | `fact add --category tech --content x`（故意不填生效时间） | 退出码 4，`错误 VALIDATION_MISSING_VALID_FROM：缺少生效时间：请提供「从何时起为真」（--valid-from YYYY-MM）`；连续两次仍无事件写入 | ✅ |
| ST-03 | AC-1-1 | `fact add --category tech --content "主力语言：Python" --valid-from 2025-01` | `已录入 fact-0001（line-0001 · 技术背景 · 生效 2025-01）`；`fact show` 五项字段齐全（内容/类别/来源/首次录入/生效时间） | ✅ |
| ST-04 | AC-2-1 / AC-2-5 | `profile show`（仅 1 条事实时） | 按 7 个启用类别分组；技术背景显示内容，其余 6 类显示「（未录入）」；无推测内容 | ✅ |
| ST-05 | AC-3-1 | `fact change fact-0001 --type evolve --content "主力语言：C++" --valid-from 2026-11` | `已追加 fact-0002`；`profile history` 两段并存：`C++（2026-11 至 今）` 与 `Java/Python（2025-01 至 2026-10）`，各标注变更类型 | ✅ |
| ST-06 | AC-4-1 | `profile at --at 2026-06` | `主力语言：Python（2025-01 至 2026-10）`，并含固定说明行「结果基于当前已知事实按生效时间回溯…」 | ✅ |
| ST-07 | AC-4-2 | `profile at --at 2024-01` | 输出恰好一行 `该时点无记录` | ✅ |
| ST-08 | AC-3-2 / AC-4-4 | `fact change fact-0001 --type correct --content "主力语言：Java" --note …` 后再查 2026-06 | 该时段显示 `Java（2025-01 至 2026-10）`；历史保留 `↳ 纠正（…）：原内容「主力语言：Python」`；当前画像仍为 C++；**不出现「Python 演进成 C++」的结论** | ✅ |
| ST-09 | AC-3-3 | `fact change fact-0001 --content x`（不写 `--type`） | 退出码 4，`VALIDATION_CHANGE_TYPE_REQUIRED：必须选择变更类型：演进（evolve）或纠正（correct）`；画像/历史不受影响（事件数不变） | ✅ |
| ST-10 | AC-2-2 | `profile show --trace` | 每个条目下出现 `↳ 溯源：fact-0002 · 首次录入 … · 最近变更：演进（…，共 1 次修订）` | ✅ |
| ST-11 | AC-2-3 | `recompute` 连续两次，比对 sha256 | `profile/current.md`、`profile/history.md` 两次哈希完全一致 | ✅ |
| ST-12 | AC-2-4 | `rm -rf <data>/profile` 后 `recompute`，比对 sha256 | 与删除前逐字节一致（`02e71cbf…` / `e4f438bf…`） | ✅ |
| ST-13 | AC-5-1 | `render` | 写出 4 个文件：`dist/AGENTS.md`、`dist/CLAUDE.md`、`dist/.cursor/rules/fasti.mdc`、`dist/INSTALL.md`；打印各文件字节数 | ✅ |
| ST-14 | AC-5-2 | 提取三份产物的 canonical body（首个 `## ` 起至文件末）并比对 | 三份**逐字节相同**（示例 71 B）；内容为 `## 技术背景\n- 主力语言：C++…` | ✅ |
| ST-15 | AC-5-3 | `render` 连续两次，比对三份产物哈希 | 两次哈希完全一致（产物不含生成时间） | ✅ |
| ST-16 | AC-5-4 | 手工把 `dist/AGENTS.md` 改成 `# 手工破坏…` 后 `render` | 产物恢复为 `# 个人画像（面向 AGENTS.md 宿主）`，且包含 `- 主力语言：C++`；`events.jsonl` 未被污染 | ✅ |
| ST-17 | AC-6-4 | `grep -c 未录入` 检查三份宿主产物 | 均为 0（空类别不进入宿主产物，无残留占位）；三份均非空 | ✅ |
| ST-18 | AC-7-2 | 修改事实后 `status`，再 `recompute`+`render` 后 `status` | 修改后：「当前画像：待重算（运行 fasti recompute）」「宿主产物：待更新（运行 fasti render）」；渲染后：「已是最新」 | ✅ |
| ST-19 | AC-7-1 | 演进为 C++ 并 `render` 后检查三份产物 | 三份产物均含 `主力语言：C++`（旧值只出现在 `profile/history.md`） | ✅ |
| ST-20 | AC-7-4 | 同一事实连续 3 次变更（1 次创建 + 2 次纠正）后 `profile history` | 3 条记录均保留且可区分（`↳ 变更：创建` + 2 条 `↳ 纠正（…）：原内容「…」`）；同秒内以 revision 序号定序（AS-10） | ✅ |
| ST-21 | AC-1-4 | `fact delete fact-0002` 后 `profile show` / `recompute` / `profile history` | 当前画像不含该条；重算后仍不含；历史显示 `（已删除 <时间>）` 与内容区间 | ✅ |
| ST-22 | AC-10-2 | `category add --name 家庭情况` + `fact add --category 家庭情况` | `已新增类别 cat-0008（custom-0008 · 家庭情况）`；画像出现「家庭情况」及内容；`render` 后进入宿主产物 | ✅ |
| ST-23 | AC-10-1 | `category disable goals` 后 `profile show` / `fact list` | 画像不再显示「长期目标」；`fact list` 仍可查询该类别（事实保留）；重算/渲染后宿主产物不含该类别 | ✅ |
| ST-24 | AC-10-5 | `category list` | 顺序为 7 个模板（template_order 1..7）在前，自定义类别（created_at 升序）在后；关闭/删除不出现于画像 | ✅ |
| ST-25 | AC-10-6 | 向不存在类别 / 未启用类别录入 | `NOT_FOUND_CATEGORY`（退出 3）/ `VALIDATION_CATEGORY_NOT_ENABLED`（退出 4，消息含「未启用」）；现有数据不受影响 | ✅ |
| ST-26 | AC-10-3 | `category delete state --handle-facts delete` | 该类别下 1 条事实打墓碑（`fact list --state deleted` 可见）；画像/产物不再显示；`profile history` 保留类别删除记录 | ✅ |
| ST-27 | AC-10-3 | `category delete family --handle-facts migrate --migrate-to tech` | `已删除类别 家庭3（存量事实：迁移至 技术背景）`；事实仍可见且归属改为「技术背景」 | ✅ |
| ST-28 | AC-8-2 | 整目录 `cp -R` 到另一路径后 `recompute` + `render`，比对 sha256 | `profile/current.md`、`profile/history.md`、`dist/AGENTS.md`、`dist/CLAUDE.md` 全部一致（无需补充其他隐藏位置数据） | ✅ |
| ST-29 | AC-8-3 | 直接查看数据文件 | `events.jsonl` 每行一个键排序紧凑 JSON（人类可读）；`profile/current.md`、`profile/history.md` 为纯 Markdown，无需专用工具 | ✅ |
| ST-30 | AC-11-1~11-3 | `export` | 输出 8 个文件：`index.md`（含 `okf_version: "0.1"` / `type: index`）、`profile.md`、`log.md`、`concepts/tech/line-0001.md`（含 `type: fact-line` 与扩展字段）、`data/events.jsonl`、`data/entities.jsonl`、`README.md`、`manifest.json`；entities 行类型统计 `{category: 8, fact_line: 2, fact: 3, fact_revision: 4}`，事实条数/内容/类别/时间与系统内一致 | ✅ |
| ST-31 | AC-11-5 | 连续两次 `export` 后 `diff -r` | 仅 `manifest.json` 的 `exported_at` 不同；其余文件（含 manifest 的 counts/payload/files）逐字节一致 | ✅ |
| ST-32 | AC-12-1 / AC-12-2 / AC-10-4 | 空环境 `import` 后 `recompute` + `render` | 导入 14 条事件（3 事实 / 8 类别）；`profile/current.md`、三份宿主产物与源环境 sha256 全部一致 | ✅ |
| ST-33 | AC-12-3 | 非空环境 `import`（不带 `--overwrite`） | 退出码 5，`CONFLICT_IMPORT_TARGET_NOT_EMPTY：导入目标非空：请先备份，然后显式使用 --overwrite 覆盖`；`events.jsonl` 字节不变；带 `--overwrite` 时自动写 `state/backups/events-<ts>.jsonl` | ✅ |
| ST-34 | AC-12-4 | 4 种坏 bundle：删字段 / 坏 JSON / 篡改内容不改 manifest / format_version=2 | 分别 `IMPORT_INVALID_RECORD`（location=`data/events.jsonl:9`，details.field=`data.content`）、`IMPORT_INVALID_RECORD`（`data/events.jsonl:5`）、`IMPORT_PAYLOAD_CHECKSUM_MISMATCH`（消息含「校验失败」）、`IMPORT_UNSUPPORTED_FORMAT_VERSION`；目标 store 未被创建/未被改动 | ✅ |
| ST-35 | AC-12-5 | `export → import 空环境 → 再 export`；`verify --deep` | 两次导出除 `exported_at` 外逐字节一致；`CHECK export.roundtrip PASS — 导出→导入→再导出往返无损（8 个文件逐字节一致）` | ✅ |
| ST-36 | AC-13-1 / AC-13-3 | `web --port 0` 后 HTTP 走完「新增→修订→画像→历史→渲染」 | 五个动作全部 HTTP 200；`/api/profile` 返回的 `text` 即核心层渲染文本（含纠正后内容），页面原样展示；`/api/render` 返回 4 个产物 | ✅ |
| ST-37 | AC-13-2 | `web_static/**` 全部静态文件外链扫描 + 监听地址检查 | `web_static/{index.html,style.css,app.js}` 中 `http(s)://` 匹配数均为 0、无 CDN/字体外链；`lsof` 显示监听 `127.0.0.1:<port>`（无 `0.0.0.0`）；`GET /` 200（5.6 KB，引用同源 `/style.css`、`/app.js`），两静态路由均 200 且 Content-Type 正确 | ✅（跨设备实测未做，见 §6） |
| ST-38 | AC-13-4 | 分别提交缺内容 / 缺类别 / 缺生效时间 / 缺变更类型 / 类别不存在 | `400 VALIDATION_MISSING_CONTENT field=content`、`400 … field=category`、`400 VALIDATION_MISSING_VALID_FROM field=valid_from`、`400 VALIDATION_CHANGE_TYPE_REQUIRED field=change_type`、`404 NOT_FOUND_CATEGORY field=category`；刷新后记录均未写入 | ✅ |
| ST-39 | 网页安全边界（ADR-020） | 无令牌 `/api/*`、无令牌 `/`、坏 Origin、坏 Host、PUT、非 JSON Content-Type | 分别为 `403`、`403`、`403`、`403`、`405`、`415`；不发送任何 CORS 允许头 | ✅ |
| ST-40 | AC-6-2 / AC-6-5 | `install-host`（`HOME` 指向临时沙箱） | agents/claude 创建软链到 `dist/` 产物（`~/.dsh/AGENTS.md`、`~/.claude/CLAUDE.md`）；二次运行 `unchanged`；`status` 显示两宿主「全局已接入」；cursor 提示需 `--project`（降级路径，输出标注「降级」） | ✅（真实宿主会话读取未实测，见 §6） |
| ST-41 | 冲突保护（ADR-011） | 目标位置预置用户手写 `AGENTS.md` 后 `install-host` | `blocked`，不覆盖，提示「请手工合并或先移走该文件」；`--dry-run` 不落盘 | ✅ |
| ST-42 | 并发（AS-14 / ADR-015） | 另一进程持有 `state/.lock` 时执行写命令 | 等待 5 秒后退出码 5，`CONFLICT_STORE_LOCKED`，数据未损坏 | ✅ |
| ST-43 | AC-13-2 / ADR-020 | 同一端口启动第二个 `web` | 退出码 5，`CONFLICT_PORT_IN_USE：…请改用 --port 0（由系统分配）或其他端口`（不静默换端口） | ✅ |
| ST-44 | 分层约束（ADR-019/ADR-022/ADR-014） | `unittest` 静态断言（**递归**扫描 `fasti/core/**`，含 `core/services/**`）+ 手工 grep | `fasti/core/**` 不 import `argparse/http/socketserver/webbrowser`、无 `print`/`argv`；全包无第三方 import（`非标准库 import: 无`）；`fasti/adapters/**` 不 import `fasti.core.services.*`（ADR-022 守卫）；`web_static/**` 无外部 URL | ✅ |
| ST-45 | 规范化字节（ADR-003） | 检查 `events.jsonl` | 每行 `json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False)`，文件末尾恰好一个 LF，seq 从 1 连续 | ✅ |
| ST-46 | AC-2-3 / AC-8-1 | `verify --deep` | 7 项检查全 PASS：`store.load` / `store.invariants` / `sql.constraints` / `derived.determinism` / `derived.consistency` / `derived.freshness` / `export.roundtrip`；结论「通过」 | ✅ |
| ST-47 | AC-12-2（重算语义） | `import` 成功后立即 `status` | 显示「尚未生成/待重算、待渲染」（导入删除 manifest，**不假装新鲜**） | ✅ |
| ST-48 | AC-13-1~13-5（防呆） | `node --check fasti/adapters/web_static/app.js` + 断言 IIFE 首尾行 | 修复 R-F1 后 **exit 0**（修复前 `SyntaxError: Unexpected token '}'`，exit 1）；断言首行 `(function () {`、次行 `"use strict";`、倒数第二行 `})();` | ✅ |
| ST-49 | 静态资源完整性（防呆） | 检查 `web_static/` 三文件存在/非空/单末尾 LF、`index.html` 仅同源引用、零外部 URL、与基线内联内容 sha256 一致 | 全部通过；`app.js` 内容 sha256 `0fb916a6…`、`style.css` 内容 sha256 `d6e0c406…` 与 `git show refactor-baseline:…/index.html` 的内联内容**逐字节相同** | ✅ |

**统计**：49 条手工/端到端自测用例（47 完全通过、2 条真实环境待验）+ **75 条自动化用例**（C-06 v2：UUIDv7/短前缀/格式门/类别复活/持久化短 ID 守卫/写路径原子性/前端字段契约等，见 §9）；无失败项。
自动化测试：`Ran 75 tests … OK`（见 §4.1 与 §9）。
> 注：下表 ST-01~ST-47 中出现的 `fact-0001` / `line-0001` / `cat-0002` 为 **v1（序号方案）历史记录**；C-06 迁移后同一行为以 UUIDv7 + 短前缀复现，见 §9。

---

## 3. 实测输出摘录（逐字粘贴）

### 3.1 自动化测试

```
$ cd workspace && python3 -m unittest discover -s tests -v
…（75 个用例，全 ok）
----------------------------------------------------------------------
Ran 75 tests in 2.631s

OK
```

### 3.2 端到端自检

```
$ python3 run.py --data-dir "$D" verify --deep
CHECK store.load PASS — 事件日志可解析（14 条事件）
CHECK store.invariants PASS — 领域不变量全部通过
CHECK sql.constraints PASS — 内存 SQLite 执行 database-schema.sql 约束通过
CHECK derived.determinism PASS — 两次折叠与渲染逐字节一致（profile/host/history 共 1623 B）
CHECK derived.consistency PASS — 三份宿主产物 canonical body 逐字节一致（71 B）
CHECK derived.freshness PASS — 派生状态与事件日志一致
CHECK export.roundtrip PASS — 导出→导入→再导出往返无损（8 个文件逐字节一致）
结论：通过
```

### 3.3 演进 / 纠正 / 回溯 / 历史

```
$ python3 run.py --data-dir "$D" fact change fact-0001 --type evolve --content "主力语言：C++" --valid-from 2026-11
演进：已追加 fact-0002（line-0001 · 生效 2026-11）
提示：fasti render

$ python3 run.py --data-dir "$D" profile at --at 2026-06
# 画像回溯 · 2026-06

> 结果基于当前已知事实按生效时间回溯（有效时间，月精度，区间左闭右开），不代表该时点的历史认知。

## 技术背景
- 主力语言：Python（2025-01 至 2026-10）

$ python3 run.py --data-dir "$D" fact change fact-0001 --type correct --content "主力语言：Java" --note "2025-01~2026-10 实际是 Java，Python 系误记"
纠正：已纠正 fact-0001（第 2 次修订）

$ python3 run.py --data-dir "$D" profile history
# 历史记录

> 由 fasti 生成，勿手工修改；按变更时间倒序（最新在前）；区间为生效时间（月精度，左闭右开）。

## 技术背景
- 主力语言：C++（2026-11 至 今）
  ↳ 变更：演进（2026-11-09T16:27:37+08:00）
- 主力语言：Java（2025-01 至 2026-10）
  ↳ 变更：创建（2026-11-09T16:27:31+08:00）
  ↳ 纠正（2026-11-09T16:27:37+08:00）：原内容「主力语言：Python」

## 类别变更
- 2026-11-09T16:27:31+08:00 新增类别「长期目标」（goals）
…
```

### 3.4 确定性（AC-2-3 / AC-2-4 / AC-5-3）

```
$ python3 run.py --data-dir "$D" recompute   # 连续两次
$ shasum -a 256 "$D/profile/current.md" "$D/profile/history.md"
02e71cbf9dac7116a9e13f00ff05c8add0bba79a671d56293ee2dc40e33f5781  …/profile/current.md
e4f438bf25064c8060e1dc64e6f5a9f8c6ae73a46ee56763b0d699dc8dfe756b  …/profile/history.md
$ rm -rf "$D/profile" && python3 run.py --data-dir "$D" recompute
$ shasum -a 256 "$D/profile/current.md" "$D/profile/history.md"
（同上，逐字节还原）

$ python3 run.py --data-dir "$D" render        # 连续两次后
（dist/AGENTS.md、dist/CLAUDE.md、dist/.cursor/rules/fasti.mdc 哈希两次一致）
```

### 3.5 三产物 canonical body 一致（AC-5-2）

```
三份 canonical body 逐一字节相同: True | bytes: 71
host_body 内容：
## 技术背景
- 主力语言：C++
## 家庭情况
- 有一个弟弟
```

### 3.6 导出 / 导入（AC-11-5 / AC-12-1/2/5）

```
$ python3 run.py --data-dir "$D" export
已导出：/tmp/fasti-demo.xxx/exports/fasti-export-20261109T082756/（8 个文件，2719 B 结构化数据）
校验：manifest.payload.sha256=8eb3ffc20b8adc4d9329459fa8f5ac056e6677231e84dcba4b0de0cf0aaabc8e
导入：fasti import /tmp/fasti-demo.xxx/exports/fasti-export-20261109T082756

$ diff -r <export1> <export2>
（仅 manifest.json 的 exported_at 不同）

$ python3 run.py --data-dir "$N" import <bundle>
已导入：14 条事件（3 条事实 / 8 个类别）
备份：无（目标为空或未要求备份）
下一步：fasti recompute && fasti render

（导入后 profile/current.md、dist/AGENTS.md、dist/CLAUDE.md、dist/.cursor/rules/fasti.mdc 与源 shasum 全部一致）
```

### 3.7 导入拒绝（AC-12-4）

```
错误 IMPORT_INVALID_RECORD：data/events.jsonl 第 9 行结构不合法：data.content 缺少必填字段
错误 IMPORT_PAYLOAD_CHECKSUM_MISMATCH：结构化数据校验失败：data/events.jsonl sha256 与 manifest 不一致（期望 8eb3ff…，实际 098b26…）
错误 IMPORT_INVALID_RECORD：data/events.jsonl 第 5 行结构不合法：不是合法 JSON（Expecting property name enclosed in double quotes: line 1 column 2 (char 1)）
错误 IMPORT_UNSUPPORTED_FORMAT_VERSION：不支持的导出格式版本 2（当前支持版本 1）；请升级 fasti 后重试
错误 IMPORT_INVALID_MANIFEST：导入 bundle 缺少 manifest.json
（以上退出码均为 4；失败时目标 store 未创建/未改动）
```

### 3.8 网页表单（AC-13-*）

```
$ python3 run.py --data-dir "$D" web --port 0 --no-browser
Fasti 网页表单已启动：http://127.0.0.1:51502/?t=sO3Rwd6-XYZzbISmqIcjd5NR3acQSR63
仅本机可访问；Ctrl-C 退出。
提示：首次使用请先运行 fasti init

$ lsof -nP -a -p <pid> -iTCP -sTCP:LISTEN
Python  <pid>  …  TCP 127.0.0.1:51502 (LISTEN)      # 只有回环地址

GET / -> HTTP:200, bytes:5595
GET /style.css -> HTTP:200, type:text/css; charset=utf-8
GET /app.js -> HTTP:200, type:application/javascript; charset=utf-8
GET / （无令牌）-> HTTP:403
fact.add: fact-0004
fact.change: correct 第 2 次修订
profile 含纠正后内容: True
history text 长度: 881 | 类别变更条数: 9
render 产物数: 4 host_body: 118 B

缺 content      -> HTTP:400 VALIDATION_MISSING_CONTENT | field= content
缺 category     -> HTTP:400 VALIDATION_MISSING_CONTENT | field= category
缺 valid_from   -> HTTP:400 VALIDATION_MISSING_VALID_FROM | field= valid_from
类别不存在      -> HTTP:404 NOT_FOUND_CATEGORY | field= category
变更缺类型      -> HTTP:400 VALIDATION_CHANGE_TYPE_REQUIRED | field= change_type
无令牌 /api -> HTTP:403   无令牌 / -> HTTP:403   PUT -> HTTP:405
坏 Origin -> HTTP:403     坏 Host -> HTTP:403    非 JSON Content-Type -> HTTP:415
```

### 3.9 宿主接入（AC-6-2 / AC-6-5，HOME 沙箱）

```
$ HOME=/tmp/sandbox python3 run.py --data-dir "$D" install-host --host all
宿主接入：
- agents：created → /tmp/sandbox/.dsh/AGENTS.md（全局）：已放置
- claude：created → /tmp/sandbox/.claude/CLAUDE.md（全局）：已放置
- cursor：skipped → -（项目级（降级））：Cursor 无文件形式用户级全局规则（降级路径）；需 --project <DIR> 做项目级放置
生效时机：宿主新会话

$ ls -la /tmp/sandbox/.dsh /tmp/sandbox/.claude
… AGENTS.md -> …/dist/AGENTS.md
… CLAUDE.md -> …/dist/CLAUDE.md

$ python3 run.py --data-dir "$D" install-host --host agents    # 二次
- agents：unchanged → …（全局）：已指向当前产物（无需变更）

$ python3 run.py --data-dir "$D" status | sed -n '/宿主接入/,$p'
宿主接入：
- agents：全局已接入（/tmp/sandbox/.dsh/AGENTS.md）
- claude：全局已接入（/tmp/sandbox/.claude/CLAUDE.md）
- cursor：降级（无文件形式全局规则，需 --project 放置）

# 冲突保护：目标预置用户手写文件
- agents：blocked → …/.dsh/AGENTS.md（全局）：目标已存在且不是本工具创建的软链，未覆盖；请手工合并或先移走该文件
```

### 3.10 并发与端口（ST-42 / ST-43）

```
$ （另一进程持有 state/.lock）python3 run.py --data-dir "$D" fact add --category tech --content "锁测试" --valid-from 2026-01
错误 CONFLICT_STORE_LOCKED：另一个 fasti 写命令正在执行，写锁在 5 秒内未能获取
exit=5   real 0m5.082s

$ python3 run.py --data-dir "$D" web --port 8791 --no-browser   # 端口已被占用
错误 CONFLICT_PORT_IN_USE：监听端口 8791 已被占用；请改用 --port 0（由系统分配）或其他端口
exit=5
```

### 3.11 分层与依赖（ST-44）

```
$ grep -rnE "^import |^from " fasti/core/*.py | grep -E "urllib|http|socket|requests|subprocess|argparse"
（空）

$ python3 - <<'PY' … 扫描全包 import 是否标准库 …
非标准库 import: 无

$ grep -rnoE "https?://[^\"' )]+" fasti/adapters/web_static/
（空：三个静态文件均无外部 URL）
```

---

## 4. 「如何验证」（从干净环境复现）

> 目标：不知道实现细节的人，照着做即可复现全部关键自测。全程离线、无需安装任何包。

### 4.0 准备

```bash
# 取得 workspace/ 目录后：
cd workspace
python3 -V                     # 期望 3.9+（实测 3.13.3）
python3 -c "import sqlite3; print(sqlite3.sqlite_version)"   # 期望 >= 3.38（需 JSON1）
```

### 4.1 一键自动化（覆盖 v2 标识/短前缀/格式门/类别复活/写路径原子性/前端字段契约/确定性/演进纠正/导入导出/CLI/网页/分层/静态资源，75 用例）

```bash
python3 -m unittest discover -s tests -v
# 期望最后三行：Ran 75 tests in … / OK
```

### 4.2 端到端手工场景（照抄命令，对比输出）

```bash
T=$(mktemp -d); echo "数据目录：$T"

python3 run.py --data-dir "$T" init
python3 run.py --data-dir "$T" fact add --category tech --content "主力语言：Python" --valid-from 2025-01
python3 run.py --data-dir "$T" fact change fact-0001 --type evolve --content "主力语言：C++" --valid-from 2026-11
python3 run.py --data-dir "$T" profile at --at 2026-06        # 期望：主力语言：Python（2025-01 至 2026-10）+ 回溯说明行
python3 run.py --data-dir "$T" profile at --at 2024-01        # 期望：该时点无记录
python3 run.py --data-dir "$T" fact change fact-0001 --type correct --content "主力语言：Java" --note "Python 系误记"
python3 run.py --data-dir "$T" profile at --at 2026-06        # 期望：主力语言：Java（2025-01 至 2026-10）
python3 run.py --data-dir "$T" profile history                # 期望：C++ 段 + Java 段 + 「原内容「主力语言：Python」」

# 确定性（AC-2-3/2-4/5-3）
python3 run.py --data-dir "$T" recompute
shasum -a 256 "$T/profile/current.md" "$T/profile/history.md" > /tmp/h1
python3 run.py --data-dir "$T" recompute
shasum -a 256 "$T/profile/current.md" "$T/profile/history.md" | diff /tmp/h1 - && echo "确定性：PASS"
rm -rf "$T/profile" && python3 run.py --data-dir "$T" recompute
shasum -a 256 "$T/profile/current.md" "$T/profile/history.md" | diff /tmp/h1 - && echo "删除派生后还原：PASS"

# 渲染与三产物一致（AC-5-1/5-2/5-3/6-4）
python3 run.py --data-dir "$T" render
python3 - "$T" <<'PY'
import sys, os
d = sys.argv[1]
paths = [os.path.join(d,"dist","AGENTS.md"), os.path.join(d,"dist","CLAUDE.md"),
         os.path.join(d,"dist",".cursor","rules","fasti.mdc")]
def body(p):
    text = open(p, encoding="utf-8").read(); off = 0
    for line in text.split("\n"):
        if line.startswith("## "): return text[off:]
        off += len(line) + 1
    return ""
bodies = [body(p) for p in paths]
print("canonical body 三份一致:", bodies[0] == bodies[1] == bodies[2])
print("宿主产物无「未录入」占位:", all("未录入" not in open(p, encoding="utf-8").read() for p in paths))
PY

# 自检（AC-2-3/2-4/5-2/12-5）
python3 run.py --data-dir "$T" verify --deep     # 期望：7 项全部 PASS，结论：通过

# 导出 → 空环境导入 → 再导出（AC-11/12）
python3 run.py --data-dir "$T" export
B=$(ls -d "$T"/exports/fasti-export-* | head -1); echo "bundle=$B"
N=$(mktemp -d); python3 run.py --data-dir "$N" import "$B"
python3 run.py --data-dir "$N" recompute && python3 run.py --data-dir "$N" render
python3 run.py --data-dir "$N" verify --deep     # 期望：结论：通过

# 错误路径抽查（期望退出码按注释）
python3 run.py --data-dir "$T" fact add --category tech --content x                       ; echo "exit=$?（期望 4）"
python3 run.py --data-dir "$T" fact change fact-0001 --content x                          ; echo "exit=$?（期望 4）"
python3 run.py --data-dir "$T" fact add --category nope --content x --valid-from 2026-11  ; echo "exit=$?（期望 3）"
python3 run.py --data-dir "$T" import "$B"                                                ; echo "exit=$?（期望 5，非空目标需 --overwrite）"
```

### 4.3 网页主界面（AC-13）

```bash
python3 run.py --data-dir "$T" web --port 8787
# 终端会打印 http://127.0.0.1:8787/?t=<一次性令牌>，浏览器通常自动打开；
# 若未自动打开，复制该 URL 到浏览器（令牌在 URL 中，缺失/错误会 403）。
```

浏览器内依次验证（同一页面完成）：

1. 状态区显示事件/类别/事实计数与「当前画像 / 宿主产物」状态。
2. 在「新增事实」填类别、内容、`YYYY-MM`，提交 → 顶部出现「已保存 …」；画像区立即变化。
3. 在「修订事实」选事实、**必须选择**「演进（evolve）」或「纠正（correct）」：
   - 演进：必须填新的生效时间；提交后历史出现两段并存；
   - 纠正：不填生效时间，可填备注；提交后当前值变、历史保留原内容。
4. 故意留空「内容」或「生效时间」提交 → 对应输入框下方出现红字错误（含错误码与字段名）。
5. 点「渲染宿主产物」→ 提示「宿主产物已更新；宿主新会话生效」，下方列出产物字节数。
6. 断开网络（或保持离线）重复 1–5：行为不变（页面无任何外部请求）。
7. 另一台设备用 `http://<本机局域网 IP>:8787` 访问 → 无法连接（只绑 `127.0.0.1`）。

停止：终端 `Ctrl-C`（或 `kill <pid>`），服务退出，不写任何常驻文件。

### 4.4 宿主接入（AC-6-2 / AC-6-5）

> 为避免影响真实家目录，建议用临时 HOME 验证：

```bash
H=$(mktemp -d)
HOME="$H" python3 run.py --data-dir "$T" render
HOME="$H" python3 run.py --data-dir "$T" install-host --host agents
HOME="$H" python3 run.py --data-dir "$T" install-host --host claude
ls -la "$H/.dsh" "$H/.claude"          # 期望：AGENTS.md / CLAUDE.md 为指向 dist 的软链
HOME="$H" python3 run.py --data-dir "$T" status | sed -n '/宿主接入/,$p'
# 期望：agents / claude 显示「全局已接入」

# 真实接入（确认无误后，去掉 HOME 前缀执行；只在你自己的机器上做）
python3 run.py --data-dir "$T" install-host --host all --dry-run   # 先看动作
```

生效验证：在 DSH / Claude Code **新开一个会话**问「我是谁、我在做什么」，应能说出技术背景、当前项目、沟通偏好各 1 项，取值与 `profile show` 一致（此步需真实宿主，见 §5）。

### 4.5 清理

```bash
rm -rf "$T" "$N" "$H"     # 自测数据均为临时目录；未写入 ~/.fasti 或真实家目录
```

---

## 5. 未覆盖项与原因

| 未覆盖项 | 原因 | 已做的替代验证 |
|---|---|---|
| **AC-6-1 / AC-6-3 / AC-6-5 的真实宿主会话效果** | 需要在本机 DSH / Claude Code / Cursor 中实际新开会话询问；本环境无法替代 | 验证了产物内容正确（含三宿主共享 body）、`install-host` 放置正确（软链指向 dist）、`status` 接入状态正确、`dist/INSTALL.md` 给出可复现步骤；宿主读取行为依赖 AS-01（宿主侧） |
| **AC-13-2「另一台设备不可访问」** | 本环境无第二台设备/局域网对端 | `lsof` 证明只监听 `127.0.0.1`；代码中 bind 常量固定为 `127.0.0.1`，无 `0.0.0.0`；页面零外部请求已扫描确认 |
| **AC-8-1 字面断网执行** | 不能安全断开本机网络 | 核心层不 import 任何网络模块（静态断言 + grep），全流程无网络调用；`verify`/`render`/`recompute` 均为本地计算 |
| **AC-9-3 / V-01~V-06（4 周价值验收）** | 时间跨度要求，属交付后人工验证 | 单次操作均为毫秒级；AC-9-1/13-5 的「≤30 分钟」量级无风险；每日自记由用户完成 |
| **AC-9-1 / AC-13-5 的真实计时** | 属 4 周使用场景（5 条新事实 + 2 处纠正的完整人工计时） | 单条录入/纠正/渲染命令耗时毫秒级；页面闭环五个动作同一页完成（ST-36） |
| **AC-11-4 第三方用通用工具复述** | 需要真人第三方 | 结构自检：`index.md`/`profile.md`/`log.md`/`concepts` 均为纯 Markdown（frontmatter + 标题 + 列表 + 相对链接），无私有语法；`data/entities.jsonl` 每行一个类型化对象 |
| **Python 3.9 实机运行** | 本机只有 3.13.3 | 未使用 3.10+ 语法（无 `match`、无 `X \| Y` 类型标注、无新标准库 API）；`requires-python = ">=3.9"` |
| **Windows** | 架构明确 POSIX（`fcntl`，AS-04） | 不承诺；README 已注明 |
| **导入合并/冲突解决** | 产品规格明确本迭代不做（候选需求） | 冲突语义只有「空环境还原 / 非空显式覆盖或取消」 |

**契约缺口/偏差说明（供 QA 注意）**：
1. 本实现额外提供 `fasti/core/schema/database-schema.sql` 副本（逐字节同 `artifacts/`），仅为离线执行 DDL 校验；不属于数据模型改动。
2. 核心 API 在契约的 20 个函数/helpers 之外增加了 3 个**纯渲染 helper**（`profile_text` / `profile_at_text` / `history_text`）与 `is_initialized`，用于网页适配器直接展示核心层渲染文本（避免前端第二套画像，AC-13-3）。API 语义与错误码未改动。
3. 网页端点按 `api-spec.yaml` §5 实现 16 个；未实现导出/导入（契约明确留给 CLI）。
4. `install-host` 在 `status` 里探测 `~/.dsh/AGENTS.md`、`~/.claude/CLAUDE.md`（含 `~/.agents/AGENTS.md` 共享根）与 Cursor 降级说明；不修改宿主配置。

---

## 6. 质量门禁自检（角色契约 §5）

- [x] 提交前本地自测**已实际执行**（§3 为实测输出；§4 可复现），不是「应该能跑」。
- [x] `SELFTEST.md` 含「如何验证」，且从干净环境可复现（临时目录 + 无安装 + 离线；§4.5 有清理步骤）。
- [x] 每条 P0 验收标准至少一条自测用例：AC-1-1~AC-13-5 共 59 条 → 覆盖见 §2（ST-19/ST-20/…）；3 条宿主会话类 AC 标注为「未覆盖 + 原因」，见 §5。
- [x] 代码可启动，冒烟命令有实际输出记录（§3.3/§3.8）。
- [x] 没有未声明的依赖（零第三方，静态扫描 + unittest 断言）；没有对 spec 类产物的改动（`artifacts/` 只读，仅复制了 DDL 副本）。
- [x] 核心层分层约束实测：无 `argparse`/`http`/`print`/`sys.argv`；适配层只调用 `core.api`。
- [x] 未用 TODO 占位；未覆盖项如实列出并给出替代验证。

---

## 7. 交付说明

- 全部实现位于 `workspace/`，入口 `python3 run.py …`（或安装后的 `fasti …`）。
- 运行方式与命令速览见 [`README.md`](README.md)。
- 自测统计：**自动化 75/75 通过（见 §9，含 QA 回归修复 4 条 + 前端字段契约 3 条）；手工/端到端 49 条用例中 47 条完全通过、2 条「核心语义通过 + 真实环境待验」、0 失败**。
- **等待 QA 独立验证**：以上「完成」仅为开发侧自测结论，不代表项目验收通过；QA 应按 `artifacts/product-spec.md` 的 59 条 AC 独立复现并给出 `pass` / `fail` 判定。

---

## 8. 文件规模治理（ADR-021 / ADR-022 / architecture §11 · 2026-11）

> 本章为**重构时点（v1 序号方案）**的记录；C-06 迁移后用例数变为 68、`app.js` 因 `category_key` 更新而不再与 v1 基线逐字节相同（相关字节 pin 已移除），文件结构见 §9.

> 目标：把 5 个超标文件拆到 ≤500 行（新文件目标 ≤300，仅 ADR-021 登记的 2 条豁免允许超目标）；
> 做法：**纯搬移**，不优化、不改逻辑/输出/错误码；`fasti.core.api` 仍是唯一 import 点。
> 基线：`git tag refactor-baseline`（`eadc1ff`，重构前最后一个 commit）。

### 8.1 拆分前后行数对照（`wc -l`）

| 原文件 | 重构前 | 拆分后 |
|---|---|---|
| `fasti/core/api.py` | 847 | **87** 门面 + `services/{_shared 138, facts 223, categories 143, views 107, lifecycle 80, verify 152}.py`（+`services/__init__ 5`、`core/category_templates.py 19`） |
| `fasti/adapters/cli.py` | 627 | **134** + `cli_parser 149` + `cli_io 72` + `cli_cmds_content 164` + `cli_cmds_store 182` |
| `fasti/core/validate.py` | 500 | **29** 门面 + `validate_fields 88` + `validate_events 195` + `validate_invariants 112` + `validate_sql 100` |
| `fasti/adapters/web_static/index.html` | 514 | **144** + `style.css 29` + `app.js 339` + `adapters/static_files.py 32` |
| `tests/test_fasti.py` | 598 | `_base 66` + 9 个 `test_*.py`（24–131 行） |
| `fasti/adapters/web.py` | 462 | **467**（静态路由 +5；ADR-021 存量豁免，<500） |

**最终扫描（含 QA 回归修复）：`workspace/` 下 `.py/.html/.css/.js` 共 51 个文件、6455 行，无任何文件 >500 行。**
超 300 行的文件仅 5 个：`web.py 467`（存量豁免）、`app.js 339`（ADR-021 目标豁免：单 IIFE 共享闭包）、
`fold.py 338` / `profile.py 337` / `store.py 329`（ADR-021 存量登记，未超硬上限、本次不拆）。
其余新文件全部 ≤300（最大 `services/facts.py 223`）。

### 8.2 重构 6 个 commit + QA 回归修复 2 个 commit（每个独立可 revert）

| # | commit | 范围 | 关键动作 |
|---|---|---|---|
| 1 | `98fd805` | `tests/` 拆分 | `_base.py` + 9 个测试文件；`test_layering.py` 改**递归**扫描并新增 ADR-022 守卫（用例 37 → 38） |
| 2 | `15877e7` | `core/api.py` 拆分 | `api.py` 改薄门面；`core/services/` 6 文件；`core/category_templates.py` |
| 3 | `a9ea28d` | `core/validate.py` 拆分 | 门面 + `validate_{fields,events,invariants,sql}.py` |
| 4 | `f0b1ba3` | `adapters/cli.py` 拆分 | `cli_parser` / `cli_io` / `cli_cmds_content` / `cli_cmds_store`；`main()` 用 `cli_io.set_context` |
| 5 | `fe11b65` | 静态资源拆分 | `style.css` / `app.js` 搬出；`static_files.py` + `web.py` 同源静态路由；`package-data` 补 css/js |
| 6 | `09e8ea3` | 文档收尾 | README / SELFTEST / 全量扫描：结构、ST-37/ST-44 口径、行数预算与证据 |
| 7 | **`f0c4085`** | **QA 回归修复** | **R-F1** app.js 补回 IIFE 首行（字节与基线一致）+ **R-F2** cli.py 恢复 `__main__` 守护；新增 2 条静态资源防呆测试（38 → 40 用例） |
| 8 | 本次文档 commit | SELFTEST / README | 修正「原样搬出」证据（§8.4.4）、新增 §8.6 根因与反思、同步 40 用例与行数（`git log -1`） |

### 8.3 逐 commit 自证（§11.4.2 四项）

| commit | unittest | AST（白名单外差异） | golden 比对 | `verify --deep` | >500 行剩余 |
|---|---|---|---|---|---|
| 1 `98fd805` | 38/38 OK | 未拆分项跳过（0 差异） | diff=0 | 7/7 PASS | api/cli/validate/index.html（后续 commit 处理） |
| 2 `15877e7` | 38/38 OK | api 部分 40/40 相等，0 差异 | diff=0 | 7/7 PASS | cli/index.html |
| 3 `a9ea28d` | 38/38 OK | 50/50 相等，0 差异 | diff=0 | 7/7 PASS | cli/index.html |
| 4 `f0b1ba3` | 38/38 OK | **80/80 相等**，0 差异 | diff=0 | 7/7 PASS | index.html |
| 5 `fe11b65` | 38/38 OK | 80/80 相等，0 差异 | diff=0（页面级白名单差异除外） | 7/7 PASS | **0** |
| 6（收尾） | 38/38 OK | 80/80 相等（`--strict`） | diff=0 | 7/7 PASS | **0** |
| 7 `f0c4085`（QA 修复） | **40/40 OK** | 80/80 相等 | diff=0 | 7/7 PASS | **0** |

> 说明：§11.6 的「无文件 >500」是最终 DoD；commit 1–4 期间尚未处理的超标文件仍 >500（属计划内后续 commit 范围），
> 每个 commit 只要求「本轮涉及文件不超限、且不新增 >300 的新文件」；commit 5 起全仓为 0。

### 8.4 行为等价的证据

1. **AST 等价（纯搬移的直接证明）**：按 §11.3 的 80 条「旧文件:函数 → 新文件:函数」映射，
   对 `sha256(ast.dump(node))` 逐一比对 → **80/80 完全相等，白名单外差异 0**。
   允许差异仅 §11.4.4 白名单：`cli.main`（3 处全局赋值 → `cli_io.set_context`）、
   `web._handle`（+3 行静态路由）、`web._serve_index`（改读 `static_files`）；
   已移除且预期消失：`web.INDEX_HTML_PATH`、`web._read_index_html`。
2. **golden 基线（§11.4.1）**：在 `/tmp/fasti-refactor/` 用**基线代码**生成冻结 store（22 事件，含
   演进/纠正/自定义类别/关闭类别/删除类别/删除事实/导出 bundle），再采集 27 条命令的基线：
   退出码 + 归一化 stdout/stderr + `--json` 解析对象 + 7 个产物 sha256 + manifest 核心字段 +
   导出 bundle 12 个文件 sha256 + `verify --deep` 结果 + 页面结构指纹。
   每次 commit 用同一脚本重采并与基线比较 → **diff count = 0**（含 `--help`/`--version`/退出码/
   错误封套/画像/历史/回溯/导出/导入/verify）。
   - 归一化仅 5 类：数据目录路径、`/tmp` 前缀、`recorded_at` 时间戳（写入时系统时钟，AS-10）、
     端口号、一次性令牌、PID、导出目录时间戳；**产物 sha256 与导出 bundle 不做归一化，按字节比对**。
   - 夹具细节：`profile history` 的组内排序使用 `recorded_at`（秒精度），同秒 tie-break 会受
     「命令是否跨秒」影响；因此该命令固定**在冻结 store 上采集**（不经过本次运行的写事件），
     保证 golden 可复现。这是测试夹具确定性处理，不涉及产品逻辑差异。
3. **数据产物字节不变**：冻结 store 副本上 `recompute` + `render` 后，7 个产物
   （`events.jsonl`、`profile/current.md`、`profile/history.md`、`dist/AGENTS.md`、`dist/CLAUDE.md`、
   `dist/.cursor/rules/fasti.mdc`、`dist/INSTALL.md`）sha256 与基线**完全一致**；
   导出 bundle 12 个文件 sha256 与 `manifest.payload/counts/files` 同样一致（忽略 `exported_at`）。
4. **页面**：三份静态文件拆分后，页面**结构指纹**（全部 `id`/`name` 排序列表 + title + 可见文本行）
   与基线**完全一致**。`app.js` / `style.css` 与基线内联内容**逐字节一致**——该结论**必须由字节比对证明**
   （§8.6 的教训：R-F1 前仅靠目检曾误报「原样搬出」）：
   - `app.js` 内容（不含末尾 LF）sha256 = `0fb916a6f433fc9e686f941bd3a258582c5c15ddf18ab9e4038ce97443210b95`
     （= QA 报告的基线前缀 `0fb916a6…`）；整文件（含末尾 LF）sha256 = `cfa758e023aae8dbf118b07c0d2b93a2f2f31b8151d0abf1016bd35f544fbc84`
   - `style.css` 内容（不含末尾 LF）sha256 = `d6e0c4069848412112c6052bc282759d3ef7a97c3b6089be1a09ee3a02c582b2`；
     整文件 sha256 = `65fd0617150d3ae0469c056f83c35892fd432e8d7e7a556c0fb69a577ca00fe5`
   - 比对方法：`git show refactor-baseline:workspace/fasti/adapters/web_static/index.html` → 取
     `<script>`…`</script>` / `<style>`…`</style>` 之间的内容（不含末尾 LF）后做 sha256，与拆分文件去末尾 LF 后比对。
   - 唯一允许差异：`index.html` 字节（23758 → 5595）、新增 `<link href="/style.css">` /
     `<script src="/app.js">` 两个同源引用、`/style.css` `/app.js` 路由由 404 → 200（§11.4.4-3/6）。
5. **对外契约不变**：`api-spec.yaml` / `database-schema.sql` **零改动**（`git diff refactor-baseline` 仅 `workspace/`）；
   `fasti.core.api` 的 20 个函数与签名逐一核对不变（如 `change_fact(data_dir, fact_id, change_type,
   content, valid_from=None, note=None)`）；CLI 22 命令与 `--help` 输出不变；16 个 web 端点形状与状态码映射不变。
6. **分层不变量**：`test_layering.py` 递归扫描 `fasti/core/**`（含 `core/services/**`）通过；
   新增 ADR-022 守卫断言 `fasti/adapters/**` 不 import `fasti.core.services.*`、`..core` 相对 import 仅限
   `api/constants/errors`；全包无第三方 import；`web_static/**` 三个文件 `http(s)://` 匹配数均为 0。
7. **跨版本导入**：用重构后代码导入「基线代码导出的 bundle」→ 导入后 `events.jsonl` 与
   bundle 内 `data/events.jsonl` **逐字节相同**；再导出与基线 bundle 除 `exported_at` 外逐字节一致（`verify --deep` 的 `export.roundtrip` 7/7 PASS）。
8. **冒烟**：`python3 run.py web --port 0` 实测 `/`=200（5595 B）、`/style.css`=200（text/css）、
   `/app.js`=200（application/javascript）、无令牌 `/`=403、`PUT /api/profile`=405；
   `fact.add` / `api/render` 正常；`--json web` 与文本模式均正常输出后阻塞服务。

### 8.5 本次未覆盖 / 限制（如实说明）

1. **wheel 构建未在本机执行**：本机 Python 未安装 `setuptools` 且要求离线，`pip wheel` 无法构建；
   已静态核对 `pyproject.toml` 的 `package-data` 含 `web_static/*.css` 与 `web_static/*.js`（及原有 `*.html`），
   并确认三个文件随包存在。**建议 QA 在具备 setuptools 的环境执行 `pip wheel` 并检查 wheel 内容**（§11.4.3-8）。
2. **页面 JS 未在真实浏览器执行自动化测试**：现有守卫为 `node --check` 语法校验（ST-48，有 node 时强制执行）
   + 5 步流程的 HTTP 级实测（§8.6）+ 页面结构指纹与静态文件字节比对；**浏览器内真实交互仍建议 QA 手工走一遍**
   （自动化解析 JS 语法可防再犯 R-F1，但不能替代浏览器 DOM 行为验证）。
3. **未新增真实浏览器/跨设备测试**：AC-13-2 的「另一台设备不可访问」仍为环境限制（见 §5）。

### 8.6 QA 回归修复（R-F1 / R-F2）与根因反思

#### R-F1（阻断，已修复）—— `app.js` 丢失 IIFE 首行

- **现象**：`fasti/adapters/web_static/app.js` 以 `"use strict";` 开头，末行 `})();` 仍在 →
  `node --check` 报 `SyntaxError: Unexpected token '}'`（退出码 1）；网页表单 JS 完全不执行，
  AC-13-1/13-2/13-3/13-4/13-5 共 5 条有 fail 风险（P0 主界面不可用）。
- **根因**：拆分脚本按 0-based 行号取 `<script>` 内联 JS 时写成 `lines[173:511]`（正确应为
  `lines[172:511]`），**off-by-one 丢掉首行** `(function () {`。现有 38 条测试不解析 JS，
  因此语法损坏一路绿灯——这是本轮最关键的教训。
- **修复**：补回 `(function () {` 首行（`f0c4085`）。
- **字节级证据**（对 `git show refactor-baseline:workspace/fasti/adapters/web_static/index.html`
  的内联 `<script>` 内容做 sha256，不含末尾 LF，与 QA 口径一致）：
  - `app.js` 内容 sha256 = `0fb916a6f433fc9e686f941bd3a258582c5c15ddf18ab9e4038ce97443210b95`（**与基线完全相同**）
  - `app.js` 整文件（含末尾 LF）sha256 = `cfa758e023aae8dbf118b07c0d2b93a2f2f31b8151d0abf1016bd35f544fbc84`
  - `node --check fasti/adapters/web_static/app.js` → **exit 0**（修复前 exit 1）
- **同类核对**：`style.css` 与基线内联 `<style>` 内容亦逐字节一致
  （内容 sha256 `d6e0c4069848412112c6052bc282759d3ef7a97c3b6089be1a09ee3a02c582b2`）。

#### R-F2（低，已修复）—— `cli.py` 丢失 `__main__` 守护

- **现象**：`python3 -m fasti.adapters.cli --version` 从输出 `fasti 0.1.0` 变为静默退出 0。
- **根因**：拆分时只搬函数，未搬模块级 `if __name__ == "__main__": sys.exit(main())`。
- **修复**：恢复该守护（`f0c4085`）→ `python3 -m fasti.adapters.cli --version` 输出 `fasti 0.1.0`，exit 0；
  `run.py` / `python3 -m fasti` / 安装后 `fasti` 三个入口行为一致。

#### 新增防呆测试（38 → 40，均为通过项）

1. `test_web_static_js_syntax`（ST-48）：断言 `app.js` 首行 `(function () {`、次行含 `"use strict";`、
   倒数第二行 `})();`；环境有 `node` 时强制 `node --check`（无 node 则 `skipTest` 并标记原因）。
2. `test_web_static_assets_integrity`（ST-49）：三文件存在/非空/恰好一个末尾 LF；`index.html` 仅引用
   同源 `/style.css`、`/app.js`；`web_static/**` 无外部 URL；`app.js`/`style.css` 与基线内联内容
   sha256 **逐字节 pin**（一旦再误改立即红灯）。

#### 修复后 QA 回归项重跑（全绿）

| 回归项 | 命令/方法 | 结果 |
|---|---|---|
| unittest | `python3 -m unittest discover -s tests` | **Ran 40 tests … OK** |
| AST 等价 | `/tmp/fasti-refactor/ast_check.py --strict` | **80/80 相等，白名单外差异 0** |
| golden 比对 | 基线 vs 修复后（27 命令 + 7 产物 + bundle + verify + 页面指纹） | **diff count = 0** |
| 产物 sha256 | 7 个产物（`events.jsonl`、`profile/*.md`、`dist/*`） | **与基线完全一致** |
| 导出 bundle | 12 个文件 sha256 + `manifest.payload/counts/files` | **与基线完全一致**（仅 `exported_at` 忽略） |
| `verify --deep` | 冻结 store 副本 | **7/7 PASS**（含 `export.roundtrip`） |
| 跨版本导入 | 基线 bundle → 修复后代码 | `events.jsonl` 与 bundle payload **逐字节相同**；再导出 12 文件与基线 bundle **全同** |
| 页面 5 步流程 | 本机 `fasti web` + HTTP 实测 | `/`=200；1 新增=`fact-0001`；2 纠正=第 2 次修订；2b 演进=`fact-0002`；3 画像含 C++；4 历史含纠正记录；5 渲染 4 产物；无令牌 `/`=403、`PUT`=405 |
| JS 语法 | `node --check fasti/adapters/web_static/app.js` | **exit 0** |
| 行数预算 | 全量扫描 | **无文件 >500**；`app.js 339`、`cli.py 134` |

#### 方法反思（已固化为流程约束）

1. **「原样搬出」必须用字节比对证明**：上一轮 SELFTEST §8.4.4 / §8.5.2 写「app.js 为原内联内容
   **原样字节**搬出」，**该结论不实**——当时只核对了行数/结构，未做字节比对，因此漏掉 off-by-one。
   本轮已改为 `git show refactor-baseline:…` 提取基线内容 → sha256 → 与拆分文件比对，
   并把哈希写进 SELFTEST 与测试 pin。
2. **拆分脚本禁止用 0-based 行号切片**：改用**标签/标记提取**（找到 `<script>`、`</script>` 行后在二者之间取内容），
   提取后立即做字节回比 + 语法检查，再写盘。
3. **静态资源纳入自动化守卫**：HTML/CSS/JS 不再只靠「文件存在 + 字节数」，而是语法校验（node）
   + 与基线字节 pin + 引用路径一致性；环境缺 node 时测试标记 skip 而不是假装通过。
4. **验证顺序**：先证明字节/语法正确，再跑行为回归（golden/产物/verify）；不得以「命令能跑」代替静态正确性。

---

## 9. C-06 变更（UUIDv7 标识方案 / v1→v2 断代）· 2026-11

> 依据：`briefs/PMS-brief.md` C-06、`artifacts/architecture.md` ADR-023 / ADR-024 / §12、
> `artifacts/api-spec.yaml` 的 `conventions.ids` / `format_versioning`、`database-schema.sql`（schema_version 2）。
> 性质：数据格式断代（事件/导出 `format_version 1→2`、DDL `schema_version 1→2`）；用户可见产品行为不变。

### 9.1 变更摘要

| 维度 | v1（旧） | v2（本次交付） |
|---|---|---|
| `line_id` / `fact_id` | `line-0001` / `fact-0001`（序号 max+1） | **UUIDv7**（RFC 9562，36 字符小写；时间有序） |
| 类别身份 | `category_id=cat-0002` + `key` 双身份 | **语义 `category_key`（唯一身份）**；已删除 key 永久占用；同 key 重建 = 复活 |
| 修订 | `revision_id=rv-<fact序号>-<n>` | **无独立 ID：`(fact_id, seq)` 复合主键** |
| CLI text 显示 | 完整序号 ID | **短 ID（默认前 12 位十六进制 = 完整 48-bit 时间戳）**；`--json`/事件/导出一律完整形式 |
| CLI 入参 | 完整序号 ID | 完整 UUIDv7 **或 4~32 位十六进制唯一前缀**（0 命中 NOT_FOUND_*、多命中歧义报错列候选、非法 VALIDATION_INVALID_ID） |
| 格式 | `format_version: 1` | **`format_version: 2`**；v1 数据/包不兼容（显式拒绝 + 迁移指引）；`contract_version = 1.2.1` |

**不改的**：画像内容与排序、历史语义（演进=新段/纠正=新修订/删除=墓碑）、三宿主产物字节口径、
导入全有或全无、断网可用、仅本机监听、零第三方依赖、`core/api.py` 薄门面与 20 函数签名、
`adapters` 不 import `core.services`（ADR-022）。

### 9.2 实现落点（commit）

| # | commit | 内容 |
|---|---|---|
| 1 | `be10907` | `core/ids.py`：stdlib UUIDv7 生成器（`time.time_ns()//1e6` + `os.urandom`，同毫秒 rand_a 单调 Method 1、溢出等下一毫秒）+ 规范形式/短前缀解析 + 测试接缝；errors 新增 `STORE_FORMAT_UNSUPPORTED`/`VALIDATION_INVALID_ID`；新增 `tests/test_ids_uuid7.py`（11 用例） |
| 2 | `596e36e` | 领域模型/事件/折叠/校验/服务/导出导入/适配层全量迁移到 v2（类别 key 身份 + 复活、UUIDv7、修订复合键、格式门、短前缀解析、CLI 短 ID、网页 category_key）；随包 DDL 升级 schema_version 2；测试重写为 68 用例（含新增 `test_short_ids`/`test_format_gate`/`test_id_persistence_guard`） |
| 3 | `db8df96` | README / SELFTEST 同步（短 ID 用法、v2 格式、迁移指引、证据与契约缺口） |
| 4 | **`cb1e819`** | **架构师裁决：默认短 ID 8 → 12 位**（ADR-023 v1.5 / api-spec v1.2.1）：`SHORT_ID_LEN=12` 单一常量、`SHORT_ID_RE`、`short_id()`、自动类别 key `c-<12位>`、`CONTRACT_VERSION 1.1.0→1.2.1`、`app.js` 显示截断 12；测试同步 12 位口径（纯显示层，无 schema/数据变更） |
| 5 | `8ce065a` | README / SELFTEST 同步 12 位短 ID 口径与实测证据 |
| 6 | `fa14fcf` | QA 回归修复：P0 写路径改为「先内存折叠校验、通过后才落盘」+ 自动类别 key 全量碰撞检查；P1 网页短 ID 去连字符；P2 错误码兜底文案用 FORMAT_VERSION；新增 `test_write_atomicity.py`（68 → 72） |
| 7 | **`17cb6da`** | **用户实测阻断修复**：网页类别对象字段 `item.category_key` → `item.key`（5 处）；新增 `test_web_contract.py`（前端 JS↔api-spec 字段契约，72 → 75） |
| 8 | 本文件 commit | README / SELFTEST 同步（§9.9 前端字段契约修复证据；用例 75） |

### 9.3 自动化测试（75 用例，全绿）

```
$ cd workspace && python3 -m unittest discover -s tests
Ran 75 tests in 2.631s
OK
```

覆盖（新增/重写）：
- `test_ids_uuid7.py`（11）：规范形式/版本与变体位、同毫秒字典序递增、跨毫秒时间序、
  rand_a 12-bit 溢出等待下一毫秒、唯一性、短 ID、完整/大写/无连字符归一化、非法输入、
  唯一前缀/0 命中/>1 命中（候选 ≤10）；**默认短 ID = 12 位**（`SHORT_ID_LEN`、`SHORT_ID_RE`、`c-<12位>`）。
- `test_short_ids.py`（5）：fact 短前缀唯一解析、歧义报 `VALIDATION_AMBIGUOUS_REFERENCE`、
  非法 `VALIDATION_INVALID_ID`、0 命中 `NOT_FOUND_FACT`/`NOT_FOUND_LINE`、line 过滤。
- `test_format_gate.py`（6）：v1 数据目录 ⇒ `STORE_FORMAT_UNSUPPORTED`（退出码 5，消息含「迁移」「§12.5」）；
  verify 同样上报退出码 5；v1 bundle / 未来版本 bundle ⇒ `IMPORT_UNSUPPORTED_FORMAT_VERSION`（退出码 4，
  消息含「UUIDv7 迁移」「当前支持版本 2」）；新 store 事件 `format_version == 2`；导出 manifest == 2。
- `test_web_contract.py`（3）：**前端 `app.js` 的类别/事实对象字段访问必须是 api-spec `Category`/`Fact.properties`
  的子集**（防 `item.category_key` 这类前端字段名漂移；白名单仅 3 条并逐条注明理由；已验证「改回 bug 会红」）。
- `test_write_atomicity.py`（3）：**固定同毫秒时钟/随机源的自动类别 key 撞车 ⇒ `VALIDATION_CATEGORY_KEY_TAKEN`，
  `events.jsonl` sha256 与条数不变、store 仍可读可写**；9 条失败写操作逐一断言 events sha256 不变。
- `test_id_persistence_guard.py`（3）：**短 ID 不得出现在任何持久化/导出内容**（events/profile/dist/导出 bundle，
  以十六进制边界正则判定）；events/entities 中所有 UUID 均为完整 UUIDv7 规范形式；profile/dist 不含任何 ID。
- 既有 40 条按 v2 重写：默认模板 key、规范化 JSONL（首事件 format_version=2、`category_key`）、
  演进产生新 `fact_id` 同 `line_id`、纠正不改 `fact_id` 且 `revision_seq+1`、首次录入时间不被改写、
  类别 key 复活、删除墓碑、导出 concepts 路径 `concepts/<category_key>/<line_id>.md`、导入全有或全无等。

### 9.4 实测验证（实际执行）

```bash
# 1) UUIDv7 形态 + 连续创建 3 条事实：text 显示 12 位短 ID（= 完整 48-bit 时间戳），互不相同
$ python3 run.py fact add --category tech --content "事实 1" --valid-from 2023-01
已录入 fact 01a1c0c8347e（line 01a1c0c8347e · 技术背景 · 生效 2023-01）
$ python3 run.py fact add --category tech --content "事实 2" --valid-from 2024-02
已录入 fact 01a1c0c834a1（line 01a1c0c834a1 · 技术背景 · 生效 2024-02）
$ python3 run.py fact add --category tech --content "事实 3" --valid-from 2025-03
已录入 fact 01a1c0c834c1（line 01a1c0c834c1 · 技术背景 · 生效 2025-03）
$ python3 run.py fact list
短ID | line 短ID | 类别 key | 生效 | 内容 | 变更类型 | 首次录入
01a1c0c8347e | 01a1c0c8347e | tech | 2023-01 | 事实 1 | create | 2026-11-09T22:48:28+08:00
01a1c0c834a1 | 01a1c0c834a1 | tech | 2024-02 | 事实 2 | create | 2026-11-09T22:48:28+08:00
01a1c0c834c1 | 01a1c0c834c1 | tech | 2025-03 | 事实 3 | create | 2026-11-09T22:48:28+08:00
共 3 条（返回 3 条）      # 三条 12 位短 ID 互不相同；不同毫秒创建的 ID 前 12 位必不同
$ python3 run.py --json fact list
JSON fact_id len: 36 | line_id len: 36 | category_key: tech     # --json 为完整 UUIDv7

# 2) 短 ID 入参：fact show <12 位> 逐一唯一命中（text 显示什么就能用什么）
$ python3 run.py fact show 01a1c0c834a1
事实 01a1c0c834a1
类别：tech（技术背景）
命题线：01a1c0c834a1
…
（三条逐一执行均唯一命中；仅同毫秒创建或人为输入 4–11 位更短前缀才会歧义）

# 3) 端到端自检（冻结 v2 store 上）
$ python3 run.py verify --deep
CHECK store.load PASS — 事件日志可解析（13 条事件）
CHECK store.invariants PASS — 领域不变量全部通过
CHECK sql.constraints PASS — 内存 SQLite 执行 database-schema.sql（schema_version 2）约束通过
CHECK derived.determinism PASS — 两次折叠与渲染逐字节一致（profile/host/history 共 1537 B）
CHECK derived.consistency PASS — 三份宿主产物 canonical body 逐字节一致（71 B）
CHECK derived.freshness PASS — 派生状态与事件日志一致
CHECK export.roundtrip PASS — 导出→导入→再导出往返无损（8 个文件逐字节一致）
结论：通过

# 4) 确定性（固定事件流上）
recompute×2: PASS
删除派生后重算: PASS    # profile/current.md 2d73a703…；history.md 762515f1…
render×2: PASS

# 5) v2 导出/导入
导出×2 文件集一致: True | 逐字节不同的文件: 无
manifest.format_version: 2 | exported_at 不同: True
导入后 events.jsonl 与 bundle payload 逐字节相同: True
（空环境导入 → recompute/render → 再导出：与首次导出除 exported_at 外逐字节一致）

# 6) v1 数据目录被拒（退出码 5，含迁移指引）
$ python3 run.py --data-dir <v1-store> status
错误 STORE_FORMAT_UNSUPPORTED：数据目录是旧格式（format_version=1，当前支持 2）：不支持向后兼容读取；
请先备份数据目录，再按迁移指引迁移到 v2（architecture.md §12.5：清空重建，或使用一次性事件改写器）后重试

# 7) v1 bundle 被拒（退出码 4，含 UUIDv7 迁移提示）
$ python3 run.py --data-dir <empty> import <v1-bundle>
错误 IMPORT_UNSUPPORTED_FORMAT_VERSION：不支持的导出格式版本 1（当前支持版本 2）：
v1 导出包需先完成 UUIDv7 迁移（见 architecture.md §12.5：清空重建或一次性事件改写器），再用当前版本重新导出

# 8) 零第三方依赖 / 零外链 / 行数
$ python3 -c "import ast,sys; …"   # tests/test_layering.py：全包 import 仅标准库
$ grep -rnoE "https?://[^\"' )]+" fasti/adapters/web_static/    # 无输出
$ node --check fasti/adapters/web_static/app.js                   # exit 0
$ find fasti tests … | xargs wc -l | tail -1                      # 7339 total（55 文件；无 >500 行）
$ python3 -c "from fasti.core import constants; print(constants.CONTRACT_VERSION)"
1.2.1                                                             # 与 api-spec contract_version 一致
$ curl /api/health                                                # {"contract_version": "1.2.1", …}
```

### 9.5 契约缺口 / 风险（需 Lead / 架构师知悉）

1. **短 ID 位数：已由架构师裁决为 12 位（本 commit 落地）**：
   8 位 = 48-bit 毫秒时间戳的高 32 位，变化周期 2^16 ms ≈ 65.5 s（同窗口必歧义）；
   **12 位 = 完整 48-bit 时间戳 ⇒ 不同毫秒创建的 ID 前缀必不同（日常零歧义）**。
   新口径（ADR-023 v1.5 / api-spec v1.2.1 / §12.7）：`fact show <12位>` 对常规录入必须唯一命中；
   只有**同毫秒创建**或**人为输入 4–11 位更短前缀**才允许歧义，且必须退出码 4 +
   `VALIDATION_AMBIGUOUS_REFERENCE` + 候选列表，**禁止静默取第一个**。实现与测试已按此对齐；
   自动类别 key 同步 `c-<前 12 位>`（如 `c-0190a1b23c4d`，总长 14 ≤ 32）。
2. **格式断代**：用户旧 `~/.fasti`（v1）会被明确拒绝；迁移由 Lead/用户执行（Lead 已备份
   `~/fasti-backup-v1/`），本轮未触碰任何真实用户数据目录，也未提供自动迁移命令（ADR-024 明确
   P0 只保留格式门与迁移说明，不把一次性迁移器放进产品代码）。
3. **UUIDv7 时间序依赖本机时钟**（AS-17）：时钟回拨只影响排序近似，不影响唯一性；`recorded_at`
   仍是审计真相；跨进程/跨设备同毫秒顺序不保证。
4. **未在 Python 3.9 实机执行**（本机 3.13.3；代码未用 3.10+ 语法，未 import `uuid`）。
5. **浏览器内真实 JS 交互**仍靠结构断言 + `node --check` + HTTP 5 步流程覆盖，未做 DOM 级自动化
   （与 §8.5 同口径；v2 的 app.js 已改为使用 `category_key`）。

### 9.6 本次未覆盖项（如实说明）

| 项 | 原因 | 替代验证 |
|---|---|---|
| 用户真实 v1 数据迁移 | Lead 负责（用户数据不在 workspace/写权限内） | 提供格式门 + migration 指引 + v1 拒绝实测；Lead 备份 `~/fasti-backup-v1/` |
| 迁移器（路径 B）端到端演练 | ADR-024 明确 P0 不把一次性迁移器放进产品代码 | §12.5 步骤已核对；v2 导入/导出/verify 全链路实测可支撑迁移后验证 |
| 真实宿主新会话读取（AC-6-1 等） | 环境限制（同 §5） | 产物字节与放置行为不变（v2 产物不含 ID，语义不变） |
| 浏览器 DOM 交互 | 无浏览器自动化能力 | `node --check` + IIFE 结构守卫 + HTTP 5 步流程 + 静态资源完整性守卫 |
| 跨设备不可访问（AC-13-2 后半） | 环境限制（同 §5） | 仅绑 `127.0.0.1`（lsof 实测） |

### 9.7 交付声明

- 全部实现位于 `workspace/`；`artifacts/`（规范/契约/DDL 源）与 `briefs/` 均未修改。
- 本轮**未写入用户 `~/.fasti`**；旧数据拒绝为预期的断代行为，迁移由 Lead 执行。
- **等待 QA 独立验证**：以上为开发侧自测结论。QA 复验按 `architecture.md §12.7`（v1.5 修订）：
  常规录入的 `fact show <12 位>` 应唯一命中；同毫秒/4–11 位短前缀才允许 `VALIDATION_AMBIGUOUS_REFERENCE`
  （退出码 4 + 候选列表，禁止静默取第一个）。

### 9.8 QA 回归修复（P0 写路径原子性 / P1 网页短 ID / P2 兜底文案）· commit `fa14fcf`

#### P0（数据损坏级）写前未校验 + 自动类别 key 未查重 → store 损坏

- **现象（修复前复现）**：固定同毫秒时钟/随机源，连续新增两个无 `--key` 的自定义类别：
  - 第 1 个 key `c-018bcfe56800`；第 2 次异常 `CORRUPT_EVENT_LOG`（应为 `VALIDATION_CATEGORY_KEY_TAKEN`）；
  - `events.jsonl` 字节已变化（坏事件已落盘）→ 后续任何命令均 `CORRUPT_EVENT_LOG`。
- **根因**：所有写路径都是 `store.write_events(...)` **之后**才 `fold.fold_events(...)`；
  折叠失败时坏事件已进文件；自动 key 生成后也未检查是否已被占用（含已删除类别的永久 key）。
- **修复（先校验后落盘）**：
  1. `services/_shared.fold_for_write(events)`：**先在内存折叠 + `validate.check_invariants`**，
     任何 `CoreError` 都在落盘之前抛出；
  2. facts 的 4 条与 categories 的 3 条写路径、以及 `init_store` 全部改为
     「`fold_for_write` 通过 → `store.write_events` 落盘」（共 8 处，逐处核对顺序）；
  3. `add_category` 无 `--key` 时改用 `_generate_auto_category_key(model)`：对**全部已占用 key
     （含已删除）**查重、最多重试 5 次，耗尽抛 `VALIDATION_CATEGORY_KEY_TAKEN`（不写事件）。
- **修复后复现输出（同一固定时钟脚本）**：
  ```
  第 1 个自动 key: c-018bcfe56800
  第 2 次异常: VALIDATION_CATEGORY_KEY_TAKEN | exit = 4
  events 字节是否变化（应为 False）: False
  后续 list_categories: 8 个类别
  后续 status 正常: 9 条事件
  ```
- **回归测试**（`tests/test_write_atomicity.py`，3 用例）：
  1. 同毫秒自动 key 撞车 ⇒ `VALIDATION_CATEGORY_KEY_TAKEN`(rc4)，`events.jsonl` sha256 与事件条数不变，
     无重复 key，store 仍可读且可继续写入；
  2. 已删除类别的 key 永久占用语义不被自动 key 破坏（不误复活）；
  3. 9 条失败写操作（add/change/delete fact、add/set_enabled/delete category 等）逐一断言
     `events.jsonl` sha256 不变，全部失败后 store 仍可正常 `status`/`list_facts`。

#### P1 网页事实下拉短 ID 截取错误（含连字符）

- **现象**：`app.js` 对带连字符的规范形式直接 `slice(0,12)` → 显示 `01a1c0c9-c2f`（11 hex + 连字符）。
- **修复**：`fact.fact_id.replace(/-/g, "").slice(0, 12)`，与 CLI 的 12 位短 ID 一致。
- **证据**：`node -e` 实测 → `修复后: 01a1c0c9c2f0 (len=12) | 修复前会显示: 01a1c0c9-c2f`；
  `node --check fasti/adapters/web_static/app.js` → exit 0；静态资源守卫测试新增断言
  （`replace(/-/g, "").slice(0, 12)` 必须存在，裸 `fact.fact_id.slice(0, 12)` 必须不存在）。

#### P2 错误码兜底文案过时

- **修复**：`errors.DEFAULT_MESSAGES["IMPORT_UNSUPPORTED_FORMAT_VERSION"]` 改用
  `constants.FORMAT_VERSION` 生成，现为「当前支持版本 2：v1 导出包需先完成 UUIDv7 迁移（见 architecture.md §12.5）…」。
- **回归测试**（`test_format_gate`）：断言文案含「当前支持版本 2」「UUIDv7 迁移」「§12.5」，且不含「当前支持版本 1」。

#### 修复后验证

| 项 | 结果 |
|---|---|
| unittest | **Ran 72 tests … OK**（68 + 4：3 条写原子性 + 1 条 P2 兜底文案） |
| `verify --deep` | **7/7 PASS**（结论「通过」） |
| 写路径顺序 | `grep` 确认 8 处写路径均为 `fold_for_write` 在 `store.write_events` 之前（含 init） |
| `node --check` | exit 0；`web_static/**` 零外链 |
| 行数/依赖 | 无文件 >500 行；零第三方依赖不变（`test_layering` 通过） |
| 用户数据 | 全程 `--data-dir` 临时目录，未触碰 `~/.fasti` |

### 9.9 用户实测阻断修复：前端类别字段契约（P0 · commit `17cb6da`）

#### 现象与根因

- **现象（用户实测）**：网页「新增事实」提交时报
  `NOT_FOUND_CATEGORY 类别不存在：'undefined'` —— P0 主界面不可用。
- **根因**：`api-spec.yaml` 的 `Category` 对象身份字段是 **`key`**（无 `category_key`），
  API 实现返回正确；但 `app.js` 有 **5 处**把类别对象写成 `item.category_key`（该对象上不存在此字段）：
  - 99 行 `categoryNames[item.category_key]`（类别名映射全空）
  - **108 行 `option.value = item.category_key`（下拉 value=undefined ← 用户报的 bug 源头）**
  - 156 行 `/api/categories/${item.category_key}/enabled`（启停 URL 错）
  - 168 行 `key !== item.category_key`（迁移候选列表错）
  - 173 行 `/api/categories/${item.category_key}/delete`（删除 URL 错）
- **为什么不归 API**：API 一直是好的；QA 测的是 API 层，bug 在「前端 JS 字段名 ↔ API 契约」之间。

#### 修复

5 处 `item.category_key` → **`item.key`**；事实对象的 `fact.category_key`（190 行）保持正确、未改。
`node --check fasti/adapters/web_static/app.js` → **exit 0**。

#### 防呆测试（新增 `tests/test_web_contract.py`，3 用例；72 → 75）

1. 从 `artifacts/api-spec.yaml` 的 `domain_model.$defs.Category` / `$defs.Fact` **缩进解析 properties**
   （不引入 PyYAML；解析失败/文件缺失会显式 fail / skip，不静默通过）；
2. 正则扫描 `app.js` 中形如 `<obj>.<field>` 的访问（obj ∈ {`item`, `category`, `fact`}）；
3. 断言：类别对象字段 ⊆ `Category.properties`；事实对象字段 ⊆ `Fact.properties`；
4. 白名单**仅 3 条**（逐条给理由）：
   - `category.value`：`form.category` 是 HTMLFormElement 的具名表单控件（DOM 元素），不是 Category 对象；
   - `item.path` / `item.bytes`：渲染产物列表项 `{path, bytes, sha256}`（`/api/render` 的 artifacts），不是 Category 对象；
5. 另加回归锁定：禁止 `item.category_key`、要求 `item.key` 与 `fact.category_key`。

**红检查（证明测试真能抓住这次 bug）**：临时把 `option.value = item.key` 改回 `item.category_key`：
```
FAIL: test_app_js_object_fields_match_contract
  app.js 访问了 api-spec 契约中不存在的字段：['item.category_key']
FAIL: test_category_identity_is_key_in_app_js
  app.js 不得访问 item.category_key（类别对象身份字段是 key，否则下拉 value=undefined）；应改为 item.key
Ran 3 tests … FAILED (failures=2)
```
恢复修复后 → `Ran 3 tests … OK`。

#### 手工验证（实际执行）

```
# 1) GET /api/categories → 7 个类别
类别数: 7 | keys: ['basics','tech','experience','projects','communication','state','goals']
字段检查: item.key 存在且无 item.category_key: True

# 2) 用 app.js 的同款取值逻辑（option.value = item.key）模拟下拉
下拉 option.value 实际取值: ['basics','tech','experience','projects','communication','state','goals']
是否存在 undefined: false

# 3) 用下拉里的真实 key（tech）POST /api/facts
HTTP: 200 | 创建成功: True | fact_id 长度: 36 | category_key: tech
内容: 网页表单实测：主力语言 C++

# 4) 复核
GET /api/facts → facts 数量 1、第一条类别 tech；GET / → HTTP:200
```

#### 修复后验证

| 项 | 结果 |
|---|---|
| unittest | **Ran 75 tests … OK**（72 + 3 条前端字段契约） |
| `verify --deep` | **7/7 PASS** |
| `node --check` | exit 0；页面零外部请求不变 |
| 行数 | 无文件 >500（`test_web_contract.py` 130 行） |
| 用户数据 | 全程 `--data-dir` 临时目录，未触碰 `~/.fasti` |

