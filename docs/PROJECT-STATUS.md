# 项目状态与文档地图

> **这份文档的唯一职责：告诉你现在该信哪份、现在是第几版、完成到哪了。**
> 更新于：2026-11-15

---

## 一、当前状态（一句话）

**P0 已实现并通过 QA；正在做自用价值验证（2026-11-09 起）。下一个功能是自动提取。**

| 维度 | 状态 |
|---|---|
| 需求 | ✅ 定稿（15 用户故事 / 74 条验收标准） |
| 架构 | ✅ 定稿（27 条 ADR） |
| 实现 | ✅ 完成（115 个测试全绿 / `verify --deep` 7/7 / 零第三方依赖） |
| 功能验收 | ⚠️ **74 条 AC：67 pass / 0 fail / 7 untested** |
| 价值验证 | ⏳ 进行中（用户自用 20 天，第 6 天） |
| 公开仓库 | https://github.com/Way2top/fasti |

### 「完成」到底是什么意思

这里有三层，很容易混：

| 说法 | 含义 | 当前 |
|---|---|---|
| **实现完成** | 代码写完了 | ✅ |
| **QA 通过** | 独立验证过，没发现功能缺陷 | ⚠️ 67/74，0 fail |
| **整体 verdict** | QA 的最终判定 | ❌ **fail** ← 不是有 bug |
| **价值验证** | 用了 20 天，确认它真有用 | ⏳ 未知 |

**为什么 verdict 还是 `fail`**：QA 契约规定"关键验收项未验证不得默认通过"。剩下的 7 条是：

| AC | 为什么验不了 |
|---|---|
| AC-6-1 / 6-2 / 6-3 / 6-5 | 需要真实 Claude Code / Cursor 环境（只在 DSH 上验过） |
| AC-7-1 | 同上，多宿主一致 |
| AC-9-3 | 需要真实观察 4 周维护耗时 |
| AC-11-4 | 需要一个没碰过这工具的真人读导出包 |

**所以：`fail` 代表"尚未完成全部验收"，不代表"代码有问题"。**

---

## 二、文档地图（哪份该信）

### 公开仓库里的文档（git 跟踪）

| 文档 | 权威性 | 内容 |
|---|---|---|
| **[../README.md](../README.md)** | ✅ 当前 | 项目概览、快速开始 |
| **[ARCHITECTURE.md](ARCHITECTURE.md)** | ✅ 当前 | **架构总览（先看这份）** |
| **[PROJECT-STATUS.md](PROJECT-STATUS.md)** | ✅ 当前 | 本文档：状态 / 地图 / 版本矩阵 |
| **[存储层次与终局形态.md](存储层次与终局形态.md)** | ✅ 当前 | 六层存储模型的展开、终局路线 |
| **[使用与验证手册.md](使用与验证手册.md)** | ✅ 当前 | 日常操作（可能滞后于功能） |
| **[../workspace/README.md](../workspace/README.md)** | ✅ 当前 | 代码层 API / 命令 / 数据契约 |
| **[../workspace/SELFTEST.md](../workspace/SELFTEST.md)** | 📚 追加式 | 开发自测记录，**最新在文末** |
| **[../AGENTS.md](../AGENTS.md)** | ✅ 当前 | DSH 会话启动须知（不是人类 README） |

> ⚠️ **注意**：根 `AGENTS.md` 是给 AI Agent 的启动指令，不是项目介绍；人类请从 `README.md` 开始。

### 内部文档（`.gitignore` 排除，仅本机）

这些**不在公开仓库里**：

| 文档 | 权威性 | 内容 |
|---|---|---|
| `briefs/PMS-brief.md` | ✅ 当前 | **需求唯一来源**（用户确认的决策） |
| `artifacts/product-spec.md` | ✅ 当前 | 产品规格（US / AC） |
| `artifacts/architecture.md` | ✅ 当前 | **27 条 ADR**（最详细的设计论证） |
| `artifacts/api-spec.yaml` | ✅ 当前 | 接口契约（20 函数 / 22 CLI / 16 端点 / 错误码） |
| `artifacts/database-schema.sql` | ✅ 当前 | 数据模型 DDL |
| `artifacts/test-report.md` | 📚 追加式 | QA 验收报告，**最新在文末** |
| `docs/价值基线.md` | ✅ 当前 | 20 天验证的基线数据 |
| `docs/reference/*` | 📖 **参考，已过时** | 原始设计说明书（P0 前）、评审意见、竞品调研 |

**为什么这些不公开**：含个人背景、内部评审、以及被推翻的早期方案。

---

## 三、版本矩阵（这些数字各是什么意思）

**同一个 "v3" 可能指五个不同的东西。** 对照此表：

| 标识 | 当前值 | 含义 | 权威来源 |
|---|---|---|---|
| **product-spec** | `v4` | 产品规格文档版本（15 US / 74 AC） | `artifacts/product-spec.md` 头部 |
| **architecture** | `v1.7.1` | 架构文档版本（27 条 ADR） | `artifacts/architecture.md` 头部 |
| **contract_version** | `1.4.0` | 接口契约版本 | `fasti/core/constants.py` |
| **format_version** | **`3`** | **数据格式版本**（最要紧的） | `constants.FORMAT_VERSION` |
| **schema_version** | `3` | 数据库 DDL 版本 | `database-schema.sql` 的 `PRAGMA user_version` |
| P0 / P1 / P2 | — | **开发阶段**（P0 已完成，P1 是自动提取） | 本文档 |
| v1 / v2 / v3 格式 | — | **历史数据格式断代**（不向后兼容） | 见下 |

### 数据格式断代史

| 格式 | 关键变更 | 状态 |
|---|---|---|
| `format_version 1` | 序号 ID（`fact-0001`）、月精度 | ❌ 已废弃 |
| `format_version 2` | UUIDv7 标识、类别语义 key | ❌ 已废弃 |
| **`format_version 3`** | **秒级时间戳、三档输入、防重复审计字段** | ✅ **当前** |

当前代码**只接受 format 3**。旧数据会被 `STORE_FORMAT_UNSUPPORTED` 拒绝，需迁移脚本（`workspace/scripts/`）。

---

## 四、10 分钟上手

```bash
cd workspace

# 1. 初始化（默认数据目录 ~/.fasti）
python3 run.py init

# 2. 录一条事实
python3 run.py fact add --category tech --content "主力语言：C++" --valid-from 2026-11

# 3. 生成画像 + 渲染
python3 run.py render

# 4. 查看
python3 run.py profile show

# 5. 图形界面（推荐）
python3 run.py web
```

接给 AI Agent：

```bash
python3 run.py install-host    # 放到 ~/.dsh/AGENTS.md 等位置
```

验证：

```bash
python3 -m unittest discover -s tests    # 115 个测试
python3 run.py verify --deep             # 数据完整性（7 项）
```

---

## 五、已知遗留（不阻塞自用）

| # | 项 | 计划 |
|---|---|---|
| 1 | **自动提取**（从对话自动生成候选事实） | **正在进行（P1）** |
| 2 | 合并两条线（用户误用「新增」导致同主题裂成两条） | P1 |
| 3 | 语义检索 | P3 |
| 4 | Claude Code / Cursor 接入未验证 | 环境不具备 |
| 5 | 相似检测边界：单个实体、纯中文改写、极短内容可能漏报 | 靠"录入时展示已有事实"兜底 |

---

## 六、如果你只 clone 了公开仓库

公开仓库**缺少**以下内容（在 `.gitignore` 中）：

- `briefs/` — 需求来源与用户决策
- `artifacts/` — 27 条 ADR、接口契约、DDL、QA 报告
- `docs/reference/` — 原始设计说明书、评审意见、竞品调研

**这意味着**：`what` / `how` 能看懂大半，`why`（为什么这样设计）和当前状态只能看懂小半。

**替代方案**：
- 设计理由看 [ARCHITECTURE.md](ARCHITECTURE.md)（已把核心决策浓缩进来）
- 数据契约看 [../workspace/README.md](../workspace/README.md)
- 当前状态看这份文档
