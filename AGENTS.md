# PMS 项目 · 新会话启动须知

> 本文件由 DSH 自动加载（`AGENTS.md` / `CLAUDE.md` 均在默认候选列表中）。
> 详细规则见 `agents/README.md`，此处只放**新会话必须知道的事**。

## 1. 这个项目是什么

**Fasti**：自用型个人记忆系统（原名 PMS，已弃用）。目标不是"记住更多事实"，而是**持续演进的个人画像**。
唯一需求输入：`briefs/PMS-brief.md`（v3，**内部文档，不随公开仓库发布**）。`docs/reference/` 下资料仅作参考，**不得作为需求来源**。

**终局形态与存储分层**：`docs/存储层次与终局形态.md`（方向性文档，非规格；P1/P2 规格设计时必读）。一句话：**Markdown 是出口不是仓库**，唯一真相是 `events.jsonl`。

## 2. 四条角色（严格按契约，不扩展职责）

| id | 角色 | 只写 | 契约 |
|---|---|---|---|
| `pm` | 产品经理 | `artifacts/product-spec.md` | `agents/contracts/pm.system.md` |
| `architect` | 架构师 | `artifacts/architecture.md`、`api-spec.yaml`、`database-schema.sql` | `agents/contracts/architect.system.md` |
| `developer` | 开发工程师 | `workspace/` | `agents/contracts/developer.system.md` |
| `qa` | QA 质量门禁 | `artifacts/test-report.md` | `agents/contracts/qa.system.md` |

## 3. 角色实例不跨会话保存，需要时重建

角色**定义**在磁盘上（`agents/contracts/`、`.dsh/skills/role-*`），**实例**（Team 队友）绑定单个会话。
新会话需要时，由 Lead 用 `spawn_teammate` 重建四条，`context: fresh`，prompt 统一为：

```
你是本项目的「<显示名>」（角色 id: <id>）。
第一步：完整读取 `agents/contracts/<id>.system.md`，那是你的完整系统提示词，严格遵守，不得扩展职责。
同时读取 `agents/README.md` 了解协作规则与写权限矩阵。
本轮仅确认就绪，不要创建或修改任何文件。
```

四条都重建后，按 `agents/README.md` §4 的主流程推进：PM → Architect → Developer → QA（强制关卡）。

## 4. 铁律（违反即返工）

1. **只通过结构化产物交接**，不依赖聊天上下文传递关键信息。
2. **一个产物只有一个写者**：看 `agents/README.md` 的写权限矩阵。`briefs/**` 由 Lead 独占写入。
3. **QA 是强制关卡**：Developer 声明完成不算完成，必须经 QA 独立验证并给出 `pass`/`fail`。
4. **写入前先给摘要**：每个角色写文件前先输出「将写入 <路径>，内容概要…」。
5. **PM 不做技术选型**，技术疑问标 `【待架构师确认】`。
6. **并发期间禁止 `git add -A`**：Lead 在队友活跃时只能提交自己写的路径（`git add <具体文件>`）。曾发生一次事故：`8d6eef2` 标称"架构 v1.3"，实际混入了 QA 的 `test-report.md` 与 Developer 的 `README/SELFTEST`，导致历史不可追溯。

## 5. 当前进度

| 阶段 | 状态 | 产出 |
|---|---|---|
| 需求 | ✅ 已交付 v3 | `artifacts/product-spec.md`（13 用户故事 / 59 AC） |
| 架构 | ✅ 已交付 v3 增量 | `architecture.md`（20 ADR）/ `api-spec.yaml`（20 核心函数 + 22 CLI + 16 web 端点）/ `database-schema.sql`（5 表，schema_version 1） |
| 实现 | ✅ 已完成 | `workspace/`：50 个源文件 / 6400 行 / 零第三方依赖 / 40 测试全绿；文件规模治理完成（无 >500 行） |
| 验收 | ⚠️ **fail（仅差真人验证）** | `artifacts/test-report.md`：59 AC = **52 pass / 0 fail / 7 untested**；重构回归通过（白名单外差异 0）；**5 条宿主会话类 AC 为唯一阻塞** |

**项目名已定：Fasti**（`fasti` / `~/.fasti` / Apache-2.0）。
**版本控制**：已 `git init`，`.git` 所在目录即项目根（技能发现依赖它）。

**当前唯一阻塞**：5 条 AC（6-1/6-2/6-3/6-5/7-1）需在真实宿主开新会话验证——只有用户能做。
已知集成缺口：用户 `~/.claude/CLAUDE.md` 已存在真实内容，`install-host` 会 `blocked` 不覆盖（正确行为），需手工 `@import` 或合并。
**P0 交付形态**：核心层 + 本机网页表单（主）+ CLI（次）；接入走用户级全局位置（`~/.dsh/AGENTS.md`、`~/.claude/CLAUDE.md`）。

## 6. 已锁定决策（不要重新讨论）

- 定位：自用工具，单用户单机，不做产品化
- 重心：持续演进的个人画像
- 维护预算：每天 30 分钟
- 首版宿主：多格式（`AGENTS.md` / `CLAUDE.md` / `.cursor/rules`），一份源渲染多格式
- 云端整层不做：无同步、无云端检索、无加密备份服务
- **终局形态：开源通用架构（规范+参考实现）；P0 仍按自用做，但数据模型与导出格式按开源标准设计**
- v2 新增硬约束：画像类别可扩展（不写死 7 类）、导出/导入纳入本迭代、命名与 License 开发前定
