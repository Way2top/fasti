# PMS 项目 · 子代理角色组

四条角色，严格按角色契约运行，不扩展职责。
**项目背景**：暂未确定；参考依据为 `docs/reference/` 目录下的资料（`PMS软件设计说明书.md`、`PMS设计评审意见.md`、`Agent-Memory-调研-2026-11.md`）。角色本身与具体项目无关，可在背景确定后直接接入。

---

## 1. 角色清单

| 角色 id | 显示名 | 输入依赖 | 输出路径 | 只读 |
|---|---|---|---|---|
| `pm` | 产品经理 | 用户原始诉求 / 业务方描述；`docs/reference/` 参考材料 | `artifacts/product-spec.md` | 否（仅写该文件） |
| `architect` | 架构师 | `artifacts/product-spec.md` | `artifacts/architecture.md`<br>`artifacts/api-spec.yaml`<br>`artifacts/database-schema.sql` | **是**（对 `workspace/` 与需求文件只读） |
| `developer` | 开发工程师 | `artifacts/api-spec.yaml`<br>`artifacts/database-schema.sql`<br>`artifacts/architecture.md` | `workspace/`（代码）<br>`workspace/SELFTEST.md`<br>`workspace/README.md` | 否（仅写 `workspace/`） |
| `qa` | QA 质量门禁 | `artifacts/product-spec.md`（验收标准）<br>Developer 交付物 | `artifacts/test-report.md` | **是**（对业务代码与 spec 只读） |

### 契约文件

| 角色 | 系统提示词 |
|---|---|
| `pm` | [`agents/contracts/pm.system.md`](contracts/pm.system.md) |
| `architect` | [`agents/contracts/architect.system.md`](contracts/architect.system.md) |
| `developer` | [`agents/contracts/developer.system.md`](contracts/developer.system.md) |
| `qa` | [`agents/contracts/qa.system.md`](contracts/qa.system.md) |

机器可读注册表：[`agents/roles.json`](roles.json)

---

## 2. 产物与目录约定

```
PMS/
├── artifacts/                      # 结构化交接产物（角色间唯一通信信道）
│   ├── product-spec.md             # ← pm
│   ├── architecture.md             # ← architect
│   ├── api-spec.yaml               # ← architect
│   ├── database-schema.sql         # ← architect
│   └── test-report.md              # ← qa
├── workspace/                      # 唯一写代码的地方
│   ├── ...                         # ← developer
│   ├── SELFTEST.md                 # ← developer
│   └── README.md                   # ← developer
├── briefs/                         # Lead 撰写的需求输入（角色只读）
│   └── PMS-brief-v1.md
├── agents/                         # 角色定义（只读）
└── docs/reference/                           # 项目参考材料（只读）
```

**写权限矩阵**（唯一依据，不受口头约定影响）。`briefs/**` 与 `docs/**` 由 Lead 独占写入，四个角色一律只读：

| 路径 | pm | architect | developer | qa |
|---|:--:|:--:|:--:|:--:|
| `artifacts/product-spec.md` | **W** | R | R | R |
| `artifacts/architecture.md` | R | **W** | R | R |
| `artifacts/api-spec.yaml` | R | **W** | R | R |
| `artifacts/database-schema.sql` | R | **W** | R | R |
| `artifacts/test-report.md` | R | R | R | **W** |
| `workspace/**` | R | R | **W** | R |
| `briefs/**`、`docs/**` | R | R | R | R |
| `agents/**`、`.dsh/**` | R | R | R | R |

---

## 3. 协作规则

1. **只通过结构化产物交接**，不依赖聊天上下文传递关键信息。任何口头结论若未落到文件，视为不存在。
2. **写操作集中在 Developer**；架构师与 QA 默认只读。
3. **QA 是强制关卡**：Developer 声称完成不算完成，必须经 QA 独立验证并给出 verdict。
4. **产出前先给写入摘要**：每个角色在写文件前，先输出 `将写入 <路径>（新建/覆盖），内容概要…`，得到确认后再写。
   —— 该规则已写入每份契约的第 7 节工作流程。

---

## 4. 主流程

```
人类 / Team Lead
      │  原始诉求
      ▼
   ┌─────┐   product-spec.md   ┌───────────┐  architecture.md
   │ PM  │ ──────────────────▶ │ Architect │  api-spec.yaml
   └─────┘                     └───────────┘  database-schema.sql
      ▲                              │
      │ 冲突/需求缺失                 │ 契约
      └──────────────────────────────┼──────────────┐
                                     ▼              │
                              ┌───────────┐         │
                              │ Developer │  workspace/ + SELFTEST.md
                              └───────────┘         │
                                     │              │
                                     ▼              │
                              ┌───────────┐         │
                              │    QA     │  test-report.md
                              └───────────┘         │
                                  │  pass → 结束    │
                                  └─ fail ──────────┘  打回 Developer（按 AC 编号修复）
```

**回流路径**（只允许这三条）：

| 触发 | 从 | 到 | 产物 |
|---|---|---|---|
| 需求矛盾 / 验收标准不可测 | QA 或 Architect | PM | 冲突点写回 `product-spec.md` 的 `## 冲突点待决策` |
| 技术信息不足 / 契约缺口 | Architect 或 Developer | 各自上游 | 假设清单 / 阻塞回报 |
| QA verdict = fail | QA | Developer | `test-report.md` 的失败明细（精确到 `AC-x-y`） |

---

## 5. 角色实例化方式

三条路径，任选其一：

### 5.1 作为 DSH 技能加载（推荐，零配置）

四个角色已注册为可发现技能，任意 Agent 在执行前 load 即可获得该角色契约：

| 技能名 | 对应角色 |
|---|---|
| `role-pm` | 产品经理 |
| `role-architect` | 架构师 |
| `role-developer` | 开发工程师 |
| `role-qa` | QA 质量门禁 |

技能根目录：`.dsh/skills/role-*/SKILL.md`（DSH 项目级技能根，rank 100）。
新增/修改后无需重启，下一个 catalog 周期生效。

### 5.2 作为持久队友（Agent Teams）

由 Team Lead 用 `spawn_teammate` 创建，`name` 取角色 id，`prompt` =
「阅读 `agents/contracts/<role>.system.md` 并严格按其执行」+ 本次具体任务。

### 5.3 作为子代理（subagent）

一次性并行任务用 `subagent`，`prompt` 同上，任务结束后角色实例即销毁。
**跨角色的长流程请用 5.2**，因为结构化产物交接需要角色持续存在。

---

## 6. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-11-09 | 初始角色组：pm / architect / developer / qa |
