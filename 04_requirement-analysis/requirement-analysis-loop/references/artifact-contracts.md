# 产物契约

## 可选图集交付契约

新项目 `memory/diagram-deliverables.json` 使用 `contract: optional-analysis-diagrams@1`；`requested` 必须同时包含布尔 `business_flow` 与 `interaction_sequence`，默认均 false；`requests` 保存每次显式请求的 kind/by/reason/at。由 `loop.py request-diagrams` 记录并使受影响阶段失效，不能从其他交付请求推断。该文件纳入 G3 快照和 manifest，handoff.diagram_deliverables 必须与其完全一致。

未请求类型没有 HTML entrypoint、不打入 ZIP，即使旧 HTML 仍在工作区；diagrams 对应条目保留事实来源和 count，html/renderer_mode 为 null、self_contained=false、status=not_requested。FLOW/SC/SEQ JSON 与页面结构 HTML 始终必需。新老交接合同名称不变，字段为显式扩展；没有该文件／字段的旧工作区／包维持两图必需，不静默迁移。以下图集契约仅对已请求图适用。旧冻结包只读，补图从工作副本重新核验并取得 G3。

## 通用规则

- JSON 文件使用 UTF-8、两个空格缩进和 `schema_version: "1.0"`。
- ID 格式：`PREFIX-` 加三位以上数字，例如 `SC-001`。
- 时间使用带时区的 ISO 8601。
- `source_refs` 保存可定位的来源；未知不得写成空泛的“业务文档”。
- 数组无内容时使用 `[]`，未知单值使用 `null`，不要用含义不明的空字符串。
- 人读视图中的 `<!-- status: draft -->` 必须在完成阶段前移除。

## 原型交接契约

S9 交付包根目录必须同时包含：

- `delivery-manifest.json`：`schema_version: "1.1"`，记录每个冻结文件的字节数与 SHA-256；清单不包含自身，但必须包含 `prototype-handoff.json`。
- `prototype-handoff.json`：`contract.name` 固定为 `requirement-analysis-to-prototype`，`contract.version` 固定为 `1.0`。

交接契约必须记录 `baseline.id`、`baseline.fingerprint`、G1/G2/G3 批准信息、全局核验摘要、接受风险、`id_catalog`、`entrypoints` 和图集元数据。`id_catalog` 至少包含全量 `acceptance_ids`、本期必覆盖的 `required_acceptance_ids`、`feature_ids`、`page_ids`、`action_ids`；`entrypoints` 必须覆盖用户故事、场景、时序、接口、项目事实、业务规则、功能清单、页面规划、追溯、问题清单、核验报告、三份 HTML 图集和五份 Markdown 视图。

`baseline.fingerprint` 从项目身份与除 handoff 自身之外的 manifest 文件条目确定性计算。原型消费者必须同时重算 manifest SHA-256、逐文件哈希与基线指纹，并把 manifest SHA-256 绑定到原型 S2 计划批准记录。业务事实或图形几何发生变化时，不得原地更新已导入基线，必须回到本 Loop 重新核验和冻结。

`consumer_rules.llm_wiki_consumed_by` 固定为 `requirement-analysis-loop`，`raw_inputs_are_audit_only` 固定为 `true`。原型的结构化事实入口不得指向 `inputs/` 中的原始材料。可选 `entrypoints.source_index` 指向 `inputs/source-index.json`，角色为 `provenance_only`，与正文分开：原型只保留元数据，禁止读取查询正文或据此新增事实。上述字段为 1.0 契约的兼容扩展；缺少它们的历史包仍遵守当前原型技能的相同边界，不原地改写冻结包。

新顺序生产的包声明 `analysis_workflow_version: "stories-flows-interfaces-sequences-pages-features@1"`，并必须包含 `entrypoints.page_structure_atlas`（`outputs/page-structure-atlas.html`，`text/html`，`review_view`）。原型保留该图和页面结构事实入口。无此版本字段的历史冻结包仍按原契约只读兼容，不自动补图或改写。

## inputs/source-index.json

S0 的来源元数据索引，不是业务事实清单；新项目默认 `sources: []`，无 Wiki 的历史项目可省略。Wiki 快照必须登记；不要求整个知识库入库。示例：

```json
{
  "schema_version": "1.0",
  "sources": [{
    "id": "SRC-001",
    "type": "llm_wiki",
    "title": "账户充值历史知识查询",
    "snapshot_path": "inputs/wiki-context/recharge-loop-context.md",
    "wiki_ref": "wiki/accounts/recharge",
    "raw_ref": null,
    "version": "unknown",
    "received_at": "2026-09-06T10:00:00+08:00",
    "status": "partial",
    "coverage": ["已有充值状态；缺少当前渠道回调约定"],
    "issue_ids": ["ISSUE-001"]
  }]
}
```

- 所有记录要求稳定 `SRC-*`、`type` 和 `title`。Wiki 类型固定为 `llm_wiki`，还必须包含上述 Wiki 元数据；`raw_ref` 未知用 `null`，版本或日期未知用 `unknown`。
- `snapshot_path` 是分析项目 `inputs/` 内的真实文件，不能引用项目外路径；仅 `status=missing` 时允许为 `null`。`wiki_ref/raw_ref` 是定位信息，不授权原型访问这些位置。
- `status` 只允许 `verified/partial/missing/conflict`；`coverage` 是非空的覆盖与缺口描述数组。`verified` 只表示所列覆盖范围经分析核对，不表示整个 Wiki 或全部接口已确认。
- `partial/missing/conflict` 必须提供实际存在的 `issue_ids`；critical/high 的开放项沿用 G3 阻断规则。需要来源级追溯时，问题的 `related_ids` 可包含 `SRC-*`。
- 结构化事实的 `source_refs` 可指向快照章节或 `inputs/source-index.json#SRC-001`；原型不沿引用读取正文，只消费已冻结结论。
- Wiki 更新时登记新快照与元数据，并先执行 `invalidate --id SRC-001` 或 `--from-stage S1`，不能静默替换原型已导入的基线。

## normalized-user-stories.json

```json
{
  "schema_version": "1.0",
  "user_stories": [
    {
      "id": "US-001",
      "title": "创建外呼任务",
      "role": "运营人员",
      "trigger": "需要发起一批外呼",
      "goal": "配置并提交任务",
      "business_value": "批量触达目标用户",
      "preconditions": [],
      "acceptance_criteria": [{"id": "AC-001", "type": "main", "statement": "..."}],
      "source_refs": ["inputs/user-stories.md#故事-1"],
      "status": "in_scope"
    }
  ]
}
```

## scenario-index.json 与 SC 文件

索引只保存发现和汇总所需字段，详情放在独立文件：

```json
{
  "schema_version": "1.0",
  "scenarios": [
    {
      "id": "SC-001",
      "name": "创建外呼任务",
      "file": "scenarios/SC-001.json",
      "priority": "P0",
      "scope": "in_scope",
      "status": "confirmed",
      "source_story_ids": ["US-001"],
      "ui_surface": "required"
    }
  ]
}
```

## project-facts.json 与 business-rules.json

`project-facts.json` 保存基线汇编所需的项目事实：

```json
{
  "schema_version": "1.0",
  "project": {"name": "云呼叫中心", "version": "1.0", "objective": "..."},
  "actors": [{"name": "运营人员", "description": "..."}],
  "systems": [{"name": "外呼中台", "description": "..."}],
  "scope": {"in": ["..."], "out": ["..."]},
  "product_structure": [{"name": "任务管理", "description": "..."}],
  "state_models": [{"id": "STATE-001", "name": "任务状态", "states": ["草稿", "运行中"], "transitions": ["草稿 → 运行中：提交"]}],
  "assumptions": ["..."]
}
```

`business-rules.json` 保存可引用的规则与跨场景流程：

```json
{
  "schema_version": "1.0",
  "business_rules": [{"id": "BR-001", "name": "...", "statement": "...", "source_scenario_ids": ["SC-001"]}],
  "business_flows": [{"id": "FLOW-001", "name": "...", "scenario_ids": ["SC-001"], "steps": ["..."]}]
}
```

`FLOW-*` 和场景泳道在 S3 生成，不等到 S7 基线汇编。`business_flows[].steps` 是 `FLOW-*` 的有序流程事实，至少包含一个非空步骤。生成器必须在 `outputs/business-scenario-atlas.html` 中为每个 `FLOW-*` 生成 `data-diagram-type="business-process"` 的内嵌 SVG，并在同一文件继续生成 `SC-*` 泳道 SVG；只有 Markdown 编号列表不满足交付契约。

## feature-list.json

`memory/feature-list.json` 在 S7 最终确认，是版本功能清单的唯一事实源。S6 已有页面结构但不能据此倒推出新能力；仍从 US/AC/SC 及接口、时序的已确认结论形成完整能力清单，包含非 UI 功能。完成后回填页面和操作的 FUNC 引用。一级、二级模块必填，三级模块可为 `null`；模块层级表达能力结构，不等同于菜单路径。备注无内容时使用 `null`。结构化修改类型只允许 `new`、`iteration`，人读文档分别展示为“新增”“迭代”。

新一轮生产声明 `granularity_version: "business-operation@2"`，按 SKILL.md 的自包含核心规则及 [功能拆分与范围标准](feature-granularity.md) 确认业务对象、单一主操作和可验收结果，不得将 SC 与 FUNC 机械一一对应。历史冻结包（含 `minimal-business-process@1`）仍只读兼容，不能由原型推断补齐或重拆；如需升级粒度，先从 S7 复核、保留旧新 ID 对照，再重新冻结。

```json
{
  "schema_version": "1.0",
  "granularity_version": "business-operation@2",
  "features": [
    {
      "id": "FUNC-001",
      "version": "1.0.0",
      "module_l1": "账户中心",
      "module_l2": "充值管理",
      "module_l3": null,
      "name": "发起充值申请",
      "description": "用户提交充值金额，系统创建待支付订单并返回支付入口。",
      "notes": "仅为边界示例，不自动纳入实际充值项目。",
      "change_type": "new",
      "boundary": {
        "business_object": "充值申请／待支付订单",
        "primary_action": "发起充值申请",
        "system_boundary": "本方账户系统负责申请与订单；支付渠道内部收款不在本方实现范围。",
        "initiator": "已登录的账户用户",
        "trigger": "用户确认提交充值申请",
        "primary_outcome": "取得本次充值申请对应的有效待支付订单与支付入口",
        "completion_result": "成功创建待支付订单并返回支付入口；失败时不创建有效订单并明确反馈",
        "included_scope": ["金额与账户资格校验", "创建订单并取得渠道支付入口", "返回申请结果"],
        "excluded_scope": ["支付渠道内部收款", "支付结果确认与余额入账"],
        "business_rules": ["金额按已确认规则校验；同一次提交不重复建单"],
        "exception_paths": ["无效金额拒绝", "重复提交返回已创建结果", "渠道失败不返回虚假成功"],
        "dependencies": ["账户身份与支付渠道创建支付能力"],
        "assumptions": [],
        "split_rationale": "申请形成独立待支付结果；金额字段和技术调用不能独立完成该业务目的"
      },
      "change_detail": {
        "before": null,
        "after": "1.0.0 提供充值申请及待支付订单结果",
        "impact": ["新增申请校验、订单创建与结果反馈；不含余额入账"]
      },
      "source_story_ids": ["US-001"],
      "source_acceptance_ids": ["AC-001"],
      "source_scenario_ids": ["SC-001"]
    }
  ]
}
```

约束：

- `id` 使用稳定 `FUNC-*`；对外引用后不得因排序或模块移动重新编号。
- `version`、`module_l1`、`module_l2`、`name`、`description`、`change_type` 必填且不得为空。
- `module_l3` 与 `notes` 只允许非空文本或 `null`，不得用空字符串代替未知或不适用。
- `new` 表示该版本首次引入；`iteration` 表示该版本改变既有功能的行为、规则或范围。当前版本未发生变化的存量功能不应伪装为本轮修改项。
- 功能粒度是单一可验收业务操作；已确认的新增／修改、开通／停用／恢复／删除、查询／详情／导出、配置／发布／默认切换，符合独立触发与结果时须分别列项，不能打包成管理或场景。控件、字段、必要校验／回滚仍属于操作明细；不凭动作清单自动增加需求范围。
- `boundary` 的八个文本字段必填且非空，新增 `business_object`（业务对象）和 `primary_action`（具体单一主操作），不能仅写“管理／全流程处理”。`split_rationale` 保留操作拆分归属与未单拆明细的理由。六个列表字段必须为非空文本组成的数组，`included_scope` 至少一项。其余列表已确认无内容才写 `[]`，不表示未知。假设与缺口同时记录在问题清单，沿用 G3 门禁。
- `change_detail.before`：新增为 `null`，迭代为非空文本，包含原版本／基线定位及原行为；`after` 为本轮交付结果的非空文本，`impact` 为非空文本数组，明确本轮改变的范围。稳定 FUNC 不因同一能力跨页面或拆成多个实现任务而增加。
- `outputs/feature-list.md` 保留原总览列并追加逐项范围、AC 判据和版本差异，带完整功能 JSON 与所引用 AC 判据的 SHA-256 标记。S7/S8/打包检查边界字段及视图同步；脚本不判断语义最小性或报价正确性，G3 必须复核拆分／合并依据。
- 每个功能必须至少引用一个用户故事、验收条件和本期业务场景；每个本期用户故事、验收条件和业务场景也必须至少被一个功能覆盖。
- 一个功能可由多个页面承载；非 UI 功能可以没有 `PAGE-*`，但仍须保留上游引用。

### 场景详情文件

```json
{
  "schema_version": "1.0",
  "id": "SC-001",
  "name": "账户充值",
  "summary": "...",
  "actors": ["用户", "充值系统", "支付渠道"],
  "trigger": "...",
  "preconditions": ["..."],
  "main_flow": [
    {"step": 1, "actor": "用户", "action": "选择充值金额并确认", "system_response": "创建充值订单", "rule_ids": ["BR-001"]}
  ],
  "alternate_flows": [
    {"id": "ALT-001", "condition": "支付失败", "steps": ["订单标记失败", "提示用户重试"]}
  ],
  "postconditions": ["..."],
  "acceptance_points": ["..."],
  "source_story_ids": ["US-001"],
  "flow_diagram": {
    "lanes": [
      {"id": "customer", "name": "用户", "kind": "actor"},
      {"id": "recharge", "name": "充值系统", "kind": "system"},
      {"id": "payment", "name": "支付渠道", "kind": "external_system"}
    ],
    "nodes": [
      {"id": "N-START", "lane_id": "customer", "type": "start", "order": 1, "label": "发起充值"},
      {"id": "N-001", "lane_id": "customer", "type": "action", "order": 2, "label": "选择金额并确认", "step_ref": 1},
      {"id": "N-002", "lane_id": "recharge", "type": "action", "order": 3, "label": "创建充值订单"},
      {"id": "N-003", "lane_id": "payment", "type": "action", "order": 4, "label": "完成支付"},
      {"id": "N-004", "lane_id": "recharge", "type": "decision", "order": 5, "label": "支付成功？"},
      {"id": "N-005", "lane_id": "recharge", "type": "action", "order": 6, "label": "增加账户余额"},
      {"id": "N-SUCCESS", "lane_id": "customer", "type": "end", "order": 7, "label": "充值成功"},
      {"id": "N-FAIL", "lane_id": "customer", "type": "end", "order": 6, "label": "提示支付失败"}
    ],
    "edges": [
      {"from": "N-START", "to": "N-001"},
      {"from": "N-001", "to": "N-002"},
      {"from": "N-002", "to": "N-003"},
      {"from": "N-003", "to": "N-004"},
      {"from": "N-004", "to": "N-005", "label": "是"},
      {"from": "N-005", "to": "N-SUCCESS"},
      {"from": "N-004", "to": "N-FAIL", "label": "否", "type": "failure", "path_id": "ALT-001"}
    ]
  }
}
```

`flow_diagram` 是业务场景图集的唯一绘图事实源：

- `lanes` 顺序就是 HTML 中从左到右的泳道顺序；`kind` 允许 `actor`、`system`、`external_system`。
- `nodes[].type` 允许 `start`、`action`、`event`、`decision`、`end`。
- `nodes[].order` 是正数，只决定节点从上到下的相对层级；不同泳道节点可使用相同 order 表示并行层级。渲染器会把所有不同 order 映射成具有安全间距的独立行，因此不要使用 `12.1`、`12.2` 等数值手工“挪图”。
- `edges` 表示控制流。任一节点出现多条出边时，每条出边都必须填写互斥、可判断的 `label`。
- `main_flow` 的每个步骤都必须至少被一个节点的 `step_ref` 引用；每个 `alternate_flows[].id` 都必须至少被一条连线的 `path_id` 引用。
- `main_flow` 用于文字评审，`flow_diagram` 用于流程结构；两者语义必须一致。

图集生成器负责版式，不在 JSON 中维护坐标：

- 同一节点的多条出边或入边使用不同连接端口，决策菱形的端口必须落在真实边界上。
- 相邻行的连线使用独立行间轨道；跨越多行的连线转入泳道间通道，不能穿过中间节点。
- 小数 order 与整数 order 具有相同的最小行间距，避免 `12.5` 与 `13` 被压在一起。
- 生成前执行几何核验；节点重叠、线穿节点、共线叠压、非正交线或端点错位均为阻断错误。
- 渲染后端不改变此契约。原生 SVG 与 PlantUML 都必须从同一份 `flow_diagram` 派生；`.puml` 只存放在 `runtime/plantuml/` 作为可重建的审计中间物，不得反向成为业务事实源。
- PlantUML 评审版必须把本地生成的 SVG 直接内嵌到 `business-scenario-atlas.html`，不得在 HTML 加载远程脚本、图片或渲染接口。

业务图集和交互时序图集共用桌面宽屏阅读壳：`body.atlas-desktop[data-reading-layout="desktop-wide@1"]`、无固定内容宽度上限、默认折叠的顶部目录、紧凑留白。图形等比显示：小于容器时铺满可用宽度，超过容器时默认保留自然宽度并横向滚动，用户可选择适应宽度。面板的 `data-natural-width/height` 记录自然尺寸；保留缩放、100%、阅读模式、下载与全屏，工具栏在全屏内可用。S3/S5、S8 与打包核对阅读壳版本、折叠目录及控件元数据，不能把这些结构检查当作视觉验收。禁止为移动端压缩标签、删节点或强制把超宽图缩成不可读缩略图；这两类图不做移动端验收。页面结构图和业务原型页面的设备约定不由此变更。

### 可选 Mermaid 候选（不是正式交接产物）

S3 的候选入口及产物约定见 [Mermaid 候选绘图工具](mermaid-candidate.md)。candidate-manifest.json 声明 mermaid-candidate@1、原始图 ID、formal_delivery=false，绑定来源文件字节及派生 graph.json、Mermaid 源码、审计快照。SC 只能从 flow_diagram 映射，泳道责任作为明确标记保留，但普通流程视图不得冒充泳道；FLOW 只能按已给 steps 顺序绘制，不推断分支。

渲染后额外绑定 HTML、SVG 与独立 verification.json；此报告不是 runtime/verification-report.json，不改变阶段状态。来源或派生文件发生变化必须生成新候选。候选源文件、报告和 HTML 不能替代正式八类展示物，也不加入原型交接入口；runtime/mermaid-candidates/ 不在打包清单中。

## sequence-index.json 与 SEQ 文件

```json
{
  "schema_version": "1.0",
  "sequences": [
    {"id": "SEQ-001", "scenario_id": "SC-001", "name": "创建外呼任务时序", "file": "sequences/SEQ-001.json", "status": "confirmed"}
  ]
}
```

```json
{
  "schema_version": "1.0",
  "id": "SEQ-001",
  "scenario_id": "SC-001",
  "name": "创建外呼任务时序",
  "participants": [
    {"id": "operator", "name": "运营人员", "kind": "actor"},
    {"id": "console", "name": "外呼中台", "kind": "system"}
  ],
  "messages": [
    {"step": 1, "from": "operator", "to": "console", "message": "提交任务", "type": "request", "api_id": null, "condition": null}
  ],
  "notes": []
}
```

`participants` 和 `messages` 是交互时序的唯一事实源。每个 `SEQ-*` 在 `outputs/interaction-sequence-atlas.html` 中必须恰有一个原生内嵌 SVG，带 `data-diagram-type="sequence"`、`data-sequence-id="SEQ-*"`、`data-renderer="native"` 和 `data-layout-audit="pass"`；`data-sequence-sha256` 绑定完整 SEQ JSON 事实。`data-message-index` 按消息数组顺序覆盖每条消息，标签完整保留步骤号、消息类型、条件、`API-*` 和消息正文；长文本自动换行并增加行间距，不截断事实。

时序仅支持 `native` 正式模式；`--sequence-renderer plantuml/compare` 明确拒绝，不调用 PlantUML、不生成 `SEQ-*.puml`，也不混入历史 PlantUML SVG。响应使用虚线返回箭头，自调用使用回环箭头。S5/S8/打包都检查图集数量、事实指纹及消息覆盖；任何事实修改后须重新渲染。不要从零散条件猜测 `alt/else` 组合片段，需先扩展并确认事实契约。

## interface-index.json

```json
{
  "schema_version": "1.0",
  "interfaces": [
    {
      "id": "API-001",
      "name": "创建呼叫任务",
      "provider": "呼叫平台",
      "source_scenario_ids": ["SC-001"],
      "used_by_sequence_ids": ["SEQ-001"],
      "verification_status": "verified",
      "source_refs": ["inputs/interface-documents/vendor-api.md#创建任务"],
      "request_fields": [],
      "response_fields": [],
      "auth": "...",
      "timeout": "...",
      "idempotency": "...",
      "rate_limit": "...",
      "errors": [],
      "notes": []
    }
  ]
}
```

`source_scenario_ids` 是 S4 必填的本期业务场景引用；泳道中涉及外部系统的场景必须有对应接口核验结论。`not_required` 必须在 notes 写明无需接口的理由。S4 的 `used_by_sequence_ids` 可为 `[]`，S5 按实际调用回填，并重新生成矩阵；未知接口或超出场景核验范围必须回到 S4。G2 的接口事实指纹只排除这个派生使用关系字段。

`verification_status` 只允许：`verified`、`partial`、`missing`、`conflict`、`not_required`。

## page-index.json

```json
{
  "schema_version": "1.0",
  "pages": [
    {
      "id": "PAGE-001",
      "name": "新建任务页",
      "menu_path": ["任务管理", "新建任务"],
      "roles": ["运营人员"],
      "function_ids": ["FUNC-001"],
      "scenario_ids": ["SC-001"],
      "sequence_ids": ["SEQ-001"],
      "states": ["initial", "submitting", "success", "error"],
      "fields": [],
      "actions": [
        {"id": "ACTION-001", "name": "提交", "source_function_ids": ["FUNC-001"], "source_scenario_ids": ["SC-001"], "permission": "task:create"}
      ],
      "acceptance_points": []
    }
  ]
}
```

`page-index.json` 在 S6 从 SC/SEQ 推导：`scenario_ids`、`sequence_ids` 与操作的 `source_scenario_ids` 必须有效，`function_ids/source_function_ids` 此阶段可为 `[]`。S7 确认归属后，当前业务页面及操作必须引用真实本期 FUNC，或经严格核验的未变存量操作；两者均无的空归属不能进入 S8。存量不写成 new/iteration，也不借页面级引用、导航或历史出处补业务覆盖。`menu_path: []` 表示非菜单入口，不能为了绘图虚构父页面。

`outputs/page-structure-atlas.html` 从菜单路径前缀构建层级，再挂接带 PAGE ID 的页面叶节点；连线只表达层级归属，不表达业务流或请求先后。必须包含一个 `data-diagram-type="page-structure"`、`data-renderer="native"`、`data-layout-audit="pass"` 的原生 SVG，不得混入 PlantUML 图，`data-page-ids` 为排序后的全部 PAGE ID（逗号分隔；无 UI 时为空）。`data-page-structure-sha256` 绑定生成时的 pages 事实，防止同 ID 的名称、层级或关联更新后仍冻结旧图。每个叶节点以 `data-page-node` 对应 PAGE ID；节点自动换行、按子树分配空间并检查无重叠，不生成 `.puml`。图中明确说明无 UI 的情况。

## traceability.json

```json
{
  "schema_version": "1.0",
  "links": [
    {"from": "US-001", "to": "AC-001", "type": "has_acceptance"},
    {"from": "US-001", "to": "SC-001", "type": "decomposes_to"},
    {"from": "SC-001", "to": "SEQ-001", "type": "sequenced_by"},
    {"from": "SC-001", "to": "API-001", "type": "requires_interface"},
    {"from": "API-001", "to": "SEQ-001", "type": "constrains_sequence"},
    {"from": "SEQ-001", "to": "API-001", "type": "calls"},
    {"from": "SEQ-001", "to": "PAGE-001", "type": "informs_page"},
    {"from": "US-001", "to": "FUNC-001", "type": "specifies"},
    {"from": "AC-001", "to": "FUNC-001", "type": "verifies"},
    {"from": "SC-001", "to": "FUNC-001", "type": "realized_by"},
    {"from": "FUNC-001", "to": "PAGE-001", "type": "surfaced_by"},
    {"from": "SC-001", "to": "PAGE-001", "type": "surfaced_by"},
    {"from": "PAGE-001", "to": "ACTION-001", "type": "contains"}
  ]
}
```

## open-items.json

严重级别只允许 `critical`、`high`、`medium`、`low`。状态只允许 `open`、`resolved`、`accepted_risk`。`critical` 或 `high` 的开放项阻止 G3。


## 未变存量操作的严格归属

问题可用可选 `resolution_scope` 区分关闭责任：`first_party_analysis_design` 仅表示本方需求/异常契约分析完成，不处置任何关联的 partial/missing/conflict 接口，也不代表接受风险、生产实现或联调完成。真实外部语义应继续由有明确关联的开放问题跟踪。`interface_contract_disposition` 和未带该可选字段的历史 resolved 行沿原兼容规则；未知或非文本 scope 拒绝，不能因拼错而回退到历史接口豁免。原 open high/critical、接口及 G3 门禁保持。

本契约只解决来源与覆盖，不证明生产已部署，不代替风险接受或G3批准；普通仅含FUNC的既有项目沿原公开schema，不新增项目私有字段要求。

- `memory/feature-list.json.retained_capability_references[]` 使用稳定`draft_key`，明确对象、单一主操作、业务触发、完成结果、US/AC/SC和来源；`delivery_class=retained_reference_not_delta`、`formal_function_id=null`，不使用new/iteration。
- 每份原行证据须有文件SHA、精确行号和实际观察；其文件/SHA必须直接或经注册快照中的文件哈希表绑定，不能只给一个存在的文件路径或伪SRC片段。
- 从候选形成独立保留引用须记录`retained_scope_review`：原候选键及JSON指纹、逐字段来源比较、实际审核人/日期、已确认范围AC、条件纳入和未决基线问题。原候选保留，不能改key、删标记或翻转role绕过审核。审核确认的是来源归属，不是用户批准或生产可用。
- 候选没有业务trigger时，可由安全JSON pointer定位原证据，并用`derived_trigger_evidence`给出同处理器的精确原控件或调用证据及完整目标。仅函数声明、同名其他对象、注释或诊断字符串不能证明业务触发。只识别支持的证明形式，无法证明则保留待核；不执行原型源码，也不宣称自动完成通用JavaScript语义验证。
- 可选 `trigger_kind=source_chain` 使用 `bounded-static-chain@1`：每个入口、静态导出/代理调用与原处理器终点均绑定已注册来源、当前文件SHA和精确行原句。仅支持无阴影/无改写的静态成员、可证render控件、明确分支和有限固定helper；layer须另证已导出render中的打开控件。跨文件工厂须显式静态绑定，不按短名或隐式全局别名猜测。词法源链只补来源归属，不证明一般控制流、实际分支执行或生产可用；不支持的传播和原仅定义/导出项继续待核。
- 当前ACTION通过`retained_operation_refs`及逐项`retained_operation_bindings`声明归属；绑定含当前PAGE/ACTION、原操作键、对象/主操作、实际SC、已审ACTION签名和注册审核快照。改名、改权限、改场景或换对象后须重新审核，不能借查询引用授权删除。
- PAGE、ACTION与保留引用必须双向一致。覆盖只从合法当前主操作绑定推导到该SC及其US/AC；前置查看、历史源、页面级空挂接和一条宽引用不能覆盖整场景。未决操作及未挂UI的后台责任单独核验，不因其没有按钮而漏掉。
- 退役来源仍保留稳定ID与真实迁移片段/当前目标/原权限边界，仅作追溯；不能复活当前菜单、扩大目标权限或冒充本期功能。普通界面明细仍归真实主功能，不独立计功能或业务覆盖。

### 显式表格渲染来源（P1）

`source_chain.entry.render_provenance` 可选采用 `version=bounded-table-return@1`，仅在原 `template_event` 中显式使用。既有入口、owner、事件后links、来源注册/SHA/原句、候选指纹、精确终点和遮蔽检查保持；缺此字段时沿原语法，不把任意table或回调加入UI白名单。

- 固定 `reachability=conditional_only`、`route_options_policy=default_route_view_no_override`、`table_sink_callee=PlatformUI.table`、table列/行槽位为0/1。它只证明原默认route/view及非空数据等原条件下的静态来源，不证明运行时权限、数据可用、实际执行或生产效果。
- 路由字段 `route_key/route_page/route_view`；根渲染与委托 `exported_render_symbol/render_delegate_symbol/view_parameter/view_variable`；列与控件 `column_key_literal/exact_event_target_symbol/event_object_argument_index`；wrapper名称、列/行形参及零基参数槽由 `wrapper_symbol/wrapper_columns_parameter/wrapper_columns_argument_index/wrapper_rows_parameter/wrapper_rows_argument_index` 精确声明。
- `evidence` 必含 `route_registration/route_dispatch/route_sink/render/delegate/wrapper/table_implementation/table_export`；菜单投射到路由表再经命名resolver时另需 `route_resolution`。每段均有已注册 `source_refs`、`file/source_sha256/line_start/line_end/source_quote`。entry沿用实际按钮模板证据及原 `ui_alias_evidence`。
- 必须绑定唯一公开的原导航派发、同route/view的已导出render及其命名返回委托、唯一字面量目标列render箭头、原wrapper的参数转送与真实table结果进入返回HTML。未调用的导航/渲染helper、未使用列或未返回的table结果不能作入口证据。
- 插值必须进入有效HTML文本；template/script/style/textarea/title/noscript、注释、属性缓存中的结果不作可见控件。没有通用CSS/DOM可见性或一般JavaScript控制流的验证承诺。
- 可选命名resolver须保选定key；明确改写本key的分支不能借原注册对象背书。导航绑定到DOM sink间不能改写page/render或选定view；只容纳可精确证明最后恢复 `view:route.view` 的原 `Object.assign` 形态，未知覆盖、尾部spread、假Object等继续拒绝。明确移除目标route或全部rows的源码不能借条件性声明放行。
- 真实table须把原 `col.render(row[col.key],row)` 放入td、body和返回tbody；有限编号前处理仅可增加不带render且不覆盖目标key的字面列前缀并spread全部原列，以及保原row spread而不覆盖目标key的编号克隆。破坏性列过滤、render改写、错误参数槽或row身份覆盖拒绝。

P1不自动支持转义command helper、未限定全局名、共享业务const别名或成员保存分支；这些必须有各自显式证据语法，不能用只打开弹层的调用代替业务保存终点。



### 显式全局对象、独立弹层及成员条件链（P2）

以下为 `bounded-static-chain@1` 的可选有限证据，不改变缺省 owner/入口/分支判据；每段 evidence 仍须具有已注册 `source_refs`、当前 `file/source_sha256/line_start/line_end/source_quote`。仅证明静态条件来源，不能证明运行时鉴权、数据存在、实际保存或供应商执行。

- `static_owner_export.global_owner_binding` 可显式采用 `version=explicit-window-owner@1`，字段为 `bare_owner/window_owner/evidence`。只连接该文件中唯一、同属性同实现的 `Owner.method` 与 `window.Owner` 公开导出；证据必须覆盖确切发布位置，局部遮蔽、重复或错误对象均拒绝。不全局删除 `window`，未声明此证据的既有隐式 owner 负例继续拒绝。
- `entry.opening_provenance.version=bounded-direct-table-layer@1` 分别证明表格中的原打开按钮和弹层中的原保存按钮。entry 沿用 `opening_evidence`（已导出 render）、`ui_alias_evidence` 和实际保存控件 `evidence`；opening 配置必含 `opening_symbol/opening_callee/opening_handler_evidence/column_key_literal/event_object_argument_index`（此 profile 固定0），以及下述 helper/table/escape 证据。不得把打开控件或创建后补开弹层的调用充作保存操作。
- `entry.direct_table_provenance.version=bounded-direct-table-event@1` 使用同一有限表格/固定 helper 判据，供直接表格按钮使用；另必含 `render_evidence`。它与 `opening_provenance/render_provenance` 互斥，不自动放行任意 table 回调。必须是同 owner 已导出 render 的真实返回模板、字面列 key 的唯一 renderer、精确原行对象参数、真实 PlatformUI.table 与有效 HTML 事件上下文；未使用表格、空行常量/清空/slice(0,0)、错误参数槽、假 UI、隐藏原始字符串继续拒绝。未知数据查询只保条件性，不宣称非空。
- 两种表格 profile 均必含 `handler_helper_evidence/helper_call/escape_alias_evidence/table_implementation_evidence/table_export_evidence/escape_implementation_evidence`。`helper_call` 含 `name/literal_method`；只支持原同作用域 `const command=(method,...args)=>esc(固定Owner＋method＋JSON.stringify参数join)` 形态，esc须是同作用域不可改写的 `const esc=ui.escape`，ui须来自 PlatformUI。真实 escape须为原 String/null 及五种有序HTML字符替换；动态方法、假escape、局部JSON/String/UI、属性改写不作证明。
- 新link `kind=const_owner_alias` 必含 `alias/owner/evidence`，精确绑定同作用域 `const api=Owner` 及当前待解析接收者；随后仍须独立 `static_owner_export` 到确切组件实现。它不是工厂；let、重绑、属性写入、其他receiver或仅同名方法均拒绝，原工厂规则保持。
- 成员保存callsite可显式使用 `branch=state_else`、`reachability=conditional_only` 和 `state_branch.version=bounded-state-else@1`。state_branch必含 `state_symbol/capture_symbol/kind_field/selected_value/excluded_values/result_variable/object_field/object_argument_index/capture_evidence`。此有限 profile 只支持已证 opening 在真正弹层前直接捕获选中kind与原首个对象ID、save内不可改写的 `const e=editor`、已声明 `let result`、原连续 `if/else if/else` 的精确排除值，以及末else直接 `result=api.method(e.id,...)`；对象参数槽固定0。
- result约束仅限所选成员分支及后续反馈区：原create/edit互斥分支可各自赋值；所选调用后的result整值或属性改写、延迟回调、错误对象/分支、缺排除分支、只定义/只开弹层和无条件执行声明均拒绝。原候选JSON pointer、原终点文件/方法、所有SHA及原句必须相符，不得换终点来通过。

两种inline table profile的主返回及实际保存layer调用，必须是各自函数体直接语句，不能以 `if(false)` 等死/条件语句背书；原有前置条件拒绝仍保留。opening捕获后至layer、save捕获后至成员调用前、成员调用后反馈区，分别保护state/capture/result：已知 `Object.assign/defineProperty/defineProperties/setPrototypeOf` 及 `Reflect.set/deleteProperty` 写入均拒绝。受保护值的未证复制、whole-object传递或传出保守拒绝，裸值只容纳逐源码核实的原 `const e=editor` 捕获、有限的 `!e` 存在性条件及单独核验的直接赋值；字段读取保留，括号包装/未知裸值使用继续待核，不建立通用别名图。

这些 profile 不提供通用JavaScript求值、一般事件可达性或任意DOM/CSS可见性证明；未支持的route、传播或create/edit共享分支继续保留待核，不扩大产品功能、权限或门禁。


### 显式嵌套条件表格控件（P3）

P1 `source_chain.entry.render_provenance` 可另声明 `nested_event_path`，仅显式 `version=bounded-row-ternary-template@1`、`reachability=conditional_only` 时识别列 renderer 内的有限子模板事件；不声明则沿旧顶层判据，不默认递归搜索 onclick。P1 的route/view、唯一已导出render/命名委托、wrapper列/行槽、真实table返回流、原owner/别名/遮蔽与候选/SHA/原句/精确终点判据全部保持。

- 必含 `version/reachability/row_parameter/row_field/path`。row_parameter只能是该字面列render箭头的第二形参；row_field为同一个静态字段。原事件的对象ID仍按P1原列key及参数槽核，不把条件字段当身份字段。
- path含2至32个有序节点。模板节点必须是 `kind=interpolation` 与零基 `expression_index`（仅按当前模板expression parts计数）；精确选择处须为有效HTML文本槽。条件节点必须是 `kind=ternary` 及 `operator/literal/branch`，literal键不可省略，包括显式null。
- 条件只接受完整 `row.field === literal ? operand : operand` 或 `!==` 及整式括号，branch仅consequent/alternate；不接受函数谓词、赋值或一般JS。literal为无转义字符串、有限十进制数、布尔或null，来源与metadata按类型精确对应，不用真假值或字符串化混同。operand仅字面模板、空字符串或同一有限三元形态。
- 沿路径累积同字段的严格等/不等约束，先祖分支与后继互相矛盾或确定死路径拒绝。路径须结束于真实目标子模板；漏/错路径、其他按钮、未用模板或错误字段不作入口证明。
- 每层祖先插值及literal HTML上下文都必须有效：属性、注释、script/style、template/textarea等raw/inert上下文、失衡的HTML及可改变上下文的未知文本拼接均拒绝。只证明已支持原模板结构，不作通用DOM/CSS可见性或一般程序可达性承诺。
- renderer参数和字段不得被直接或经已知Object/Reflect helper改写；整行对象的未证复制/传递按P2有限escape守卫保守拒绝。原值参数v的专业esc(v)/command参数用途不因此禁止，但不由本profile验证事件转义helper组合。目标事件仍须是原P1可核的raw事件和原行身份。

本profile只补原条件按钮的静态来源，不新增业务权限、确认回调链、保存能力、供应商执行或生产结论；不修改原候选/业务scope以通过。转义helper组合及其他未支持传播继续待核。同类逐项证，不自动接受其他候选。

### P4：已确认原操作的有限来源组合（显式选择）

以下是静态、条件性的来源证明格式。它们不执行来源 JavaScript，不证明生产权限、接口执行、一般控制流可达性或最终业务验收；未识别语法继续 pending，不能靠更换原候选、终点或手工白名单通过。旧证明缺少新字段时继续旧路径及原负例行为。不同 profile 不相互借入口。

**委托表格与固定转义 helper。** 在原 `entry.render_provenance` 内显式提供 `event_helper_provenance`：`version=bounded-fixed-escaped-table-event@1`、`reachability=conditional_only`、`handler_helper_evidence`、`helper_call={name,literal_method}`、`escape_alias_evidence`、`escape_implementation_evidence`。原 P1 路由/view、导出 render、delegate、wrapper、实际表格列/行及真实 PlatformUI.table 合同保持；仅复用 P2 已验证的固定 owner、字面方法、JSON.stringify 参数数组及真实 HTML escape 实现。对象参数在去掉 helper 第一个方法参数后仍须绑定所选列的原 v 或 row[key]。这一步不支持同时带 `nested_event_path`，不自动扩为任意 helper 或表格 renderer。

若原纯 escape 函数另被用作局部字符串数组的 `map` 回调，可在同 profile 明确列 `escape_callback_uses`（最多8条）：每条 `version=bounded-local-string-array-map@1`、`array_symbol`、覆盖完整原局部函数的 `evidence`。必须证明唯一 const 空数组、仅在 map 前 push 字面字符串、同数组的直接 length 三元条件读取和准确 map(该 escape 别名)；重绑、覆盖、复制、转交、其他成员、跨函数捕获与原型变化拒绝。仅经真实源码验证的那个参数 token 可作为函数回调使用，不放宽 receiver 对象逃逸或旧 P2 默认守卫。

**返回模板中的 quoted 控件。** `entry.quoted_control_provenance` 采用 `version=bounded-returned-quoted-control@1`、`reachability=conditional_only`、`route_options_policy=default_route_view_no_override`。字段包含原 `route_key/route_page/route_view`、`exported_render_symbol/render_delegate_symbol/view_parameter`、`ui_helper/html_argument_index` 及 `evidence` 中的原路由注册/解析/分派/DOM sink、完整 render/delegate、实际 UI helper 实现/导出。仅支持命名 render 的 options.view 字面分派到零参数命名 delegate，并在其直接返回 template 的活动 HTML 插值中调用真实 `PlatformUI.pageHeader` 第3参数或 `toolbar` 前2参数；控件为该参数的单个原 quoted literal。UI helper 必须确将原 HTML 参数插入活动内容而未转义/改写/转交。支持的 quoted escape 仅反斜杠、单/双引号及 n/r/t；未知 JS escape 保持 pending。隐藏、inert、raw 标签、注释、属性中的按钮，死返回、无实际消费或 UI/owner 别名逃逸不能提供入口。

**直接 inline 表格的 raw 控件。** `entry.raw_table_control_provenance` 采用 `version=bounded-returned-raw-table-control@1`；同样提供上述路由/render/delegate 字段和条件策略，另给 `column_index`、`column_key_literal`、`event_object_argument_index`，`evidence` 包含真实 `table_implementation/table_export`。只接受实际返回 template 中的唯一活动 ui.table，真实 literal columns，所选准确 index 和 key，以及单参数或括号参数箭头直接返回的 template；onclick 必须绑定同一原 owner/方法和该列原 v 或 row[key]。原 display/action 列重复 key 时仅依原列 index 精确定位，不全局允许任意重名列或借其他列按钮。动态列/函数 body renderer、helper、嵌套条件控件和 layer 不能套用此 profile。

**原锚角色守卫。** 可选 `derived_trigger_evidence.original_anchor` 采用 `version=bounded-original-anchor-role@1`，原候选 key/完整 JSON SHA、原 source pointer/field pointer/file SHA、implementation_symbol 必须与当前原记录及比较一致；原 locator/excerpt 必须存在于原 source 的确切行范围。角色 metadata 不是证明。所有显式角色都必须由 bounded-source 的实际词法/结果验证器接受，验证器缺席、异常、未知或返回非错误列表均为 pending。只有被验证的非 callable 结果表达式角色才可例外绑定其唯一原词法所属实现；之后仍须完整正常入口/owner/终点证明。无此字段保持原 locator→目标符号硬约束。仅打开表单的发起锚必须另证同对象/context/mode的后续真实用户保存及实际结果，不能用 opener 已可调用来代替完成。具体角色 profile 未实现时保留待核，不承诺96项自动可用。

P4新增表格 profile 的只读责任是准确事件身份来源：所选 renderer 根内的参数/字段直接改写均拒绝；事件若取第二参数 row[key]，还须拒绝该 row 的未证明复制/转交逃逸。事件若严格取第一参数 v，可保原有 peer 资格函数对 row 的使用，但不把此证明称为整个 renderer 纯度，也不能据此接受 v 的绑定改写。原 P3 的已有全 row 保护及默认负例保持不变。

非 callable 角色当前仅实现 `role_evidence.version=bounded-readonly-result-expression@1` 的原只读详情形状。必填静态名称为 `containing_symbol/result_symbol/readonly_symbol/identity_parameter/readonly_parameter/ui_alias/access_factory_symbol/access_method/dialog_symbol/dialog_capture_symbol/save_symbol`；必填槽位为 `identity_argument_index/readonly_argument_index/result_expression_index/no_save_expression_index`，结果插值槽固定为0；字面量为 `detail_literal/layer_id_literal`。证据包括 `expression_evidence/readonly_binding_evidence/layer_evidence/ui_alias_evidence/readonly_call_evidence/dialog_binding_evidence/save_guard_evidence`及`save_owner_binding={kind:static_owner_export,callee,symbol,evidence}`，以及真实固定事件 helper、escape alias/实现/导出的证据。

该角色只核原唯一父函数中的 `const RESULT=READONLY?详情字面值:ID?编辑字面值:新建字面值`，READONLY来自原 `!!ID&&(READONLY_PARAM||!FACTORY().ACCESS_METHOD(true))`；实际选中控件在准确槽位传入字面 true。原 layer 活动文本消费标题，READONLY 真支排除整个保存控件，dialog 捕获同一 readonly，原 save 最前的保护拒绝该 readonly。参数、identity、结果和 capture 的改写及逃逸不得靠声明豁免。角色通过仍不证明入口表格、路由、对象参数或整个操作通过；正常 source_chain 继续核验。在上述只读／static profile 中，其它角色形状及发起→保存链仍不支持，未另行选择下述 active source-flow 版本时继续 pending，不得以 complete 文字替代实际保存结果。

只读角色须将保存控件的固定 owner/method 通过真实导出绑定到同一 save guard 实现，未导出的同名函数不作证。只读捕获的保护范围覆盖原父 handler（包括 layer 参数/模板和原恢复 hook）；仅精确允许原 `onRestore(){dialog=current;}` 初始化，不因 callback 延后执行而忽略其中改写。



### P4：显式活动来源流、原始发起锚与保存结果（SRC2116有限范围）

新的证明格式为 opt-in：`source_chain.version=bounded-active-source-chain@1`，其 `source_flow_provenance.version=bounded-active-source-flow@1`、`reachability=conditional_only`。`family`只支持`resource/reporting/execution`，`entry_binding`、1至32个`flow_steps`及`result_binding`须逐边绑定原source、SHA、quote、词法函数、参数槽和真实活动HTML/DOM事件。没有字段即待核；不能用同组件、相似名称、布尔声明或代表操作通过替代逐key证明。旧格式、默认原锚和门禁保持。

- `resource`：真实发布render及精确路由/view/kind，经原控件、表格列/renderer/固定事件helper进入原handler；原模式和捕获id/context/revision必须到原保存方法和确切组件结果。分类字段tab必须有原tab选择与render证据；不能借分类入口冒充字段入口。新增、编辑、成员维护不得互相借用。
- `reporting`：原route/view/kind决定实际报表、任务或名单；返回数组、拼接、tabs、表格和次级页面须真实进入HTML槽/DOM。原对象与事件参数须保留到原导出、明细或控制方法；条件可达只证明本地来源，不证明任何运行中业务数据或生产权限。按钮只在空集合显示而成功导出要求同集合非空的矛盾应记录源结果冲突，不能靠确认框/警告或手工元数据判通过。
- `execution`：原表单、捕获状态和用户保存事件、实际发布方法到原结果；原onclick闭包必须实际赋给DOM按钮，并满足原scope/context保护。monitor角色上下文只可依据实际eligible函数和权威本地getter源码证明具体分支互斥，不能按角色名称或期望用户身份跳过导航改写，也不授真实权限。

`bounded-static-publication@1`只接受实际具名方法与显式原发布证据。IIFE参数/window常量别名、字面量PAGE常量、准确转发、具名const对象多处发布、inline方法以及非目标Object.assign静态扩展均须分别声明并校验；不默认剥除root/window，不执行JS，不接受动态receiver、getter、spread扩展、未知计算键或方法重绑。词法绑定区分真实局部变量/默认参数声明和自由变量写入；未知副作用不外推为一般JS纯度。

菜单和内部/次级路由须准确注册、解析、dispatch、默认view和真实DOM sink。菜单分组、顶层叶子与literal-map属性分别证明，不借其它路由UI。允许有据的纯breadcrumb字面量装饰，不允许page/view/key或权限覆盖。特殊角色互斥上下文为显式`bounded-source-role-exclusion@1`，只消除同一resolver中被实际证明不可能的那一段分支；证明对象必须与真实监控入口guard同一词法函数。

原始候选若为`initiator_only_needs_save_result`，`original_anchor`仍为`bounded-original-anchor-role@1`，额外要求`role_evidence.version=bounded-active-followup-role@1`及`reachability=conditional_only`。原pointer/SHA/target/发起函数完整实现均保留；解码器须验证另一用户保存事件、同id/context/mode/revision的活动表单及真实组件提交结果，并返回实际Token/Call/Evidence而不是自报完成。只开表单、错readonly/新建/编辑、错保存owner、未提交、被重绑或未进入HTML的保存按钮继续拒绝。

本格式证明有限静态条件下的原UI来源及原本地结果语义。它不关闭真实身份、多租户授权、供应商禁呼、稳定实体、迁移或实际保存/执行契约，不自动完成S7，不批准G3或生产，不要求或执行真实系统调用。

静态对象成员的匿名`method:function(id){...}`可显式使用`inline_function:true`，与`inline_method:true`（shorthand）互斥。只能选择该发布对象的确切成员和完整原匿名函数体；成员名作为实现定位标签，不把匿名函数伪造成具名原候选。默认不开通，getter、复制、覆写、错成员或错scope继续拒绝。通过`p4_flow_body`读取实际参数/函数体，原候选终点仍须保持原组件或处理器。

字段库查询的原端点为`render(kind=activeKind)`：只有`execution`中`bounded-field-kind-flow@1/operation=query`完整证明实际字段tab、selector、同作用域状态与无参重渲染后，可返回`verified_default_endpoint`的真实Python Function/Model/Evidence/selector/state Token对象供端点层复核。该对象不从JSON声明透传；复核原`let activeKind='category'`及准确动态默认header。其它动态默认仍待核，旧函数参数规则不放宽。字段family本身也须拒绝把默认改成固定category/field而断开选型链。

账号成员列表绑定坐席的有限入口由`p4r_account_membership_entry`验证：实际`renderAccounts/accountRows`、`ui/esc`原var别名、membershipId投影、原保留行身份的分页副本、常量cloud分支、字面量列＋限定splice/push、仅width/className的保留列map、原table协议及活动body。行内seatAction必须实际进入返回HTML，且其可选片段仅为经真实escape处理的disabled/title属性；实参必须来自membershipId列的value，不用accountId或名字替换。必填render/rows/event/UI/escape/table实现及导出证据。返回实际renderer/入口Evidence/实参Token，后续匿名openAccountSeat及独立AccountSeat保存到原bind结果仍由完整family另行证明；前段通过不等于绑定完成。


### P4 bounded execution flows (conditional source attribution only)

`bounded-active-source-chain@1.source_flow_provenance` keeps `version=bounded-active-source-flow@1`, `reachability=conditional_only`, and `family=execution`. Every function/publication/control/result uses registered file SHA and exact full source evidence. Original candidate key/hash/pointer/target and terminal remain unchanged. The five entry variants are:

- `bounded-application-view-flow@1`: exact original list/create/edit/detail/delete view, original active UI slot/table key, immutable fixed-owner JSON/HTML handler, original component result. Create/edit preserve selected view/id and original scope; deletion binds the real confirmation consumer and its full local result/failure handling.
- `bounded-delegated-exception-flow@1`: original selected system view delegates to the actual published component; query consumes its filters; detail/recheck preserve original record id and context. Recheck binds the actual mounted DOM button, original delayed callback and guarded local result; it is not a redial or business-followup operation.
- `bounded-captured-monitor-flow@1`: exact current snapshot table `cno`, original state-selected pause/unpause or literal logout, eligibility, row identity, captured scope, independently mounted confirmation/save, and actual `manageSeat` receiver/result. It retains busy/pending/unknown and context/identity checks; no real supplier success is inferred.
- `bounded-retry-user-save-flow@1`: exact original retry anchor and failed-create object/type/status guards, actual same-record input layer, independent `createSingle` save, captured record/context/request id and exact `AccountSeat.create` configuration-record argument; the original locked local result/failure handling is bound.
- `bounded-outbound-group-user-save@1`: exact selected create/edit entry, real independent Save and original `editor.kind`/id/context/revision; only the corresponding actual `api.create`/`api.update` branch reaches its published component transaction. Original storage/context/revision/failure behavior remains conditional source evidence.

`flow_steps` is a finite list of complete original named functions: `{kind:'function', role, symbol, evidence}`. Each variant has explicit `entry_binding`, `event_binding` and `result_binding`, including UI implementation/export/sink evidence, actual page/component publication and alias/factory evidence as applicable. Metadata strings never substitute for lexical calls, actual HTML consumers, captures or results. Inert/comment/attribute controls, wrong row key/argument slots, unused/dead functions, unknown receiver copies/writes, captured id/context/mode/result overwrites and decoy publications remain pending.

The explicit monitor `route.resolver_context.version=bounded-source-role-exclusion@1` binds `resolver`, `selector`, monitor `guard`, policy and AppState access/get publications and source literals. The finite original guard requires ADMIN; the actual selector returns a workbench only for distinct OPERATOR. Only the original matched resolver branch interval is excluded. `monitor_frame` must match the returned trusted guard Function/Model to its own `eligible`; unrelated admin metadata/functions cannot establish this premise. Missing/unknown evidence and every other route write remain rejected; legacy P1 behavior is unchanged.

Initiator roles require `original_anchor.version=bounded-original-anchor-role@1`, `role=initiator_only_needs_save_result`, exact unchanged original candidate/hash/source pointer/locator/target binding, and `role_evidence={version:'bounded-active-followup-role@1',reachability:'conditional_only'}`. The role validator calls `validate_active_flow(require_followup=True)`. The family must independently return trusted `followup_verified=True`; an opener or role name alone cannot pass. `result_binding.followup.version=bounded-captured-user-save@1` binds the exact `anchor`, distinct `save` and actual component `result` function evidence plus the independently checked `save_event.evidence`. Internal context/mode and identity/selection/new-object-scope facts must be actual parsed Tokens; commit must be an actual Call inside that save and resolve to the exact published result. JSON capture labels/booleans are not proof. No generic callback execution, arbitrary JS evaluator, candidate-key whitelist, new permissions or production verification is added.

### P4 original reusable-field tab flow

`entry_binding.version=bounded-field-kind-flow@1`, `selected_kind=field`, and `operation=query|detail|create|edit|enable|disable|delete` explicitly consume the original category page and field tab. Required source evidence includes the original category route, actual `BusinessConfiguration.render/selectKind` publication, immutable UI/model aliases, static page wrapper/compatibility registration, literal kind/state/route dictionaries and actual `activeKind` declaration. Complete named `flow_steps` bind the original render, links/select, field-row/columns/action helpers, filtering, open/draw/save/toggle/remove and scope/active guards. The original candidate pointer is `/source_evidence/1`; its page handler remains the terminal. Component pointer0 is not substituted.

The field selector is the real `Object.keys(kindNames)` returned-button path; `selectKind` must preserve the field selection through the actual no-argument page wrapper and `render(kind=activeKind)`. The family always checks this exact default, including when a different literal default would be syntactically accepted elsewhere. `activeKind` admits only its actual initialization, selector and renderer writes. The page wrapper, selected field table/id column, row-action helper, field-only editable/default fields and UI HTML activity are verified from source.

`event_binding.constructor.version=bounded-concat-owner-alias-handler@1` is an explicit finite alternative for the original immutable `ui/esc` alias and fixed-owner JSON/string-concatenation handler; it does not broaden legacy helper recognition. Edit and readonly preview share the original `open` handler but are selected by their exact `kind,row.id[,true]` tuple; they cannot borrow each other's completion. `draw_save_expression_index` binds the exact original active `editor.previewOnly ? '' : <save>` branch. The real independent save immediately rejects preview-only/inactive editors, dispatches the exact literal `{category:model.save,field:model.saveField}` map through the captured kind, values/scope/context/revision, and preserves its actual failure branch. Unknown map/result/capture rewrites or whole-object escapes are not accepted.

Toggle preserves original row id and `!row.enabled`, original scope/revision and selected enable/disable direction. Deletion binds the real UI confirmation callback and original `deleteField`, including preset/reference guards. Component save/delete results bind the actual published model methods and original permission/context/revision/storage mutation behavior. Original interface/production limitations remain open.

Only field query may return a trusted internal `verified_default_endpoint={function,model,evidence,selector_function,state_binding_token}` for the two active-flow endpoint completeness checks. Those values are actual parser Function/Model/Evidence/Token objects, not proof JSON. The common validator rechecks the explicit family/version/query operation, original `render(kind=activeKind)` header, same lexical selector and source state declaration. Legacy `p4_flow_function` and all other defaults retain their prior behavior.

### P4 original queue picker and saved configuration flow

`entry_binding.version=bounded-queue-user-flow@1` is limited to `operation=create|associate|verify-members|edit`. `resource_frame` binds the actual queue page route/publication, original returned qno table/column/pagination and immutable fixed-owner helper. `page_factory_evidence` binds the original zero-argument queue service/context factories. Named `flow_steps`, real opening/save publications, UI toolbar/openLayer slots and original component/transaction evidence are required. No alternative original candidate pointer or terminal is used.

Create retains the original `continueCreate` initiator: its exact validated selected-id snapshot and context are captured before closing the first picker, then the actual `QueueConfig.openCreate` rechecks all selected skills and captures its distinct `groupIds/context/revision/mode=new` editor. Only the real editable footer Save and actual `createForGroups` call/guarded local result establish follow-up completion. The generic initiator role still requires its original candidate/hash/anchor and an independently proven save/result; opening the second form is insufficient.

Association preserves the actual association picker qno, selected/initial ids, context and revision. Its original zero-selection confirmation must call the same guarded `saveAssociation(true)` handler, with the actual mounted confirmation consumer; nonempty saves retain their ordinary path. Verify starts at the real association footer's `verifyCurrent`, rejects dirty selections and invokes the original queue/group comparison path; it never pretends to change returned members. Edit preserves the original `QueueDetail.save` terminal, true edit entry, qno/context/revision, local payload validation, current editability and original storage/failure result.

Conditional method-name/readonly footer projection uses only booleans derived internally from those actual parsed mode/permission captures. Unknown conditions stay opaque, inert/comment/attribute ancestors do not create controls, and proof JSON cannot provide an arbitrary evaluator/binding map. The confirmation helper additionally accepts one proven original guarded call and its zero-argument expression callback; its internal guard is an actual source Call plus exact Token range, not caller metadata. Other callbacks or guard names do not establish completion. Queue detail's original pure `string(value).trim()` reads may exempt only exact editable scalar-value argument Tokens; protected editor qno/context/revision and whole-object writes/copies remain guarded. These are original conditional local/mock operations, not production execution or supplier-contract approval.


### Reporting active source flow

Opt in with `source_chain.version: bounded-active-source-chain@1` and `source_flow_provenance: {version: bounded-active-source-flow@1, reachability: conditional_only, family: reporting}`. The original `entry` event/callee/evidence and exact original named `terminal` symbol/evidence remain required. An original candidate pointer, field, object slot or terminal may not be replaced to obtain a pass. All evidence uses the existing exact file/SHA/line/quote/registered-source contract. No source JavaScript is evaluated.

Supported `profile_kind` values are `task_list_export`, `call_record_export`, `report_drill`, `import_batch_query`, `import_batch_detail`, `workspace_task_control`, and `seat_event_query`. Each has an explicit `entry_binding`, exact `event_binding`, nonempty `flow_steps`, and `result_binding`. Common route/publication/UI contracts are consumed rather than treating a registration or function's existence as a UI entry.

Required profile-specific bindings:

- Task/record exports: actual route/view -> published render -> original returned delegate -> real `toolbar` active HTML slot -> exact callee and fixed literal type (or no argument) -> original complete export handler. The source's own qualification, empty-data and failure behavior remain part of the operation. CSV or adapter results are not production verification.
- Report drills: exact definition/route/view -> known literal metric-array branch -> original card call and fixed metric key -> immutable fixed-owner action helper with String/null + JSON + real HTML escape -> original `drill(kind,key)` capture -> exact `getDrillRows`/`showDrill` table in real `openLayer`. Different report views retain separate original candidates and results.
- Batch query/detail: actual returned list control and real inline table respectively; detail identity is the original `id` field passed through the bounded JSON/HTML argument mapper. Query's original inline published method navigates the same route with `batchId:selectedBatch`; the original renderer resolves `displayBatch(selectedBatch)` and returns the original detail renderer. Query does not imply accurate batch export.
- Task controls: source-bound IVR view/type -> real `taskId` column and preserving pagination/`orderNo` clone -> original opener -> same-ID workspace forwarding/internal route -> original context/task lookup -> real header action slot -> directly initialized `buttons` push/join -> original exact task/action handler. No provider success is invented; original pending/confirmation/failure and local deletion rollback remain in the complete original handler.
- Seat event history: original workbench/monitor embedding and its explicit source-role resolver context -> finite literal-pair event tabs -> actual setter/DOM refresh -> active event log -> exact internal history route -> original `renderEventHistory` and real event table. It remains current-browser/current-tenant local event history, not real-time collection or production-wide history.

Finite accepted syntax is limited to the source-bound literal dispatches, complete original functions/static publications, real UI HTML slots, quoted/template `+` composition, fixed literal metric arrays, direct empty-array `push`/`join`, the original identity-preserving row clone/pagination, and fixed event mappers described above. Direct declarations/bindings and actual consumers are checked. Inert/attribute/comment content, dead branches, unused strings, wrong slots/owners/arguments, unknown copies/forwarding, identity writes and unsupported transformations remain pending. These checks are not a general JavaScript evaluator.

The new active route path may additionally prove the exact invoked top-level leaf projection `if(!group.children) routes[group.key]={...group,page:group.page||group.key,breadcrumb:[group.label]}` together with the complete original child projection. It requires a direct childless literal entry, unique projected keys, the exact guard/projection/invocation, and no conflicting or unknown map/menu writes, including unproved local escapes and writes through actual static public aliases. Old P1 child-only behavior does not become implicitly broader.

`predictive-export-task-customers` is not admitted by these profiles: its only explicit button is in the empty-actual-customer fallback while its CSV handler requires the same actual-customer getter to be nonempty. This is a recorded source result conflict, not a production-interface gap, permanent feature ban or authorization to change the prototype. Existing fake/fixed data, source-only/provider-pending boundaries, permissions and open engineering issues remain explicit. Source attribution does not approve S7/S8/G3 or risk acceptance.


### Original inbound-rule source flow

This is an explicit subdecoder of `bounded-active-source-chain@1`: `source_flow_provenance.version` is `bounded-active-source-flow@1`, `reachability` is `conditional_only`, `family` remains `resource`, and `entry_binding.kind` is `inbound_rule`. The only `profile_kind` values are `query`, `detail`, `create`, `edit`, `enable`, `disable`, and `delete`. The original candidate pointer/field/hash and exact original named terminal remain unchanged. The decoder returns the same five actual Evidence endpoints used by the active-flow gate; it does not approve a product gate or execute source JavaScript.

Required common data includes the exact route/page publication/render/UI alias, `initial_frame`, `service_binding`, actual event/callee/evidence/publication/fixed escaped helper, four source-edge kinds (`initial_frame_to_rule_list`, `actual_rule_control`, `original_identity_context`, `original_local_mock_result`), and the profile-specific original result bindings. `initial_frame` proves the original `inbound-service` route/view -> returned `renderInbound` -> real header action slot -> original `openList` -> `inbound-routes`; internal registration alone is insufficient.

`service_binding.version` is `bounded-extended-mock-service@1`. It requires the original immutable zero-argument factory and exact global receiver, const API object, one literal named-only `Object.assign` extension, direct publication from the actual window IIFE, and complete used member implementations. Unknown/executable/spread/duplicate members, wrong receivers, deferred publication, member replacement, namespace mutation, and unproved API/factory/published-owner copies or forwarding remain pending. Mutable original mock trace/scenario data is distinct from immutable method identity. Source support for the unchanged original component is SRC-2119; this is not a new API contract, permission grant or vendor-readiness approval.

Query proves the original input reads and refresh back to the original result renderer. Row profiles use a real table/row contract and the actual original `id` field plus current service `key`; the name column is explicitly allowed to carry `r.id` rather than its displayed name. Detail additionally retains the original `result_expression_noncallable` header anchor role and the installed read-only result contract, requires `readOnly=true`, and proves the original immutable field/fallback title reader without permitting unknown whole-row escapes. A role-only success never counts as full operation admission.

Create/edit prove their original distinct opening arguments, original captured `id`/context/local expected revision/read-only state, real main layer and user save button, original save guard/submit lock, exact service-save argument slots, unchanged post-result context guard and failure feedback. The local revision is not introduced as a supplier API field. Enable/disable use only the original finite `Number(row.active)===1?2:1` source with active values 1/2 and the original manage/nonpending conditions. Delete additionally follows the original disabled-row branch. The original confirmation helper must create its real active button, bind its ID, and consume the supplied callback; callback id/state/context arguments cannot be substituted.

Mutation results bind the original service method to the actual outer `write` call while retaining its exact nested id/active/context payload. The original local persistence, failed/unknown result and pending-journal behavior remain explicit. All seven results are original Mock-only/local prototype semantics; they do not establish vendor execution, production storage, real permissions, or accurate remote data. This source-flow profile does not add business fields, CRUD beyond the source, API families, role assignments, or a new workflow.


### Resource active source flow (P4 continuation)

This is an explicit `bounded-active-source-chain@1` opt-in. It adds no default JavaScript interpretation and changes no original candidate, observation pointer, operation, permission, endpoint or production fact. Every branch below is conditional static source attribution; source JavaScript is never evaluated.

`source_flow_provenance` must retain version `bounded-active-source-flow@1`, reachability `conditional_only`, family `resource`, explicit `entry_binding`, ordered and consumed `flow_steps`, `event_binding`, and `result_binding`. The original `source_chain.entry` event/callee/evidence and original terminal remain mandatory. The terminal never changes from an opener to a save function to make a proof pass.

Resource entry uses `version: bounded-resource-entry@1` and one finite `kind`:

| kind | Actual source responsibility |
| --- | --- |
| `table_direct`, `table_row_helper`, `table_scalar_helper` | Published route renderer, actual returned table/forwarding wrapper, exact literal/local const column slot and key, selected real event and row identity; helper HTML must actually be returned into active HTML. |
| `query_result` | Original query control, original filter input consumption, and same route/current-route DOM render result. `bounded-current-query-return@1` supports only the actual literal navigation, finite literal selector/named delegate, and current-route refresh shapes. |
| `form_mode` | Original literal view/create/edit/remove/import argument, captured object/context/version, actual layer, independent original user save and actual same-mode result. The pool/extension/time variants are finite source shapes, not a general form engine. |
| `provider_account` | Original account configId/context/expectedVersion capture, original save or status confirmation, actual published local transaction. Credential state remains a status only. |
| `skill_form`, `agent_form` | Original scalar editing ID/context or seat/skill/phone dialog capture, actual user save, original failure and partial-result responsibility. Skills can remain pending; creating a seat and then failing an optional skill assignment is not rewritten as atomic rollback. |
| `component_import` | Original number wizard or existing-seat/batch-seat/verification mode, actual native selection/precheck, real layer-owned DOM, original submit/verification result. Number validation and seat per-item/unknown outcomes retain their original different semantics. |
| `account_bind` | Actual membershipId row control, same member account/tenant/enterprise, existing-association layer, native identity selection, independent submit and original identity lock callback/local bind result. PAGE-2003/ACTION-2091 is the actual carrier; unused workbench binding HTML is not completion coverage. |

Table content uses `bounded-resource-table@1`. It requires original selected `column_source_index`/`column_key_literal`, real table implementation/export, actual row input and actual pagination when present. A named wrapper must preserve its exact rows/columns argument slots into the actual table. Finite conditional literal-array spreads cannot replace the selected column. Duplicate display keys do not select a column implicitly. Distinct display/identity fields require explicit `identity_kind: row_field_distinct_display` and matching `row_identity_field`, and the source must pass `row[field]`, never the unrelated displayed value.

Events use `bounded-resource-event@1`, exact event/callee/evidence, and actual identity/literal argument slots. The new family can prove the original immutable multi-const fixed-owner escaped/JSON helper, and literal owner constants through explicit `constant_keys`; it does not loosen old P2 defaults. `bounded-guarded-field-array-escape@1` permits only its explicit original readonly, type/nonempty-guarded field-array display map of the already-proved pure UI escape function. Unproved helper/copy/callback uses remain pending.

`mode_import_followup` requires the original `initiator_only_needs_save_result` anchor role and `bounded-captured-user-save@1`. The common contract receives actual parsed Token/Call/Function objects proving context, mode, selected IDs and commit receiver; JSON role labels alone cannot authorize a followup. Other unsupported role/structure combinations remain pending.

Selection and stage output must be active in the real original HTML context. Native input direct `hidden`, `inert`, or inline hiding styles are rejected along with inactive ancestors. A selected stage footer must retain its actual assignment; later/unknown competing writes are rejected, while writes proved to belong to another literal stage remain outside the selected path. Original no-op, cancellation, stale scope/version, unavailable browser lock, storage failure, partial/unknown receipt and post-write display-failure limits are preserved. No general rollback, production success, interface authority, new capability or authorization is inferred.
