# Fasti · P0 参考实现（`workspace/`）

Fasti 是自用型个人画像工具：**唯一真相是追加型事件日志 `events.jsonl`**，当前画像、历史时间线、三份宿主产物都由它**确定性折叠 + 渲染**得到。本目录是按 `artifacts/architecture.md`、`artifacts/api-spec.yaml`、`artifacts/database-schema.sql` 实现的 P0 参考实现。

- 运行时依赖：**零第三方依赖**（Python ≥ 3.9 标准库；ADR-014）。
- 分层：`fasti/core/**` 核心层（UI 无关，不 `import argparse/http.server`、不 `print`）/ `fasti/adapters/**` 薄适配层。
- 主界面：本机 localhost 单页网页表单（`fasti web`，只绑 `127.0.0.1`）；CLI 为次选（脚本/自动化/安装）。

---

## 1. 快速开始（无需安装）

```bash
cd workspace

# 1) 初始化数据目录（默认 ~/.fasti；可用 --data-dir 或 FASTI_HOME 覆盖）
python3 run.py init

# 2) 录入事实（从 2025-01 起为真）
python3 run.py fact add --category tech --content "主力语言：Python" --valid-from 2025-01

# 3) 找到刚录入的 fact（text 输出显示短 ID = UUIDv7 前 12 位 = 完整 48-bit 时间戳；也可 --json 取完整 ID）
python3 run.py fact list
#    短ID | line 短ID | 类别 key | 生效 | 内容 | 变更类型 | 首次录入
#    0190a1b23c4d | 0190a1b43c4d | tech | 2025-01 | 主力语言：Python | create | 2026-…

# 4) 演进：2026-11 起主力语言变为 C++（旧段自动收盘于 2026-10）
python3 run.py fact change 0190a1b23c4d --type evolve --content "主力语言：C++" --valid-from 2026-11

# 5) 纠正：把 2025-01~2026-10 误记的 Python 改成 Java（区间不变，历史保留原内容）
python3 run.py fact change 0190a1b23c4d --type correct --content "主力语言：Java" --note "Python 系误记"

# 6) 查看
python3 run.py profile show        # 当前画像
python3 run.py profile at --at 2026-06   # 时点回溯
python3 run.py profile history     # 历史时间线（演进/纠正/删除/类别变更）

# 7) 重算 + 渲染（写 profile/*.md 与 dist/*宿主产物）
python3 run.py recompute
python3 run.py render

# 8) 自检
python3 run.py verify --deep

# 9) 主界面：本机网页表单（自动打开浏览器；--no-browser 只打印 URL）
python3 run.py web --port 8787
```

也可以安装为命令（可选、离线）：

```bash
pip install --no-deps -e .
fasti --help
```

> 等价入口：`python3 run.py …`、`python3 -m fasti …`、安装后的 `fasti …`。

---

## 2. 环境要求

| 项 | 要求 / 实测 |
|---|---|
| Python | ≥ 3.9；**实测 3.13.3**（macOS 26.5 arm64）。POSIX only（写锁用 `fcntl`，Windows 未适配，见 architecture AS-04） |
| SQLite | 需内置 JSON1（SQLite ≥ 3.38）；**实测 sqlite3 3.54.1**（仅用于内存约束校验，不落地 `.db`） |
| 第三方包 | **无**。不 `pip install` 任何依赖；全程无网络调用 |
| 文件锁 | `flock`（macOS/Linux）；NFS 等不支持 `flock` 的文件系统不保证（ADR-015） |

依赖自检：`python3 -m unittest discover -s tests -v`（**93 个用例**：v3 时间语义（三档输入/auto-bump/自适应显示/`--at` 桶起点）、UUIDv7/短前缀/格式门/类别复活/持久化守卫 + 写路径原子性 + 前端字段契约守卫（fixture 快照，无 artifacts 也可执行） + v2→v3 迁移脚本/强回归 + 分层/确定性/导出导入/CLI/网页/静态资源守卫，含「禁止 import UI 模块 / 禁止 print / 禁止第三方依赖」的分层断言）。

---

## 3. 目录与分层

```
workspace/
├── run.py                          # 免安装入口（等价 fasti）
├── pyproject.toml                  # 仅打包元信息：零依赖、console script = fasti
├── fasti/
│   ├── __main__.py                 # python3 -m fasti
│   ├── core/                       # 核心层（UI 无关；禁 argparse/http.server/print）
│   │   ├── api.py                  # C0 薄门面（re-export；唯一入口，ADR-019）
│   │   ├── services/               # C0s 实现（只允许 api.py 引用，ADR-022）
│   │   │   ├── _shared.py          # 共享 helpers + 内部工具（单向依赖）
│   │   │   ├── facts.py            # add/change/delete/list/show_fact
│   │   │   ├── categories.py       # list/add/enable/delete_category
│   │   │   ├── views.py            # profile / profile_at / history / recompute / render
│   │   │   ├── lifecycle.py        # init / status / export / import / install_hosts
│   │   │   └── verify.py           # verify + 私有检查函数
│   │   ├── category_templates.py   # _load_templates（与 templates/ 同级）
│   │   ├── store.py                # 数据目录/事件读写/原子写/flock 单写锁/备份
│   │   ├── fold.py                 # 纯函数折叠：事件流 → ReadModel
│   │   ├── model.py                # ReadModel（派生字段在这里定型）
│   │   ├── validate.py             # C4 校验门面（re-export 10 个校验函数）
│   │   ├── validate_fields.py      # 字段校验（require_*）
│   │   ├── validate_events.py      # 事件记录结构校验
│   │   ├── validate_invariants.py  # 领域不变量
│   │   ├── validate_sql.py         # 内存 SQLite 声明式约束
│   │   ├── profile.py              # 当前画像 / 时点回溯 / 历史视图 + 确定性文本渲染
│   │   ├── render.py               # 宿主产物与 profile/*.md 写出 + manifest
│   │   ├── export.py               # OKF 形状 bundle 导出
│   │   ├── importer.py             # 全量校验 + 原子替换 + 显式覆盖 + 自动备份
│   │   ├── status.py               # 新鲜度/外部篡改/宿主接入状态
│   │   ├── install.py              # install_hosts（全局位置 / Cursor 降级）
│   │   ├── ids.py clock.py errors.py constants.py
│   │   ├── templates/categories.json        # 7 个默认模板类别（随包只读）
│   │   └── schema/database-schema.sql       # 与 artifacts/database-schema.sql 逐字节相同的副本
│   └── adapters/                   # 界面适配层（只调用 fasti.core.api；ADR-022）
│       ├── cli.py                  # CLI 入口：分发 / main
│       ├── cli_parser.py           # argparse 声明（22 命令）
│       ├── cli_io.py               # 输出上下文 + text/json 呈现 + 退出码错误输出
│       ├── cli_cmds_content.py     # fact / category / profile 命令实现
│       ├── cli_cmds_store.py       # init/status/render/export/import/verify/web/install-host
│       ├── web.py                  # 本机网页表单后端（127.0.0.1 + 令牌 + Origin/Host 校验）
│       ├── static_files.py         # 同源静态资源白名单（3 条只读路由）
│       └── web_static/             # index.html + style.css + app.js（零外部请求）
├── tests/                          # _base.py + 16 个 test_*.py + fixtures/*.json（93 个用例）
└── scripts/                        # 一次性迁移工具（stdlib、离线，不随产品命令暴露）
    └── migrate_v2_to_v3.py         # v2→v3 事件改写器（--dry-run/原子写回/幂等）
```

**为什么 `core/schema/database-schema.sql` 有一份副本**：`verify` 的 `sql.constraints` 检查与导入流水线第 4 步需要把折叠结果载入内存 SQLite 执行同一份 DDL。该文件是从 `artifacts/database-schema.sql` **逐字节复制**的只读副本（schema_version 3，sha256 `d11fbac631657cb375302a9cce6296cde3024bb3421032fe0749420481b02a61`），便于离线运行；`artifacts/` 未被修改。也可用环境变量 `FASTI_SCHEMA_PATH` 指向其他 DDL。

---

## 4. 数据目录

```
<data-dir>/                       # 默认 ~/.fasti
├── events.jsonl                  # 唯一权威数据（追加型事件日志，UTF-8/LF/规范化 JSON）
├── profile/current.md            # 派生：当前画像（含「（未录入）」）
├── profile/history.md            # 派生：历史时间线
├── dist/AGENTS.md                # 派生：DSH 宿主产物
├── dist/CLAUDE.md                # 派生：Claude Code 宿主产物
├── dist/.cursor/rules/fasti.mdc  # 派生：Cursor 规则（含 alwaysApply: true frontmatter）
├── dist/INSTALL.md               # 派生：三宿主接入说明（全局限位 + Cursor 降级）
├── state/manifest.json           # 派生状态（可删除；用于新鲜度/篡改检测）
├── state/backups/                # 覆盖导入前的自动备份
└── exports/                      # export 默认输出目录
```

- 数据目录默认权限 `0700`、文件 `0600`；全部 UTF-8 文本，**不落地任何 `.db`**。
- **写路径原子性**：所有写命令先在内存中折叠并校验领域不变量，**通过后才**原子重写 `events.jsonl`；
  任何校验失败（含自动类别 key 撞车）都不会留下事件，文件字节不变。
- `profile/`、`dist/`、`state/manifest.json` 都是**可丢弃派生结果**：删掉后 `recompute`/`render` 会逐字节还原（AC-2-4）。
- 迁移 = 整目录复制（AC-8-2），或 `export` → 新环境 `import`（AC-12-1）。

---

## 5. 命令速览（22 个）

全局参数：`--data-dir <PATH>`、`--json`、`--quiet`、`--version`、`--help`（可放在命令前或后）。

| 分组 | 命令 |
|---|---|
| 生命周期 | `init`、`status` |
| 事实 | `fact add`、`fact change`（`--type evolve\|correct`，或 `fact evolve` / `fact correct`）、`fact delete`、`fact list`、`fact show` |
| 类别 | `category list`、`category add`、`category enable`、`category disable`、`category delete`（`--handle-facts migrate\|delete`） |
| 画像 | `profile show [--trace]`、`profile at --at YYYY-MM`、`profile history` |
| 派生 | `recompute`、`render [--host all\|agents\|claude\|cursor]` |
| 迁移 | `export [--out DIR]`、`import <bundle> [--overwrite] [--no-backup]` |
| 自检 | `verify [--deep]` |
| 界面/接入 | `web [--port 8787] [--no-browser] [--token T]`、`install-host [--host all\|agents\|claude\|cursor] [--mode symlink\|copy] [--project DIR] [--dry-run]` |

**退出码**（CLI 适配器约定，稳定错误码在核心层）：

| 退出码 | 含义 |
|---|---|
| 0 | 成功 |
| 2 | 用法错误 |
| 3 | 未找到（fact / category / line / 路径） |
| 4 | 领域校验失败（含 `verify` 不通过） |
| 5 | 状态冲突（store 未初始化/已存在、导入目标非空、写锁被占、端口占用） |
| 6 | 数据损坏 |
| 7 | IO 失败 |
| 99 | 未预期内部错误 |

`--json` 成功输出 `{"ok":true,"command":"<id>","data":{…}}`；失败输出 `{"ok":false,"command":"<id>","error":{"code","message","details","location"}}`（失败也走 stdout，退出码非 0）。

---

## 6. 本机网页表单（P0 主界面）

```bash
python3 run.py web --port 8787          # 默认自动打开浏览器
python3 run.py web --port 0 --no-browser # 端口被占用时用 0 让系统分配
```

- 只绑 `127.0.0.1`；每次启动生成一次性会话令牌（URL `?t=` → 请求头 `X-Fasti-Token`），并校验回环 `Host` / `Origin`，防浏览器 CSRF / DNS rebinding。不做用户鉴权（单机单用户，OS 账户即边界）。
- 页面 = `web_static/index.html` + 同源 `style.css` / `app.js`（`static_files.py` 白名单静态路由）；**零外部请求**（无 CDN/远程字体/遥测），断网可用。页面需要一次性令牌，`/style.css` 与 `/app.js` 为只读同源资源。测试对 `app.js` 执行 `node --check` 语法校验、并与基线内联内容做字节 pin（`app.js` 内容 sha256 `0fb916a6…`）。
- 同页完成：新增事实、修订（必选「演进/纠正」）、查看当前画像（核心层渲染的文本原样展示，前端不重排）、查看历史、时点回溯、类别管理、重算/渲染。
- 页面不缓存画像；任何写操作都经核心层的领域校验与单写锁。类别路径参数使用 `category_key`（无 `category_id`）；`fact_id` 入参同样支持唯一短前缀。

---

## 7. 宿主接入

`render` 产出 `dist/` 三份产物；`install-host` 把它们放到宿主位置（默认软链，渲染原地生效）：

```bash
python3 run.py render
python3 run.py install-host --host agents                 # → ~/.dsh/AGENTS.md（全局，一次生效）
python3 run.py install-host --host claude                 # → ~/.claude/CLAUDE.md（全局，一次生效）
python3 run.py install-host --host cursor --project DIR   # → DIR/.cursor/rules/fasti.mdc（降级：每项目一次）
python3 run.py install-host --dry-run                     # 预览动作，不落盘
```

- 目标已存在且不是本工具创建的软链 ⇒ `blocked`，不覆盖用户手写文件。
- Cursor 无文件形式用户级全局规则，属**降级路径**；`dist/INSTALL.md` 与 `status` 会明确标注。
- 生效时机：宿主**新会话**读取；运行中的会话不保证热更新。

---

## 8. 导出 / 导入

```bash
python3 run.py export                    # → <data-dir>/exports/fasti-export-<UTC>
python3 run.py export --out /tmp/bundle
python3 run.py import /tmp/bundle        # 空环境直接还原
python3 run.py import /tmp/bundle --overwrite   # 非空环境必须显式覆盖（默认先自动备份）
```

- bundle = OKF 形状 Markdown（`index.md` / `profile.md` / `log.md` / `concepts/**`）+ 结构化 `data/events.jsonl`（权威、导入唯一来源）+ `data/entities.jsonl`（快照，仅供核对）+ `manifest.json`。
- 导入是**全有或全无**：任何一步校验失败，现有 `events.jsonl`/画像/历史一行不动；坏文件被拒并给出行号定位（如 `data/events.jsonl:9`）。
- 导入后不自动重算/渲染（派生状态归零为「待重算」），按提示执行 `recompute` / `render`。
- 人读 Markdown 不参与导入（人工改 Markdown 不会改事实）。

---

## 9. 标识（C-06）与时间精度（C-07 / v3）及迁移

### 9.1 标识（ADR-023）

| 对象 | 形式 |
|---|---|
| `line_id` / `fact_id` | **UUIDv7**（RFC 9562；36 字符小写规范形式，字典序 = 创建时间序） |
| 类别身份 | 语义 **`category_key`**（无 `category_id`；7 个模板 key 固定，自定义可省略 → 自动 `c-<UUIDv7 前 12 位>`，如 `c-0190a1b23c4d`） |
| 修订 | **无独立 ID**：以 `(fact_id, seq)` 复合定位 |
| CLI text 显示 | **短 ID = 前 12 位十六进制**（= 完整 48-bit 时间戳，如 `0190a1b23c4d`） |
| CLI 入参 | 完整 UUIDv7，或 **4~32 位十六进制唯一前缀**（大小写不敏感、忽略连字符）；默认显示/推荐输入 12 位 |
| `--json` / 事件 / 导出 / 网页 API | **一律完整 UUIDv7 规范形式**（短 ID 绝不进入持久化/导出内容） |

短前缀解析：唯一命中即用；0 命中 → `NOT_FOUND_FACT` / `NOT_FOUND_LINE`（退出码 3）；
多命中 → `VALIDATION_AMBIGUOUS_REFERENCE`（退出码 4，`details.candidates` 列候选，最多 10 个）；
格式非法 → `VALIDATION_INVALID_ID`（退出码 4）。**不静默取第一个**（AS-19）。

> 短 ID 默认 **12 位 = 完整 48-bit 毫秒时间戳**，不同毫秒创建的 fact/line 前缀必不同（日常零歧义）；
> 只有同毫秒创建或人为输入 4–11 位更短前缀时才可能歧义（正确报错列候选）。

### 9.2 生效时间精度（ADR-025 / C-07，秒级）

- **存储/事件/导出**：完整 RFC3339、秒精度、**带本机偏移**：`2026-11-01T00:00:00+08:00`（永不存 `YYYY-MM`，也不用 UTC-Z）。
- **输入三档**（录入负担不变）：
  - `YYYY-MM` → 该月 1 日 00:00:00；
  - `YYYY-MM-DD` → 当日 00:00:00；
  - `YYYY-MM-DDTHH:MM[:SS]` → 原值（秒缺省 00）；
  - 统一附加**写入时本机偏移**；不接受时区后缀/小数秒/不存在日期 ⇒ `VALIDATION_INVALID_VALID_FROM`。
- **同粗粒度 auto-bump**：粗输入（无时分秒）且候选瞬间 ≤ 当前 head 且与 head 同月/同日 ⇒ 自动取**当前本机时刻**
  （若仍 ≤ head 取 head+1s），响应 `auto_bumped: true`，CLI stderr/网页 warnings 明示实际时刻。
  显式时刻、或回溯到更早桶 ⇒ `VALIDATION_VALID_FROM_NOT_AFTER_PREVIOUS`（不静默改写明确意图）。
- **显示自适应**：日 01 且 00:00:00 → `YYYY-MM`；00:00:00 → `YYYY-MM-DD`；否则完整时刻。
  段终：null→「今」；月起点→上一月；日起点→前一日；否则 `display_time(end-1s)`。
  ⇒ **按月录入的历史数据，迁移后显示文本逐字节不变**（如仍是 `Python（2025-01 至 2026-10）`）。
- **区间**：仍 `[valid_from, valid_to)` 左闭右开；`valid_to` 由同 line 下一段推出；按**瞬间**严格递增 ⇒ 无零长区间。
- **回溯**：`fasti profile at --at` 接受同三档；粗粒度 = 该粒度**起点**；输出说明行标注实际查询瞬间。

```bash
python3 run.py fact add --category tech --content "主力语言：C++" --valid-from 2026-11        # 月
python3 run.py fact add --category tech --content "转岗"        --valid-from 2026-11-09      # 日
python3 run.py fact change <短ID> --type evolve --content "主力语言：Java" \
        --valid-from 2026-11-09T23:50:00                                                      # 时刻
python3 run.py profile at --at 2026-06            # 粗粒度 = 2026-06-01T00:00:00+08:00
python3 run.py profile at --at 2026-11-09T23:50   # 可区分同月内多段
```

### 9.3 格式版本与迁移（ADR-024 / ADR-026）

- 事件与导出 `format_version: 3`；读取模型 DDL `schema_version: 3`；`contract_version = 1.3.0`。
- **不向后兼容**：
  - v1/v2 数据目录 ⇒ `STORE_FORMAT_UNSUPPORTED`（退出码 5，消息指向 architecture.md §13.5 与迁移脚本）；
  - v1/v2 导出 bundle ⇒ `IMPORT_UNSUPPORTED_FORMAT_VERSION`（退出码 4）。
- **v2 → v3 一次性迁移**（真实数据带 `recorded_at`/真实顺序；不提供自动降级）：
  1. 停写 → **双备份**：`mv ~/.fasti ~/.fasti.v2-backup` + `fasti export --data-dir <副本>` 留档；
  2. 在副本上演练：`python3 scripts/migrate_v2_to_v3.py --data-dir <副本> --dry-run`（只打印变更表）；
  3. 正式迁移：去掉 `--dry-run`（原子写回；成功后删除 `state/manifest.json`）；
  4. `fasti recompute && fasti render && fasti verify --deep`；
  5. **成功判据**：迁移 + 重算 + 渲染后 `profile/*.md`、`dist/*` 与迁移前 **sha256 逐字节一致**（自适应显示保证）。
  6. 回退：恢复备份目录 + 旧代码 tag（不提供 v3→v2 自动降级）。
- v1 → v2 的历史方法（UUIDv7/类别 key 改造）见 architecture.md §12.5；v1 数据需先按该路径升级，两个断代不能一步跳过。

---

## 10. 验证方式

```bash
# A. 自动化自测（93 个用例；含 v3 时间语义/auto-bump/自适应显示/迁移强回归 + UUIDv7/短前缀/格式门/类别复活/持久化守卫/写路径原子性/前端字段契约 + 分层/确定性/导出导入/CLI/网页/静态资源）
python3 -m unittest discover -s tests -v

# B. 端到端自检（存储不变量 + 内存 SQLite 约束 + 确定性 + 三产物一致 + 导出导入往返）
python3 run.py verify --deep

# C. v2→v3 迁移演练（在备份副本上；真实数据迁移由用户/Lead 执行）
python3 scripts/migrate_v2_to_v3.py --data-dir /tmp/fasti-v2-copy --dry-run

# C. 冒烟（干净临时目录）
T=$(mktemp -d); python3 run.py --data-dir "$T" init
python3 run.py --data-dir "$T" fact add --category tech --content "主力语言：C++" --valid-from 2026-11
python3 run.py --data-dir "$T" recompute && python3 run.py --data-dir "$T" render
python3 run.py --data-dir "$T" verify --deep && echo SMOKE-OK
```

完整自测记录（实际命令与实际输出）见 [`SELFTEST.md`](SELFTEST.md)。

---

## 11. 文件规模预算（ADR-021）

`workspace/` 下所有 `.py` / `.html` / `.css` / `.js` 文件均 **≤500 行**（实测最大 469 行，共 57 个文件 / 8675 行）；
本次拆分新增文件**目标 ≤300 行**，仅两个已在 ADR-021 登记的豁免文件超目标：

| 文件 | 行数 | 状态 |
|---|---|---|
| `fasti/adapters/web.py` | 469 | 存量文件 + 静态路由；ADR-021 目标豁免（<500） |
| `fasti/core/fold.py` / `profile.py` / `store.py` | 377 / 346 / 329 | 存量登记（未超硬上限）；v3 增加时间戳/瞬间校验 |
| `fasti/adapters/web_static/app.js` | 352 | 单个 IIFE 共享闭包状态；ADR-021 目标豁免（<500） |
| 其余全部源码 / 静态资源 | ≤300 | 达标（含 `clock.py 261`、`scripts/migrate_v2_to_v3.py 210`、`export.py`、`services/facts.py`、`ids.py`、`api.py` 门面） |

规模治理的逐文件迁移映射、AST 等价验证、golden 基线方法与逐 commit 证据见
`artifacts/architecture.md` §11 与 [`SELFTEST.md`](SELFTEST.md) §8。

---

## 12. 已知限制（如实说明）

1. **未在真实宿主会话中验证注入效果**（AC-6-1 / AC-6-3 / AC-6-5）：本环境只能验证「产物文件正确 + 放置位置正确 + `install-host` 行为正确」，宿主是否在**新会话**读取需在 DSH / Claude Code / Cursor 实测一次。
2. **未在另一台设备验证「不可访问」**（AC-13-2 后半）：已用 `lsof` 证明监听地址只有 `127.0.0.1`（无 `0.0.0.0`），跨设备实测受环境限制。
3. **4 周维护预算**（AC-9-3）与第三方复述测试（AC-11-4）属人工/时间跨度验证，未在本轮执行；AC-9-1/13-5 的单次操作耗时只做了量级观察（毫秒级）。
4. **Python 3.9 未实机执行**：代码未使用 3.10+ 语法，也未见 3.9 不兼容 API；实测环境为 3.13.3。
5. **并发只做兜底**：第二个写命令会等待 5 秒后报 `CONFLICT_STORE_LOCKED`（已实测），不做合并。
6. **同秒内多次变更**按 revision 序号定序（时间戳为秒精度，符合 AS-10）；跨秒即可按时间区分。
7. 页面是极简表单（非 UI 精修），只实现 `api-spec.yaml` §5 的 16 个端点，不做导出/导入（设计上留给 CLI）。
8. **v1/v2 数据不兼容**：用户旧数据（format_version 1 或 2）会被明确拒绝；v2→v3 用 `scripts/migrate_v2_to_v3.py`（§9.3），v1 需先按 architecture §12.5 升级。本轮未触碰用户 `~/.fasti`（真实数据迁移由 Lead 执行）。
9. **短 ID 位数**：默认 12 位（= 完整 48-bit 时间戳，不同毫秒必不同）；仅同毫秒创建或 4–11 位更短前缀会歧义（正确报错列候选）。极高频批量（同毫秒多个）录入时需加长前缀或用 `--json` 完整 ID。
10. **UUIDv7 时间序依赖本机时钟**（AS-17）：时钟回拨只影响排序近似，唯一性不受影响；`recorded_at` 仍是审计真相。
