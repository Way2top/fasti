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

依赖自检：`python3 -m unittest discover -s tests -v`（**68 个用例**：v2 标识(UUIDv7)/短前缀/格式门/类别复活/持久化短 ID 守卫 + 分层/确定性/导出导入/CLI/网页/静态资源守卫，含「禁止 import UI 模块 / 禁止 print / 禁止第三方依赖」的分层断言）。

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
└── tests/                          # _base.py + 13 个 test_*.py（68 个用例）
```

**为什么 `core/schema/database-schema.sql` 有一份副本**：`verify` 的 `sql.constraints` 检查与导入流水线第 4 步需要把折叠结果载入内存 SQLite 执行同一份 DDL。该文件是从 `artifacts/database-schema.sql` **逐字节复制**的只读副本（schema_version 2，sha256 `0f29b8810e822e6ca8d600b274a903b8570992e7dc0fcebd6f68600794e5a049`），便于离线运行；`artifacts/` 未被修改。也可用环境变量 `FASTI_SCHEMA_PATH` 指向其他 DDL。

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

## 9. 标识与格式版本（v2 / C-06）与 v1→v2 迁移

### 9.1 标识（ADR-023）

| 对象 | v2 形式 |
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

> 注意：短 ID 默认 **12 位 = 完整 48-bit 毫秒时间戳**，**不同毫秒创建的 fact/line 前缀必不同**（日常零歧义）。
> 只有**同一毫秒内创建**多个 ID，或**人为输入 4–11 位更短前缀**时才可能歧义；此时会正确返回
> `VALIDATION_AMBIGUOUS_REFERENCE`（退出码 4 + 候选列表，禁止静默取第一个），加长到 13–32 位即可。
> 版本口径：`contract_version = 1.2.1`（ADR-023 v1.5 / api-spec v1.2.1）。

### 9.2 格式版本（ADR-024）

- 事件与导出 `format_version: 2`；读取模型 DDL `schema_version: 2`。
- **不向后兼容**：v2 代码遇到 v1 数据目录 ⇒ `STORE_FORMAT_UNSUPPORTED`（退出码 5，含迁移指引）；
  v1 导出 bundle ⇒ `IMPORT_UNSUPPORTED_FORMAT_VERSION`（退出码 4，含 UUIDv7 迁移提示）。
- 迁移方法（architecture.md §12.5）：
  - **路径 A · 清空重建**（P0 采用）：停写 → 备份/移走旧数据目录（`mv ~/.fasti ~/.fasti.v1-backup`）→
    `fasti init`（写 v2 事件）→ 重新录入 → `recompute` → `render` → `verify --deep`；
  - **路径 B · 一次性事件改写器**（真实用户带数据）：备份/导出留档 → 逐事件重写
    `category_id → category_key`、`migrate_to_category_id → migrate_to_category_key`、
    序号 ID → UUIDv7、`store_initialized.format_version → 2`、`seq` 保持连续 →
    原子写回并删除 `state/manifest.json` → `verify --deep` + 导出/导入往返验证。
- 迁移由 Lead/用户执行（本轮交付未触碰用户 `~/.fasti`；旧数据备份由 Lead 负责）。

---

## 10. 验证方式

```bash
# A. 自动化自测（68 个用例；含 UUIDv7/短前缀/格式门/类别复活/持久化守卫 + 分层/确定性/导出导入/CLI/网页/静态资源）
python3 -m unittest discover -s tests -v

# B. 端到端自检（存储不变量 + 内存 SQLite 约束 + 确定性 + 三产物一致 + 导出导入往返）
python3 run.py verify --deep

# C. 冒烟（干净临时目录）
T=$(mktemp -d); python3 run.py --data-dir "$T" init
python3 run.py --data-dir "$T" fact add --category tech --content "主力语言：C++" --valid-from 2026-11
python3 run.py --data-dir "$T" recompute && python3 run.py --data-dir "$T" render
python3 run.py --data-dir "$T" verify --deep && echo SMOKE-OK
```

完整自测记录（实际命令与实际输出）见 [`SELFTEST.md`](SELFTEST.md)。

---

## 11. 文件规模预算（ADR-021）

`workspace/` 下所有 `.py` / `.html` / `.css` / `.js` 文件均 **≤500 行**（实测最大 467 行，共 55 个文件 / 7339 行）；
本次拆分新增文件**目标 ≤300 行**，仅两个已在 ADR-021 登记的豁免文件超目标：

| 文件 | 行数 | 状态 |
|---|---|---|
| `fasti/adapters/web.py` | 467 | 存量文件 + 静态路由；ADR-021 目标豁免（<500） |
| `fasti/core/fold.py` / `profile.py` / `store.py` | 365 / 336 / 329 | 存量登记（未超硬上限）；v2 增加 UUIDv7/复活校验 |
| `fasti/adapters/web_static/app.js` | 338 | 单个 IIFE 共享闭包状态；ADR-021 目标豁免（<500） |
| 其余全部源码 / 静态资源 | ≤280 | 达标（含 `export.py 272`、`importer.py 230`、`services/facts.py 229`、`ids.py 183`、`api.py` 门面） |

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
8. **v1 数据不兼容**：用户旧 `~/.fasti`（format_version 1）会被明确拒绝，需按 §9.2 迁移（Lead 已备份 `~/fasti-backup-v1/`）。本轮未触碰用户数据目录。
9. **短 ID 位数**：默认 12 位（= 完整 48-bit 时间戳，不同毫秒必不同）；仅同毫秒创建或 4–11 位更短前缀会歧义（正确报错列候选）。极高频批量（同毫秒多个）录入时需加长前缀或用 `--json` 完整 ID。
10. **UUIDv7 时间序依赖本机时钟**（AS-17）：时钟回拨只影响排序近似，唯一性不受影响；`recorded_at` 仍是审计真相。
