# AOF 完整诊断报告

诊断日期：2026-09-05。范围：产品目标、版本与交付边界、架构、语义与 Agentic 链路、身份与数据隔离、审计、测试和 CI、部署、依赖、可运维性及下一步验收。此次产出为诊断，不修改业务实现、不合并分支、不部署服务。

## 1. 核心判断

**AOF 已有可复验的受治理语义运行时和合成财务单域工程闭环，但整个项目还没有形成统一、可靠、可部署的企业产品基线。** 最强的部分是 SemanticResource → 审批 → 签名 KnowledgeRelease → 受控查询/动作 → 回执的纵向实现；最弱的部分是版本整合、公共入口治理、审计账本完整性、历史企业模块的真实持久化，以及部署模板与当前代码的一致性。

这不是仅凭目录或 README 得出的判断。本次在远端确认主线提交，导出干净快照，运行全量测试、覆盖率、静态检查和针对性复现。测试通过与以下缺陷同时成立：匿名请求能写入和读取运行时共用的决策账本；并发写入能破坏其哈希链；注册在 OpenAPI 中的部分接口因模块未进入 main 而返回 500；Kubernetes 健康检查请求了不存在的路径。

项目现阶段适合继续开展受限、隔离环境中的工程验证。开放多用户访问前应先关闭本报告的 P1 问题；真实业务验收应在此后使用企业样本完成。没有证据支持把当前状态描述为通用 Agentic 平台、完整企业 IAM，或已经验证收益的财务控制平面。

## 2. 本次检查的版本与证据边界

| 对象 | 本次确认的状态 | 诊断处理 |
|---|---|---|
| 用户当前工作目录 | `/Users/<owner>/LLM/AOF`，HEAD=`649bf527353b634dae746e64395f3b9c1172b672`，main 初始版本 | 保留所有脏文件；在临时导出副本运行旧版测试 |
| 远端 main | `bfc1e02d799c5810fef25b3ee10c9445d7e52f12`；`git ls-remote` 实时核实；比工作目录领先 9 个提交 | 核心诊断基准 |
| 主线干净快照 | `/private/tmp/aof-diagnosis-20260905/main` | 所有复现仅写临时目录，未接触实际业务数据 |
| internal edition | 本地远端跟踪引用 `origin/codex/internal-edition=f501c553`；本地同名分支另有分叉 | 只用现存 Git 对象核对能力差异；未宣称本次已刷新该分支远端 |
| GitHub CI | main 对应 `test` 检查 success | [本次核对的 CI](https://github.com/andrewchay/AOF/actions/runs/33936512610/job/101225391986) |
| 本机部署 | Docker 仅见 `optimed-postgres`，未见 AOF 容器；8787 未发现监听 | 不能把 Compose/K8s 文件当作已部署系统；未使用其他项目数据库 |

旧工作目录的测试是 **65 passed**；主线测试为 **389 个用例**。此前 internal 分支的测试规模不能直接套用到 main，也不能根据数量差就判断测试被错误删除：两者并非同一个产品文件集合。具体缺口必须按功能核对。

依赖安装因网络下载长期无进展而停止。为验证是否依赖本机隐式 `.pth`，从本机已安装分发包中按 `requirements-dev.txt` 及传递依赖递归复制到新虚拟环境，校验版本约束，排除 `.pth` 和未声明 Cognee。该环境测试成功，**这是隔离解释器验证，不是全新联网安装或镜像构建验证**。包版本保存在 [isolated-packages.json](evidence/isolated-packages.json)。Ruff 使用本机 0.15.9 二进制；复制环境没有复制 CLI 二进制。

## 3. 产品与架构诊断

### 3.1 为什么这个方向值得继续

AOF 试图解决的是：企业从来源材料形成业务知识后，如何确保 Agent 在正确的语义版本、证据、权限和审批条件下执行，并能解释和追溯结果。价值不在于单独增加一种搜索类型，而在于把知识发布与业务动作之间的责任链连接起来。

当前规范路径能够支撑这个方向：

```text
来源快照 / 证据
  → SemanticResource / proposal
  → validate / review / waiver
  → compile / signed KnowledgeRelease / channel promotion
  → 同版本 graph / SQL / evidence / Skill / rule
  → ActionPlan / 独立审批 / execute
  → receipt / reconciliation / decision provenance
```

关键依赖是来源准确性、身份可信性、发布与执行版本一致性，以及写入和审计的可靠性。控制边界必须覆盖所有进入共享状态的入口。验收不能只看“财务问题返回一个答案”，而应验证：证据定位正确，数值与数据库一致，审批前零业务写入，审批主体分离，不确定结果停止重试，重放不产生新业务副作用。

### 3.2 已有能力与实际限制

| 领域 | 已有且本次测试覆盖的能力 | 尚不能据此宣称的能力 |
|---|---|---|
| 语义资源与发布 | 不可变资源、依赖闭包、验证/审批、签名 release、promotion、回放 | 任意企业领域数据已完成语义核验 |
| 确定性执行 | 受控 SQL、RDF 图、Datalog、同 release 检索证据 | 通用开放问题解析、任意语义推断 |
| Agentic 编排 | 词汇路由、已发布 QueryContract DAG、步骤预算、工具证据、总结引用、运行持久化 | 开放式动态重规划；默认 LLM fallback 实际执行 |
| 统一知识底座 | 本地 RDFLib、只读 SQLite、稀疏词频检索、发布的 Function/Action catalog | 外部 Embedding、图库/向量库、Skill 市场集成已验收 |
| 财务工程闭环 | SQL→规则→分析 Skill→待审批工单→独立审批→执行或对账 | ERP/CRM 真实连接器、客户签字、实际 ROI |
| 会话记忆 | tenant/session 隔离、摘要和查询、摘要完整性检查 | 每用户私有会话、字段级隐私和数据保留闭环 |
| 发布与运维控制 | readiness、遥测、SLO、本地 backup/replay | HA、容量、跨节点持久状态、外部灾备演练 |

`tests/test_finance_review_e2e.py` 使用合成财务表，但 SQL 与本地 SQLite 工单写入是真实执行。样本收入 80、预算 100、回款 50、未回款 30，其他地区/月份被排除；合同定位到第 3.1 条；独立审批前工单数为 0，审批后成功分支为 1，不确定分支进入 `reconciliation_required`。工单连接器由测试注册，并非生产 ERP 适配器。这个测试有工程价值，也有明确业务边界。

`evaluate().score` 衡量结构和引用完整性；summary 校验工具输出文本和 citation 对应关系。它们不度量领域答案正确率，也不构成自动事实核查。

### 3.3 架构维护成本

主 API 文件为 **5,924 行、159 个路径、168 个 HTTP 操作**，混合历史 topic/Cognee 接口、运维、训练、决策和新语义控制面。MCP 文件为 1,164 行。认证大量依赖各端点自行调用校验函数，这使新增受治理入口正确、旧入口遗漏的情况同时存在。

`semantic_core/contracts.py + service.py + repository.py` 另有一套 `SemanticFact/Release` 生命周期；本次调用检索未发现它接入 REST/MCP 或现有测试，规范运行时实际使用 `models.py/releases.py` 等。应标明其原型身份或收敛，避免未来调用者把两套 Release 当作可互换合同。此项是维护风险，不作为已复现生产故障。

## 4. 按优先级排序的发现

这里的 P1 表示正式共享环境发布前必须关闭；P2 表示应在产品化阶段处理。对外暴露旧入口的部署会提高 P1 安全问题的紧迫性，但本次未发现实际生产服务遭利用，因此不作已发生泄露的判断。

### D01 · P1：决策 API 匿名读写且信任请求体身份，直接触及新运行时账本

**证据：** `app.py:4138–4189`，`DecisionRecordReq` 接受 `agent_id/tenant_id`，端点没有签名 principal；`_decision_store()` 同时被新的控制面使用。[固定提交源码](https://github.com/andrewchay/AOF/blob/bfc1e02d799c5810fef25b3ee10c9445d7e52f12/services/semantic_middle_layer_api/app.py#L4138)。

**复现：** 无 headers 的 POST `/v1/decisions` 返回 201，并接受合成的 `publisher:impersonated`、`tenant-a`；匿名 GET 返回 200；不指定 tenant 的先例搜索返回该记录。详见 [decision-probe.json](evidence/decision-probe.json)。

**影响：** 调用者可以伪造审计身份、写入决策、读取共享决策记录。不能用“旧接口兼容”解释为隔离完毕，因为账本就是新运行时使用的账本。

**建议与验收：** 以可信 principal 注入主体和租户；所有读、先例、因果链和影响分析都验证租户和权限；需要迁移的本地管理入口隔离。匿名写入应失败，跨租户按 ID、先例、父决策遍历都不泄露，客户端身份字段无授权效力。

### D02 · P1：审计账本并发不安全，损坏后的普通读取没有失败关闭

**证据：** `bridge/decision_provenance.py:125–164` 先读取整个文件和链头，再 append，没有覆盖读链头→计算→写入的互斥/事务；`get()` 调用 `_entries()`，不先校验链完整性。`verify_integrity()` 是可选独立调用。[源码](https://github.com/andrewchay/AOF/blob/bfc1e02d799c5810fef25b3ee10c9445d7e52f12/bridge/decision_provenance.py#L125)。

**复现：** 用 barrier 固定“两个写入者先读到同一链头”的合法并发时序：两次 record 均成功返回，但第二条完整性校验失败。另将临时账本 conclusion 改写，完整性校验 false，但 `get()` 仍返回改写后的内容。[probes.json](evidence/probes.json)。这是同步化竞态复现，不是自然负载发生率测量。

**影响：** 正常并发即可损坏审计链；知道完整性检查存在，不等于消费时执行了检查。随着账本增长，每次写入全文件读取也会放大成本。

**建议与验收：** 为追加建立跨进程事务/锁及持久化策略，验证前序链和 ID 唯一性；读取/导出拒绝损坏记录。多线程、多进程并发写入后链有效，无重复 ID；损坏后读、写、审计导出均受控失败。外部签名锚定可在明确威胁模型后增加，普通 hash 不代表管理员不可篡改。

### D03 · P1：main 与 internal 能力集合不一致，留下可调用的残缺 REST/MCP

**证据：** main 没有 `bridge/context_exchange`、`bridge/harness_trainer`、`bridge/training_data`、`web` 和若干 exporter，而 internal 跟踪引用中有对应资产。main 仍注册 Harness、训练、OKF/RAG 等相关端点或工具。[差异清单](evidence/edition-code-diff.txt)、[缺失内部导入](evidence/missing-internal-imports.json)。

**复现：** GET `/v1/harness/sessions`、POST `/v1/training-data/generate` 在干净主线快照中均返回 500。[接口证据](evidence/missing-routes.json)。AST 扫描发现 10 个不同的缺失内部模块引用，涉及 REST 和 MCP；不是说 10 个都已逐一调用验证。前端源码未进入 main，因此无法从 main 构建前端。

**影响：** checkout/部署版本决定实际产品能力；OpenAPI 可生成且 CI 通过仍不能证明接口可执行。Context Exchange 既往成果不能作为当前 main 的交付能力。

**建议与验收：** 建立功能级“main / internal / 废弃 / 待迁移”台账，逐项决定保留、迁移或显式禁用。不要把整个分叉历史直接合并。每个公开操作至少执行 import/startup smoke；已暴露但不支持的能力明确 410/503，不能因内部模块缺失 500。若前端属于交付物，要求从干净克隆构建、测试并打包。

### D04 · P1：生产模板绕开新生产配置门禁，K8s 无可靠运行条件

**证据：** Compose prod 未设置 `AOF_RUNTIME_MODE=production`、三类签名密钥及 key id、明确 SLO/OTel 等配置。代码默认 development；Nginx 模板没有身份认证或按能力隔离旧接口。K8s `deployment.yaml` 的 liveness/readiness 都指向 `/health`，并设置 3 replicas，但只挂载 `/tmp` emptyDir，没有 AOF 持久状态卷。[部署源码](https://github.com/andrewchay/AOF/blob/bfc1e02d799c5810fef25b3ee10c9445d7e52f12/k8s/deployment.yaml#L48)。

**复现：** 默认环境 readiness 返回 `{mode: development, ready: true}`；`/health`=404，`/healthz`=200。尚未实际启动 K8s；路径错误是 TestClient 与配置交叉确认。

**影响：** “prod”文件名并不启用生产门禁；K8s 探针会失败。即便修正探针，多副本本地状态也没有一致性或恢复保证。简单把副本数改大不能提供当前运行时 HA。

**建议与验收：** 先形成明确单实例、持久卷的可复验部署；生产模板强制 production 模式并缺配置拒绝启动/就绪；liveness 与 readiness 分离；完成重启状态保留。多副本在共享状态/账本并发方案实证后再启用。镜像构建→启动→探针→发布→查询→重启恢复须进入部署 smoke。

### D05 · P1：从当前目录构建镜像会把未忽略的 `.env` 纳入 COPY 范围

**证据：** 当前目录存在未跟踪 `.env`；main 和本地 `.dockerignore` 均未排除 `.env/.env.*`；Dockerfile 用 `COPY --chown=aof:aof . $APP_HOME` 复制构建上下文；`.gitignore` 也没有忽略环境文件。只检查存在性和规则，没有读取或记录密钥值。

**影响：** 按 README/生产清单从当前工作目录运行 Docker build 时，本地配置文件可进入构建上下文和镜像。此处没有执行构建，没有证据表明已上传镜像或泄露。

**建议与验收：** 增加环境文件和私有数据忽略规则，保留显式无秘密示例；构建测试放置合成 `.env` 哨兵，确认镜像中不存在。避免 COPY 全部工作区成为默认分发边界。

### D06 · P1：历史 IAM/租户 CRUD 返回成功但没有实际持久化

**证据：** `bridge/auth/rbac.py:408` 后的用户/角色数据库函数大量 pass；`bridge/tenant/manager.py:503` 后的持久化、唯一性检查等未实现；状态更新直接返回 True。[RBAC 源码](https://github.com/andrewchay/AOF/blob/bfc1e02d799c5810fef25b3ee10c9445d7e52f12/bridge/auth/rbac.py#L408)。

**复现：** 创建用户返回 ID，立即 get 为 None；创建租户返回 active，立即 get 为 None；相同 slug 再创建成功。[probes.json](evidence/probes.json)。使用默认管理器，这些函数不会因为传入 db_session 自动获得 ORM 实现。

**影响：** README 中完整 IAM、多租户生命周期的描述超出该模块实现。新签名 principal 的验证机制是真实的，但它依赖可信签发方，不能代替用户目录和租户生命周期服务。

**建议与验收：** 明确自建 IAM 或外接可信 IdP；不用的历史管理器标识 unsupported。需要保留的 CRUD 必须做到创建后读取、重启保留、slug 唯一、角色撤销和租户停用实测；不能以 mock 掩盖持久化缺失。

### D07 · P1：CI 绿灯不覆盖类型失败、功能缺失或真实部署

**证据与实测：** CI 的 mypy 命令有 `|| true`；本机环境报 40 个错误，新隔离环境报 **56 个错误 / 26 文件**，并因 app 模块重复发现而中止后续检查。它们包含缺失模块、类型 stub 和代码类型问题，不能把 56 全部称为运行时 bug。完整输出见 [mypy-isolated.txt](evidence/mypy-isolated.txt)。

治理脚本在 `data/governance/change_requests` 不存在时通过；本次通过的原因正是没有该目录。因此该步骤没有验证任何真实变更单，也不替代 SemanticGovernanceService 测试。OpenAPI drift 检查比较 schema，没有调用全部业务操作。CI 没有镜像启动、K8s 探针、前端构建或覆盖率阈值。

**建议与验收：** 先修模块解析/可选依赖边界，再把类型检查变为真实门禁；明确 CR gate 的“未配置/无待审批/真实批准”状态；加入内部导入和公开端点可用性检查、D01/D02 对抗测试及部署 smoke。覆盖率按风险模块设置，避免把全仓数字当唯一目标。

### D08 · P2：审计查询/外部存储适配器存在成功空实现

**证据：** `bridge/audit/logger.py:358–388` 的 DB/MQ 写入 pass；`bridge/audit/query.py` 的按 ID、文件/数据库查询和异常检测含未实现分支。`bridge/storage/cognee_backend.py` 部分读取/清理也未实现。对这些位置做了源码检查，没有接外部数据库/MQ验收。

**影响与验收：** README 的审计报告/异常检测等能力需分级。启用未实现后端应明确拒绝，不可记录成功计数却不落地；已落地文件事件必须可重读、可检索。不要把这套旧 AuditLogger 与新 DecisionProvenance 混为同一成熟度。

### D09 · P2：数据与会话授权粒度还不足以表达私密业务协作

**证据：** Agentic memory 表按 tenant/session 组织；读 API 检查租户和通用 read role，没有 subject/session ACL。项目文档也明确“不是每用户私有会话”。这是现有设计边界，不是跨租户隔离失效的已证实结论。

**建议与验收：** 为用户私有、团队共享、业务对象共享会话定义明确策略，并覆盖运行历史和回放。若财务 session 含敏感问题，相同租户 viewer 不应天然获得所有 session。保留期、删除/冻结、日志脱敏、备份保护需要落入可执行配置和测试。

### D10 · P2：依赖与运行环境不能稳定再现全部能力

开发和服务 requirements 不同，除 Ruff 外大量使用无上界范围，没有完整 lock；README 要本地 editable Cognee，Docker 默认安装通用 Cognee，prod 又固定 `0.1.45`。本次没有验证该版本满足当前适配器 API，不能直接判为已证实不兼容。

隔离测试跳过的唯一项为 Cognee ontology runtime 集成，生产适配器缺依赖时仍抛错，符合失败关闭。外部解析器、LLM/Embedding、数据库/MQ等没有完成端到端连通验证。

**验收：** 给 core、Cognee、文档解析、观测、部署分别声明受支持依赖集合与锁定版本；以全新环境和镜像分别验证，不依赖开发机 `.pth`。缺可选能力应在 capability/readiness 中可见。

### D11 · P2：资源生命周期和容量边界缺少负载证据

本机覆盖率运行产生 435 条 warnings，其中可见大量未关闭 SQLite connection。`SqliteAgenticRunRepository` 多处使用 `with self._connect()`，事务上下文并不显式 close；建议审查连接生命周期。尚未证明产生生产 FD 耗尽，不能据此估算承载量。

`OBS_PATH_STATS` 按原始请求路径累计，动态 ID/任意路径会增加时序键；DecisionProvenance 每写一次读取全部账本。这些适合做有目标的容量基线。验收应测连接/FD 是否回落、连续运行内存是否有界、并发时错误率和 p95，而不是先引入更多分布式组件。

### D12 · P2：文档/交付包还存在明显漂移

README 历史部分仍写 51 端点，实际为 159 路径/168 操作；前端和 Context Exchange 不在 main；API 描述仍偏旧版 SQL 生成；License 仍为占位符。生产清单虽补了治理段落，但旧 smoke 与部署配置不完全适配新模式。

验收应以能力台账驱动 README、API、运行手册和包内容：每项声明链接到当前版本实现及验证证据，明确 experimental/compatibility/unsupported。License 占位状态需要项目负责人明确分发规则，此报告不作法律授权判断。

## 5. 测试、复现与实际结果

| 检查 | 结果 | 解读 |
|---|---|---|
| 旧 HEAD 干净快照测试 | 65 passed，2 warnings | 只说明旧版测试通过 |
| main 本机完整环境 | 389 passed，435 warnings，14.87s | 本机有外置 Cognee 等环境；带 coverage |
| main 隔离解释器 | 388 passed，1 skipped，12.91s | 无 Cognee/.pth；按声明依赖复制已安装包；不等于全新联网安装 |
| Ruff 0.15.9 | passed | 源码默认 lint 通过 |
| OpenAPI drift | passed | schema 一致；不证明全部操作可运行 |
| strict CR gate | passed，实际无 change_requests 目录 | 本次未校验真实 CR |
| mypy 隔离环境 | 56 errors / 26 files，中止 | CI 忽略失败 |
| 匿名决策 API | POST 201、GET/search 200 | D01 复现 |
| 匿名 metadata 写入 | 200 且临时文件存在 | 其他旧写入口亦没有统一鉴权 |
| 受治理 proposal 无签名读取 | 401 | 新入口的身份门禁有效 |
| 账本并发及篡改读取 | 两次成功写入后链 false；损坏内容仍可 get | D02 复现 |
| 缺模块 API | Harness、training-data 返回 500 | D03 复现 |
| 健康路径 | `/health` 404；`/healthz` 200 | D04 复现 |
| 容器盘点 | 无 AOF，仅其他项目 PostgreSQL | 无生产部署结论 |

覆盖率为 **语句覆盖率**，scope 是 `bridge + services`，不是整个仓库、更不是业务正确率；未启用 branch coverage。

| 模块 | 语句覆盖率 |
|---|---:|
| bridge + services 合计 | 58.52%（10,295 / 17,593） |
| semantic_core/agentic_system.py | 90.00% |
| semantic_core/query_execution.py | 88.43% |
| decision_provenance.py | 87.67% |
| services/.../app.py | 48.49% |
| auth/rbac.py | 40.50% |
| tenant/manager.py | 47.30% |

高覆盖率的决策账本仍有并发与读取完整性缺口，说明需要增加正确的对抗用例，而不是只提高覆盖率数字。[覆盖率明细](evidence/coverage-summary.json)。

## 6. 建议的处理顺序与关闭条件

| 顺序 | 工作包 | 完成条件 |
|---|---|---|
| 1 | 固定交付基线与能力归属（D03） | main/internal 差异逐项有归属；干净克隆没有公开功能的缺失内部模块；保留脏工作区，按功能迁移 |
| 2 | 封闭身份、共享账本与数据入口（D01/D02/D05） | 匿名/跨租户对抗通过；并发及损坏账本失败关闭；镜像无环境文件哨兵 |
| 3 | 形成可部署的最小受治理服务（D04/D06/D07） | production 强制配置、可信身份签发路径、持久状态、重启恢复、有效探针及 CI 部署 smoke |
| 4 | 完成一次真实财务业务验收 | 替换合成数据；财务负责人核对数值、来源、条款、审批和执行回执；不确定结果对账 |
| 5 | 产品化及扩容（D08–D12） | 会话 ACL、保留/删除、可观测容量基线、受支持依赖、必要后台/UI与一致文档 |

最有价值的下一次验收不是增加更多检索能力，而是把现有“收入偏差→合同/回款证据→人工审批→工单/对账”替换为一组经过授权的企业数据。先定义数据来源、时间口径、金额核对、责任主体、允许动作与拒绝条件，再测端到端。真实系统连接器和业务负责人签字是必需证据；未明确这些条件前，不把合成样本通过等同于收益兑现。

不建议在此时整体重写项目或先上多套数据库。保留已经通过验收的语义合同、发布控制和动作状态机；优先把它们外围的身份、版本交付、账本与运行条件补齐。

## 7. 可复验资料与未覆盖项

本目录 `evidence/` 保存测试摘要、mypy、版本差异、包版本、缺失导入、复现脚本和 JSON。脚本路径默认指向本次临时 main 快照，换机器需将 ROOT/sys.path 改为该固定提交的干净 checkout；所有演示数据为临时合成数据。

主线复验基本命令：

```bash
python -m pytest -q -o addopts=''
ruff check .
python -m mypy aof_add.py aof_run.py aof_doctor.py bridge services tools
python tools/governance/validate_release_gate.py --root data/governance --strict
python tools/export_openapi.py --check
```

本次未完成或不属于此次证据：全新联网依赖安装、Docker image build、真实 K8s 启动、前端浏览器验收（main 无源码）、真实 LLM/Embedding/ERP/MQ 连接、负载/渗透测试、灾备切换、客户验收、业务收益归因。没有读取实际 `.env` 内容，没有发送外部业务消息，没有修改既有代码或 Git 状态，没有将诊断问题伪称已修复。
