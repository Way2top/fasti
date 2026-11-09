-- =============================================================================
-- fasti · 数据模型 DDL（SQLite 3 · schema_version 3）
-- 对应 product-spec v3 + brief C-06/C-07 / architecture.md v1.6（ADR-002 / ADR-003 /
-- ADR-009 / ADR-023 / ADR-024 / ADR-025 / ADR-026）/ api-spec.yaml v1.3
-- -----------------------------------------------------------------------------
-- 【v6 变更说明（2026-11-09，brief C-07：生效时间精度升级为秒级）】
--   schema_version 2 → 3；事件格式 format_version 2 → 3。变更只涉及时间精度：
--   1) fact.valid_from：YYYY-MM → 完整 RFC3339 时间戳（带本机偏移、秒精度）；
--      输入仍支持三档（月/日/时刻），粗输入映射粒度起点（由应用层归一，DB 只存完整形式）；
--   2) 派生列 valid_to_month → valid_to：完整 RFC3339（左闭右开终点，由同 line 下一段推出）；
--   3) 区间仍左闭右开；写入校验保证同 line 按瞬间严格递增 ⇒ 不存在零长区间；
--   4) 其它表/列/事件类型/导出结构不变（fact_revision / category / event 表无变化）。
--   数据迁移：v2 数据目录/导出包不向后兼容读取（见文件末尾「迁移/回滚说明」与
--   architecture.md §13.5）；用户现有 v2 数据用一次性事件改写器升级（月值补 -01T00:00:00+偏移）。
-- -----------------------------------------------------------------------------
-- 【v4 变更说明（2026-11-09，brief C-06：ID 方案改用 UUIDv7）】
--   schema_version 1 → 2；事件格式 format_version 1 → 2。变更全部是标识方案：
--   1) fact / fact_line 主键改为 **UUIDv7**（RFC 9562，时间有序、跨设备无冲突）；
--   2) category 取消 category_id，主键改为**语义 key**（模板类别固定 key，跨设备一致）；
--      已删除类别保留 key（PK 唯一），同 key 重新新增按「复活」处理（读模型重置、历史事件保留）；
--   3) fact_revision 取消 revision_id，主键改为 **(fact_id, seq) 复合键**（修订是 fact 的从属对象）；
--   4) 事件 payload 字段随之改名：category_id → category_key、
--      migrate_to_category_id → migrate_to_category_key；事件格式版本 = 2。
--   数据迁移：v1 数据目录/导出包**不向后兼容读取**（见文件末尾「迁移/回滚说明」与
--   architecture.md §12）；P0 现有数据量接受清空重建。模式定义无表新增、无业务列删除，
--   仅主键形态与标识列变化。
-- -----------------------------------------------------------------------------
-- 【这个 DDL 的用途】
-- 1) 它是本工具**唯一权威的关系模型定义**（终局可收敛为 L0 规范的起点）。
-- 2) 也是运行时**约束校验引擎**：核心层把 events.jsonl 折叠成读取模型后，
--    载入**内存** SQLite（:memory:）执行本文件，用 PK/FK/UNIQUE/CHECK 做声明式
--    校验（validate 的 sql.constraints 检查项；导入流水线第 4 步）。
-- 3) 本工具**不落地任何 .db 文件**：数据真相是 events.jsonl（人可读，AC-8-3），
--    读取模型每次由折叠重建（AC-2-4）。
--
-- 【两种表】
-- A. 持久化表：event —— events.jsonl 每行就是一个 event 行（JSON 序列化）。
-- B. 派生读取模型：category / fact_line / fact / fact_revision
--    —— 由事件折叠（fold）得到，永不单独持久化，删除后重算逐字节一致。
--
-- 【执行方式】
--   sqlite3 :memory: < database-schema.sql
--   或 Python：conn = sqlite3.connect(":memory:"); conn.executescript(open(...).read())
--   注意：每个连接都必须先 `PRAGMA foreign_keys = ON;`（本文件已包含，但
--   SQLite 的 PRAGMA 是连接级设置，重建连接时需重新执行）。
--
-- 【注释约定】SQLite 没有 COMMENT 语法，本文件用紧邻的 `--` 注释承担字段说明；
--   这些注释是契约的一部分，修改字段必须同步修改注释。
-- =============================================================================

PRAGMA foreign_keys = ON;
PRAGMA user_version = 3;     -- schema_version：结构变更时 +1（v3 = 秒级生效时间）

-- =============================================================================
-- A. 持久化表：event（对应 <data-dir>/events.jsonl，每行一个 JSON 对象）
-- =============================================================================
-- 唯一权威数据。只追加、永不修改、永不物理删除（历史不可覆盖：US-3/US-4）。
-- 字段与 api-spec.yaml `file_formats.events_jsonl` 一一对应：
--   events.jsonl 行 {"seq":N,"at":"...","type":"...","data":{...}}
--   → (event_seq, occurred_at, event_type, payload)
-- payload 是 event.data 的规范化 JSON 文本（键排序、紧凑分隔符、非 ASCII 不转义）；
-- payload 内 ID 字段为 UUIDv7 规范形式或类别 key（见 api-spec conventions.ids）。
CREATE TABLE IF NOT EXISTS event (
  event_seq      INTEGER PRIMARY KEY    -- 事件序号：从 1 开始**连续**递增（断号/重号 ⇒ CORRUPT_EVENT_SEQUENCE）
                                        -- 同时是事件日志的全局顺序与去重依据（事件本身没有 UUID）
  , event_type   TEXT    NOT NULL       -- 事件类型（枚举）；每类事件的 data 结构见 api-spec file_formats.event_types
                                        -- 枚举取值：store_initialized / category_added / category_enabled_set /
                                        --          category_deleted / fact_created / fact_evolved /
                                        --          fact_corrected / fact_deleted
  , occurred_at  TEXT    NOT NULL       -- 事件发生时间：RFC3339 带时区偏移、秒精度（例 2026-11-09T13:30:00+08:00）
                                        -- 用途：审计、history 倒序、AC-7-4「按时间可区分」
  , payload      TEXT    NOT NULL       -- event.data 的规范化 JSON 文本；必须可被 JSON 解析（CHECK json_valid）
  , CHECK (event_seq >= 1)
  , CHECK (event_type IN (
      'store_initialized', 'category_added', 'category_enabled_set', 'category_deleted',
      'fact_created', 'fact_evolved', 'fact_corrected', 'fact_deleted'))
  , CHECK (json_valid(payload))         -- 依赖 SQLite JSON1（SQLite >= 3.38 内置；更早版本需启用 JSON1 扩展）
  , CHECK (occurred_at GLOB
      '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9][+-][0-9][0-9]:[0-9][0-9]')
);
-- event 表说明：不建 (event_type, occurred_at) 组合索引，因为 P0 量级（≤数千行）
-- 全表扫描即可；下面两个单列索引各自的意图见「索引」段。

-- =============================================================================
-- B1. 派生表：category（画像类别；C-01：类别是数据，不是代码枚举）
-- =============================================================================
-- 由 category_added / category_enabled_set / category_deleted 事件折叠得到。
-- 7 个默认模板在 init 时作为 7 条 category_added 写入（templates/categories.json），
-- 之后与自定义类别完全同权（AC-10-1~AC-10-6）。
-- 【v4】主键 = 语义 key：模板类别在两台设备上 init 得到同一个身份；不再有 category_id。
CREATE TABLE IF NOT EXISTS category (
  category_key          TEXT PRIMARY KEY   -- 主键：语义 key（稳定身份，如 tech / basics / family）
                                           -- 正则 ^[a-z][a-z0-9_-]{0,31}$；**永久唯一**（已删除类别保留 key）
                                           -- 导出概念路径 concepts/<category_key>/<line_id>.md 使用它
  , display_name        TEXT NOT NULL      -- 展示名：1..40 字符；未删除类别内唯一（见 ux_category_name_alive）
                                           -- 中文默认模板名如「技术背景」「沟通偏好」
  , origin              TEXT NOT NULL      -- 类别来源：template=7 个默认模板之一；custom=用户自定义
                                           -- 枚举取值：template / custom
  , template_order      INTEGER            -- 模板顺序：origin=template 时必填且 1..7 内唯一；custom 必须为 NULL
                                           -- 画像类别排序依据（AC-10-5）：模板按此升序，自定义按 created_at 追加在后
  , enabled             INTEGER NOT NULL   -- 是否启用：1=启用、0=关闭（只启用子集）。
                                           -- 关闭后画像/宿主产物不展示，但事实保留且可查询（AC-10-1）
  , created_at          TEXT NOT NULL      -- 创建时间：RFC3339 带偏移；自定义类别排序第二键（AC-10-5）
  , deleted_at          TEXT               -- 软删除时间：NULL=未删除。类别删除不物理删除（历史可查，AC-10-3）
  , deleted_handling    TEXT               -- 删除时对存量事实的处置方式（删除时必须显式选择）
                                           -- 枚举取值：migrate=迁移到其他类别 / delete=一并删除（均保留历史）
  , migrate_to_category_key TEXT           -- 迁移目标类别 key：deleted_handling='migrate' 时必填
                                           -- 外键：category(category_key)，ON DELETE RESTRICT（目标类别不得被物理删）
  , CHECK (length(category_key) BETWEEN 1 AND 32
        AND category_key GLOB '[a-z]*'
        AND category_key NOT GLOB '*[^a-z0-9_-]*')
  , CHECK (origin IN ('template', 'custom'))
  , CHECK (enabled IN (0, 1))
  , CHECK (deleted_handling IS NULL OR deleted_handling IN ('migrate', 'delete'))
  , CHECK ((origin = 'template' AND template_order IS NOT NULL AND template_order BETWEEN 1 AND 7)
        OR (origin = 'custom'   AND template_order IS NULL))
  , CHECK (deleted_at IS NULL OR deleted_handling IS NOT NULL)             -- 删除必须带处置方式
  , CHECK (deleted_at IS NOT NULL OR deleted_handling IS NULL)             -- 未删除不得带处置方式
  , CHECK ((deleted_handling = 'migrate' AND migrate_to_category_key IS NOT NULL)
        OR (deleted_handling = 'delete'  AND migrate_to_category_key IS NULL)
        OR (deleted_handling IS NULL     AND migrate_to_category_key IS NULL))
  , CHECK (migrate_to_category_key IS NULL OR migrate_to_category_key <> category_key)   -- 不得迁移到自己
  , CHECK (created_at GLOB
      '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9][+-][0-9][0-9]:[0-9][0-9]')
  , FOREIGN KEY (migrate_to_category_key) REFERENCES category(category_key) ON DELETE RESTRICT
);
-- 外键级联行为说明：迁移目标使用 RESTRICT（而不是 SET NULL/CASCADE）——
-- 类别删除是软删除，目标类别永远保留行；RESTRICT 用于在物理误删（不应发生的路径）时尽快报错。
-- 同 key 重新新增（已删除后重建）由 fold 处理为「复活」：重置该行的 created_at / enabled /
-- 删除字段，历史事件全部保留（事件日志仍是完整历史）；因此本表**永远只有一行/ key**。

-- =============================================================================
-- B2. 派生表：fact_line（命题线 = 一条可持续演进的属性线，如「主力语言」）
-- =============================================================================
-- 演进（US-3）的容器：同一 line 下可以有多个先后事实段（fact）。
-- 类别迁移（category_deleted handling=migrate）会改写本表的 category_key；
-- 历史回溯按「当前类别归属」展示（architecture.md §2.3）。
CREATE TABLE IF NOT EXISTS fact_line (
  line_id      TEXT PRIMARY KEY    -- 主键：UUIDv7 规范形式（36 字符小写，如 0190a1b2-3c4d-7e5f-8a9b-0c1d2e3f4a5b）
                                   -- 时间有序：字典序 = 字节序 = 创建时间序（固定宽度十六进制）
                                   -- 跨设备/跨进程无需协调（RFC 9562；零第三方依赖实现）
                                   -- CHECK 校验：36 长度、4 个连字符固定位置、版本位=7、变体位∈{8,9,a,b}、仅小写十六进制
  , category_key TEXT NOT NULL     -- 所属类别：外键 category(category_key)，ON DELETE RESTRICT
                                   -- 意图：类别删除必须先处置存量事实（AC-10-3 硬要求），故禁止级联物理删除
  , created_at  TEXT NOT NULL      -- 创建时间：RFC3339 带偏移；审计用（排序以 line_id 为准，见 api-spec ordering）
  , CHECK (length(line_id) = 36
        AND substr(line_id, 9, 1) = '-' AND substr(line_id, 14, 1) = '-'
        AND substr(line_id, 19, 1) = '-' AND substr(line_id, 24, 1) = '-'
        AND substr(line_id, 15, 1) = '7'                     -- UUIDv7 版本位
        AND substr(line_id, 20, 1) IN ('8', '9', 'a', 'b')   -- RFC 9562 变体位（10xx）
        AND line_id NOT GLOB '*[^0-9a-f-]*'                  -- 仅小写十六进制与连字符
        AND line_id NOT GLOB '*-*-*-*-*-*')                   -- 不允许第 5 个连字符
  , CHECK (created_at GLOB
      '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9][+-][0-9][0-9]:[0-9][0-9]')
  , FOREIGN KEY (category_key) REFERENCES category(category_key) ON DELETE RESTRICT
);

-- =============================================================================
-- B3. 派生表：fact（事实段 = 一条命题线在一段时间内的断言）
-- =============================================================================
-- 演进 = 同一 line 追加一个新 fact 段（change_type='evolve'）；
-- 纠正 = 对已有 fact 段追加 fact_revision（本表区间/内容不变）。
-- 删除 = 打墓碑 deleted_at（历史保留，AC-1-4）。
CREATE TABLE IF NOT EXISTS fact (
  fact_id          TEXT PRIMARY KEY   -- 主键：UUIDv7 规范形式（同 fact_line.line_id 的格式与含义）
                                      -- 前端展示用短 ID（前 12 位十六进制），持久化/导出用完整形式
  , line_id        TEXT NOT NULL      -- 所属命题线：外键 fact_line(line_id)，ON DELETE RESTRICT
  , valid_from     TEXT NOT NULL      -- 生效时间（秒级完整时间戳，RFC3339 带本机偏移，如 2026-11-01T00:00:00+08:00）：
                                      -- 从何时起为真（AC-1-2 必填项）。输入三档（月/日/时刻）由应用层归一（C-07 / ADR-025）
                                      -- 区间语义：左闭右开 [valid_from, valid_to)（architecture.md §2.3）
  , valid_to       TEXT               -- 段终（派生列，不独立写入！）：由同 line 中生效瞬间更大的
                                      -- 最小「未删除段」推出；NULL=至今。人读显示用自适应自适应（月/日/秒）；
                                      -- 派生列不参与事件、不参与导出原始数据（导出快照里会带上便于核对）
  , change_type    TEXT NOT NULL      -- 该事实段的产生方式，枚举：create=首次录入 / evolve=由演进产生
                                      -- 注意：纠正不产生新事实段，纠正记录在 fact_revision.revision_type
  , previous_fact_id TEXT             -- 演进来源：change_type='evolve' 时指向被演进的上一段；'create' 时必须为 NULL
                                      -- 自引用外键 fact(fact_id)，ON DELETE RESTRICT（链完整性；物理删不允许）
  , source         TEXT NOT NULL DEFAULT 'user_entry'  -- 来源：P0 唯一取值 user_entry（用户录入）
                                      -- 枚举：user_entry（本迭代）；未来扩展来源需 format_version 升级，不得静默新增
  , recorded_at    TEXT NOT NULL      -- 首次录入时间：纠正**不得改写**（AC-1-3「第一次录入时间不被改写」）
  , deleted_at     TEXT               -- 删除墓碑时间：NULL=未删除。删除后不参与当前画像/回溯，但历史可见（AC-1-4）
  , CHECK (change_type IN ('create', 'evolve'))
  , CHECK (source IN ('user_entry'))
  , CHECK (length(fact_id) = 36
        AND substr(fact_id, 9, 1) = '-' AND substr(fact_id, 14, 1) = '-'
        AND substr(fact_id, 19, 1) = '-' AND substr(fact_id, 24, 1) = '-'
        AND substr(fact_id, 15, 1) = '7'
        AND substr(fact_id, 20, 1) IN ('8', '9', 'a', 'b')
        AND fact_id NOT GLOB '*[^0-9a-f-]*'
        AND fact_id NOT GLOB '*-*-*-*-*-*')
  , CHECK (previous_fact_id IS NULL OR (length(previous_fact_id) = 36
        AND substr(previous_fact_id, 9, 1) = '-' AND substr(previous_fact_id, 14, 1) = '-'
        AND substr(previous_fact_id, 19, 1) = '-' AND substr(previous_fact_id, 24, 1) = '-'
        AND substr(previous_fact_id, 15, 1) = '7'
        AND substr(previous_fact_id, 20, 1) IN ('8', '9', 'a', 'b')
        AND previous_fact_id NOT GLOB '*[^0-9a-f-]*'
        AND previous_fact_id NOT GLOB '*-*-*-*-*-*'))
  , CHECK (valid_from GLOB
      '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9][+-][0-9][0-9]:[0-9][0-9]')
  , CHECK (substr(valid_from, 6, 2) BETWEEN '01' AND '12'    -- 月份
        AND substr(valid_from, 9, 2) BETWEEN '01' AND '31'   -- 日
        AND substr(valid_from, 12, 2) BETWEEN '00' AND '23'  -- 时
        AND substr(valid_from, 15, 2) BETWEEN '00' AND '59'  -- 分
        AND substr(valid_from, 18, 2) BETWEEN '00' AND '59') -- 秒
  , CHECK (valid_to IS NULL OR (valid_to GLOB
      '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9][+-][0-9][0-9]:[0-9][0-9]'
        AND substr(valid_to, 6, 2) BETWEEN '01' AND '12'
        AND substr(valid_to, 9, 2) BETWEEN '01' AND '31'
        AND substr(valid_to, 12, 2) BETWEEN '00' AND '23'
        AND substr(valid_to, 15, 2) BETWEEN '00' AND '59'
        AND substr(valid_to, 18, 2) BETWEEN '00' AND '59'))
      -- 注意：valid_to > valid_from 只做格式/取值 CHECK；同一瞬间先后由应用层按偏移换算后校验
      -- （SQLite 字符串比较在跨偏移时不可靠）。不存在零长区间由写入严格递增 + validate_invariants 保证。
  , CHECK ((change_type = 'create' AND previous_fact_id IS NULL)
        OR (change_type = 'evolve' AND previous_fact_id IS NOT NULL))
  , CHECK (recorded_at GLOB
      '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9][+-][0-9][0-9]:[0-9][0-9]')
  , UNIQUE (line_id, valid_from)      -- 同一命题线同一生效瞬间只允许一个事实段：区间不相交的结构性保证
  , FOREIGN KEY (line_id) REFERENCES fact_line(line_id) ON DELETE RESTRICT
  , FOREIGN KEY (previous_fact_id) REFERENCES fact(fact_id) ON DELETE RESTRICT
);
-- 「同 line 内区间不重叠」的完整保证分两层：
--   结构层：UNIQUE(line_id, valid_from) + 演进只允许在 head 上追加且新年月更晚（api-spec fold_rules）
--   校验层：verify 的重叠检查 SQL（见文件末尾「不变量校验查询」）
-- 为什么不在库里存 content：内容随纠正变化（revision 历史），content 存 fact 上会导致「纠正要改 fact 行」，
-- 破坏「历史不可覆盖」；正确做法是 fact_revision 保存全量内容版本。

-- =============================================================================
-- B4. 派生表：fact_revision（事实段内容的不可变版本；纠正 = 追加修订）
-- =============================================================================
-- 第 1 版 = 首次录入（revision_type='initial'）；
-- 后续版本 = 纠正（revision_type='correct'），区间与 fact.recorded_at 不变（AC-1-3 / AC-3-2）。
-- 旧内容永久保留（AC-3-2/AC-4-4 要求能回看原误记）。
-- 【v4】取消 revision_id：修订是 fact 的从属对象，用 (fact_id, seq) 复合主键定位；
--   事件 fact_corrected 从不携带修订 ID，导出 entities.jsonl 的 fact_revision 行也只有 fact_id + seq。
CREATE TABLE IF NOT EXISTS fact_revision (
  fact_id     TEXT NOT NULL        -- 所属事实段：外键 fact(fact_id)，ON DELETE RESTRICT
  , seq       INTEGER NOT NULL     -- 修订序号：从 1 连续递增；当前内容 = 最大 seq 的修订
                                   -- 用途：AC-3-4/AC-7-4「多次变更按时间可区分」的第二排序键
  , content   TEXT NOT NULL        -- 事实内容：单行、1..2000 字符、UTF-8 原样（不做大小写/全半角归一化）
  , revision_type TEXT NOT NULL    -- 修订类型：initial=首次录入（必须 seq=1）/ correct=纠正（seq>=2）
  , note      TEXT                 -- 修订备注：仅 correct 可非空；0..500 字符；用于「为何纠正」
  , recorded_at TEXT NOT NULL      -- 本次修订时间：RFC3339 带偏移；纠正记录「何时改」
  , PRIMARY KEY (fact_id, seq)     -- 复合主键：替代原 revision_id；跨 fact 的全局修订 ID 不再存在
  , CHECK (seq >= 1)
  , CHECK (length(content) BETWEEN 1 AND 2000)
  , CHECK (content NOT LIKE '%' || char(10) || '%' AND content NOT LIKE '%' || char(13) || '%')   -- 禁止换行（单行）
  , CHECK (revision_type IN ('initial', 'correct'))
  , CHECK ((revision_type = 'initial' AND seq = 1) OR (revision_type = 'correct' AND seq >= 2))
  , CHECK (note IS NULL OR (length(note) <= 500 AND note NOT LIKE '%' || char(10) || '%'))
  , CHECK ((revision_type = 'initial' AND note IS NULL) OR revision_type = 'correct')
  , CHECK (recorded_at GLOB
      '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9][+-][0-9][0-9]:[0-9][0-9]')
  , FOREIGN KEY (fact_id) REFERENCES fact(fact_id) ON DELETE RESTRICT
);

-- =============================================================================
-- 索引（每条索引都写明意图：服务于哪条查询/哪个不变量）
-- =============================================================================
-- 1) 未删除类别的展示名唯一：避免「按名称引用」产生歧义（VALIDATION_CATEGORY_NAME_TAKEN）
CREATE UNIQUE INDEX IF NOT EXISTS ux_category_name_alive
  ON category(display_name) WHERE deleted_at IS NULL;
-- 意图：references 解析第二层（display_name）必须无歧义；AC-10-2 新增类别时查重
-- 说明：category_key 是主键，天然全局唯一（含已删除），不再需要 ux_category_key_alive。

-- 2) 模板顺序唯一：保证 AC-10-5 的展示顺序确定（7 个模板各占一个序号）
CREATE UNIQUE INDEX IF NOT EXISTS ux_category_template_order
  ON category(template_order) WHERE origin = 'template';
-- 意图：画像类别排序（template_order 升序）在数据层就唯一，避免两类别同序导致渲染不确定

-- 3) 按类别枚举命题线：画像按类别分组渲染、类别删除时统计/处置存量事实
CREATE INDEX IF NOT EXISTS ix_fact_line_category
  ON fact_line(category_key);
-- 意图：profile 渲染按类别顺序分组；category delete 时枚举受影响 line（AC-10-3）

-- 4) 事实段定位：时点回溯（valid_from <= at）与区间不重叠校验
CREATE INDEX IF NOT EXISTS ix_fact_line_valid_from
  ON fact(line_id, valid_from);
-- 意图：AC-4-1/AC-4-3 的「某时点取哪一段」；UNIQUE(line_id, valid_from) 已隐式建索引，
--       本条为显式声明（便于 schema 读者理解意图；实际可与 UNIQUE 索引合并，见迁移说明）

-- 5) 删除过滤：当前画像/回溯只取未删除事实段（AC-1-4）；部分索引减少无效行
CREATE INDEX IF NOT EXISTS ix_fact_alive_line
  ON fact(line_id, valid_from) WHERE deleted_at IS NULL;
-- 意图：当前 head 判定（每 line 取 valid_from 最大的未删除段）与「删除后回退到上一段」

-- 6) 演进链完整性：校验 previous_fact_id 指向同 line 的上一段（chain 校验）
CREATE INDEX IF NOT EXISTS ix_fact_previous
  ON fact(previous_fact_id);
-- 意图：verify 的重叠/链校验；删除事实时沿链检查（AC-3-1 两段并存的可追溯性）

-- 7) 事件按类型过滤：history 的「类别变更」段、审计与排障（如只看 category_deleted）
CREATE INDEX IF NOT EXISTS ix_event_type
  ON event(event_type);
-- 意图：AC-10-3 历史可查类别删除动作；verify/审计按事件类型筛选

-- 8) 事件按时间排序：history 倒序展示与「按时间可区分」
CREATE INDEX IF NOT EXISTS ix_event_occurred_at
  ON event(occurred_at);
-- 意图：AC-7-4（3 次变更按时间可区分）；非当前画像路径（当前画像不读事件时间）

-- 说明：fact_revision 的 (fact_id, seq) 查询由复合主键隐式索引覆盖，不再单独建索引。

-- =============================================================================
-- 视图（只读便利视图；不属于持久化表，无法独立写入）
-- =============================================================================
-- v_fact_current_content：每条事实段的当前内容（= 最大 seq 修订）
-- 【v4】不再输出 revision_id（修订无独立 ID）；以 (fact_id, revision_seq) 定位。
-- 【v6】valid_from / valid_to 为完整 RFC3339 时间戳（带偏移）；左闭右开区间。
CREATE VIEW IF NOT EXISTS v_fact_current_content AS
SELECT f.fact_id,
       f.line_id,
       f.valid_from,
       f.valid_to,
       f.change_type,
       f.recorded_at,
       f.deleted_at,
       r.content,
       r.revision_type,
       r.recorded_at AS content_recorded_at,
       r.seq        AS revision_seq
FROM fact f
JOIN fact_revision r ON r.fact_id = f.fact_id
WHERE r.seq = (SELECT MAX(r2.seq) FROM fact_revision r2 WHERE r2.fact_id = f.fact_id);
-- 意图：渲染当前画像/历史时，避免每个事实段重复写「取 max(seq)」子查询；也是 AC-2-2 溯源的取数入口

-- v_line_current_fact：每条命题线当前 head 事实段（未删除段中生效瞬间最大者）
CREATE VIEW IF NOT EXISTS v_line_current_fact AS
SELECT v.*
FROM v_fact_current_content v
WHERE v.deleted_at IS NULL
  AND NOT EXISTS (
        SELECT 1 FROM fact g
        WHERE g.line_id = v.line_id
          AND g.deleted_at IS NULL
          AND g.valid_from > v.valid_from);
-- 意图：当前画像判定（architecture.md §2.5）：head = 未删除段中 valid_from 最大；
--       判定不依赖系统时钟（AC-2-3 确定性的关键）

-- =============================================================================
-- 不变量校验查询（给 verify 的 store.invariants / sql.constraints 使用）
-- 下列每条查询正常情况下必须返回 0 行；返回非空即校验失败（错误码 CORRUPT_EVENT_SEQUENCE
-- 或 IMPORT_SEMANTIC_INVALID，location 指向对应事件序号/行号）。
-- -----------------------------------------------------------------------------
-- (1) 事件序号必须从 1 连续：
--   SELECT MIN(event_seq), MAX(event_seq), COUNT(*) FROM event;   -- 期望 MIN=1 且 MAX=COUNT
-- (2) 首事件必须是 store_initialized 且只出现一次、format_version 必须为 3：
--   SELECT event_seq, event_type FROM event WHERE event_seq = 1 AND event_type <> 'store_initialized';
--   SELECT event_seq FROM event WHERE event_type = 'store_initialized' AND event_seq <> 1;
--   SELECT event_seq FROM event WHERE event_type = 'store_initialized'
--     AND (json_extract(payload, '$.format_version') IS NOT 3);
-- (3) 同一命题线内未删除事实段不得重叠（按瞬间严格递增，且 valid_to = 下一段 valid_from）：
--   SELECT a.fact_id, b.fact_id FROM fact a JOIN fact b
--     ON a.line_id = b.line_id AND a.deleted_at IS NULL AND b.deleted_at IS NULL
--    AND a.valid_from < b.valid_from AND (a.valid_to IS NULL OR a.valid_to > b.valid_from);
--   -- 期望 0 行；同时 a.valid_to 必须等于 b.valid_from（派生一致性；按偏移换算后的瞬间比较）
--   -- 零长区间（valid_to <= valid_from）非法：由 UNIQUE(line_id, valid_from) + 严格递增保证
-- (4) 删除类别的存量处置必须完成：不存在「类别已删除、但其 line 仍挂在它下面」的孤儿：
--   SELECT l.line_id FROM fact_line l JOIN category c ON c.category_key = l.category_key
--    WHERE c.deleted_at IS NOT NULL AND c.deleted_handling = 'migrate';
--   -- 期望 0 行（迁移必须已改写 line.category_key）
-- (5) 修订序号连续（每 fact 的 seq 必须是 1..n 无洞）：
--   SELECT fact_id FROM fact_revision GROUP BY fact_id HAVING MAX(seq) <> COUNT(*);
-- (6) 每个 fact 至少一条 revision 且第一条为 initial：
--   SELECT f.fact_id FROM fact f LEFT JOIN fact_revision r ON r.fact_id = f.fact_id AND r.seq = 1
--    WHERE r.fact_id IS NULL OR r.revision_type <> 'initial';
-- (7) 演进链必须同 line 且按瞬间严格递增（previous_fact_id 指向同 line 的更早段）：
--   SELECT f.fact_id FROM fact f JOIN fact p ON p.fact_id = f.previous_fact_id
--    WHERE f.line_id <> p.line_id OR f.valid_from <= p.valid_from;
--   注：SQL 文本比较仅在同偏移时等价；应用层会先按偏移换算再比较（validate_invariants）
-- (8) 未删除类别中 display_name 唯一、模板顺序 1..7 唯一（由部分唯一索引保证）：
--   -- 索引已强制；若建索引时数据非法会直接失败
-- (9) UUIDv7 形态：fact_id / line_id / previous_fact_id 由 CHECK 强制（36 字符、版本位 7、变体位 10xx）
--   -- 无需额外查询；若 CHECK 未启用（异常路径）可对照 api-spec conventions.ids.uuid7.canonical 正则
-- (10) 时间形态：valid_from / valid_to 由 CHECK 强制 RFC3339（秒精度 + 显式偏移）；
--   人读自适应显示不落库；`YYYY-MM` 粗粒度值**不得**出现在数据库中（迁移器负责补全）。
-- =============================================================================

-- =============================================================================
-- 迁移 / 回滚说明（文字版；当前 schema_version 3；历史：1 → 2 → 3）
-- -----------------------------------------------------------------------------
-- 【版本标识】PRAGMA user_version = 3；事件日志另带 format_version（store_initialized 事件与
--   导出 manifest 中各有一次，当前值 = 3）。两者必须一致：user_version 描述「读取模型结构」，
--   format_version 描述「事件/导出格式」。
--
-- 【v2 → v3 迁移（brief C-07：生效时间精度升级为秒级）】
--   兼容性：**不向后兼容读取**。v3 代码遇到 v2 数据目录 ⇒ STORE_FORMAT_UNSUPPORTED（退出码 5，
--   提示迁移）；遇到 v2 导出包 ⇒ IMPORT_UNSUPPORTED_FORMAT_VERSION（退出码 4，提示迁移）。
--   步骤（脚本 `scripts/migrate_v2_to_v3.py`，一次性离线、stdlib；详见 architecture.md §13.5）：
--   1) 迁移前：`fasti verify --deep` 预检；整目录备份 `~/.fasti.v2-backup-<ts>` + v2 `fasti export`；
--      建议先复制备份到 /tmp 副本演练（dry-run → 实跑 → verify → 比对显示）。
--   2) 停写（关闭 fasti web；单用户无常驻，天然满足）。
--   3) 对每条 fact_created / fact_evolved 事件做：
--        valid_from: "YYYY-MM" → "YYYY-MM-01T00:00:00<offset>"；
--        offset 取**同一事件 recorded_at 的偏移**（用户数据为 +08:00）；
--        store_initialized.format_version → 3；其它事件/字段/seq 原样。
--      校验：同 line 按瞬间严格递增（v2 月唯一 ⇒ 升为月起点后必然递增，无零长区间）。
--   4) 原子写回（tmp + fsync + rename）→ 删除 state/manifest.json →
--      fasti verify（全 PASS）→ recompute → render。
--   5) 成功判据：迁移 + 重算 + 渲染后，profile/*.md、dist/* 与迁移前 sha256 逐字节一致
--      （显示自适应）；verify --deep 7/7；同月内演进手测通过。
--   6) 回滚：恢复 v2 备份目录 + 切回旧代码 tag；极端情况用 v2 bundle（旧代码 import）恢复。
--   注意：迁移器任何校验失败都**不触碰** events.jsonl；若目标已是 v3 则幂等 no-op。
--   DDL 差异：v2 的 valid_to_month（YYYY-MM）→ v3 的 valid_to（完整 RFC3339）；
--   valid_from 由月字符串升级为完整时间戳；其他表/索引/约束不变。
--
-- 【v1 → v2 迁移（brief C-06：ID 方案改用 UUIDv7）】
--   兼容性：**不向后兼容读取**。v2 代码遇到 v1 数据目录 ⇒ STORE_FORMAT_UNSUPPORTED（退出码 5，
--   提示迁移）；遇到 v1 导出包 ⇒ IMPORT_UNSUPPORTED_FORMAT_VERSION（退出码 4，提示迁移）。
--   两条路径（详见 architecture.md §12.5）：
--   A. 清空重建（P0 采用；当前仅 2 条事实）：
--      1) fasti export（留一份 v1 人读备份，可选）；
--      2) 停止写命令 → mv ~/.fasti ~/.fasti.v1-backup（整个目录挪走，保留可回退）；
--      3) fasti init（写 format_version=2 的 store_initialized + 7 条模板类别）；
--      4) 重新录入原有事实（P0 仅 2 条；用 web 表单或 CLI）→ recompute → render；
--      5) 宿主软链不变，渲染即覆盖；跑 fasti verify。
--   B. 事件改写器（真实用户带数据时；一次性离线脚本，stdlib）：
--      1) 备份 data-dir（或先 export v1 bundle 留档）；
--      2) 逐行读取 v1 events.jsonl，按 seq 顺序建立映射：
--         旧 category_id → 新 category_key（从 v1 category_added 事件读 key；保留模板 key）；
--         旧 fact_id / line_id → 新 UUIDv7（按事件顺序生成；建议注入固定随机源使其可复现）；
--         旧 revision：v1 事件不含 revision_id，无需映射（读模型改复合键即可）；
--      3) 重写事件 payload 字段：category_id → category_key、
--         migrate_to_category_id → migrate_to_category_key、ID 字段替换为 UUIDv7；
--         重写 store_initialized.format_version = 2；seq 保持 1..N 连续；
--      4) 原子写回（tmp + fsync + rename），删除 state/manifest.json；
--      5) fasti verify（期望 store.invariants + sql.constraints 通过）→ recompute → render →
--         用 fasti export 生成 v2 bundle 并做一次空环境导入往返验证。
--      注意：事件改写会丢失「旧 ID」外观（历史语义不变）；改写前必须备份；不得在运行中的
--      v1 代码与 v2 代码之间来回切换同一数据目录。
--
-- 【迁移原则（今后）】读取模型是派生数据，不需要 ALTER TABLE 迁移数据：
--   1. 结构变更（加列/加表/改约束）→ 修改本 DDL，user_version +1；
--      旧事件按新 schema 重新折叠校验。
--   2. 事件格式变更（字段/语义）→ format_version +1；必须同时提供：拒绝旧版本的明确错误 +
--      一次性迁移器/迁移说明（P0 不承诺向后兼容读取）。
--   3. 不允许「只改 DDL 不改折叠逻辑」：两者必须同时改，否则校验与渲染会漂移。
--
-- 【回滚步骤（数据回滚）】
--   1) 停止一切 fasti 写命令（单用户、无后台进程，天然满足）；
--   2) 用 state/backups/events-<时间戳>.jsonl 覆盖 events.jsonl（导入前自动备份；
--      或手工备份的历史副本）；
--   3) 删除 state/manifest.json（派生状态归零，避免「假装最新」）；
--   4) 运行 verify（期望 sql.constraints 与 store.invariants 通过）→ recompute → render。
--
-- 【回滚步骤（v3 → v2 / v1，含结构回滚）】
--   1) 恢复对应版本的数据目录备份（~/.fasti.v2-backup-<ts> / ~/.fasti.v1-backup）或该版本 export；
--   2) 切回对应版本代码（git tag）；
--   3) 若需退回旧 schema：按依赖逆序删表/视图，再执行 v1 DDL 与 user_version；
--      顺序：DROP VIEW v_line_current_fact; DROP VIEW v_fact_current_content;
--            DROP TABLE fact_revision; DROP TABLE fact; DROP TABLE fact_line;
--            DROP TABLE category; DROP TABLE event;   （索引随表删除）
--   注意：由于数据真相在 events.jsonl，结构回滚不涉及数据丢失，只需重放校验。
--
-- 【物理删除政策】P0 任何路径都不物理删除 event 行与 fact_revision 行：
--   用户可见的「删除事实/删除类别」都是墓碑（deleted_at）+ 事件，历史永久保留（US-4/AC-1-4/AC-10-3）。
--   数据在库中的物理删除只会发生在用户手工恢复备份的场景。
-- =============================================================================
