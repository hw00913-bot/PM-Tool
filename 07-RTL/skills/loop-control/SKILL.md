---
name: loop-control
description: "Run RTL as Master. The explicit start request 使用 rtl 开始规划项目 opens S0 project planning. Use for request routing, dispatch, pause and resume, rechecking an existing candidate, or final acceptance."
---

# 主控调度与验收

Master 负责选择下一步、记录决定、检查真实产物和证据。场景 Agent 负责场景改动，Integration Agent 负责暂存和提升，独立验证 Agent 负责必需的非作者验证。

以下命令从 Loop 工具目录执行，省略 `python3 -m loop --data DATA` 前缀。大写参数取自本轮台账。完整参数和证据上下文见[运行手册](../../docs/runbook.md)。

## S0 · 显式启动与确定本轮身份

新规划的启动口令是 **「使用 rtl 开始规划项目」**。用户将其作为当前请求发出时进入 S0，先梳理目标、输入材料和已有确认，列出缺项。文档引用、口令讨论或工具维护沿当前任务处理。明确的续跑请求按指定运行接管。

1. 先确定用户授权的独立项目绝对路径、最终 `index.html`、既有内容和本轮验收要求。工具目录、持久数据目录和业务项目分别存放。读取 `runs` 和匹配运行的 `status RUN`，核对稳定项目编号、实际输出路径和活动运行。
2. 新项目或上一轮已交付时，先明确落盘路径和已有内容处置，再走[需求冻结与派发前核对](#需求冻结与派发前核对)。默认入口是 `PROJECT/prototype/index.html`。同项目已有活动轮次时转 [loop-recovery](../loop-recovery/SKILL.md)，接回原运行。迁移与副本先确认是重新绑定原项目还是独立建项。
3. 本轮开始后用 `skill loop-control --role master --stage S0 --run RUN --epoch EPOCH` 读取并记录当前 Skill。后续调用使用实际阶段与最新有效代次。Skill 更新从下一次调用起生效。冻结的业务基线和硬门禁按台账及执行模块核对。

新项目采用内置 React + Umi + Ant Design 底座，并将 `frontend` 写入本轮基线。显式使用原生静态模式时填写 `static`。续跑按已登记的技术栈核对。

**放行记录**要能定位 `project_id`、运行编号、绝对项目和落盘路径、基线修订、当前代次、候选。路径不明、被占用或旧成果处置未确认时，停在本阶段。

## 按用户当前请求选择入口

对已有项目先根据用户所指的项目、运行或交付入口，用 `runs` 和 `status RUN` 找到对应对象。只有一个匹配对象时沿用。多个对象都可能符合时，请用户指出运行或入口。入口分类参考 pstack 的按意图路由。 [pstack Playbooks](../references/pstack-workflows/SKILL.md#playbooks)

| 用户的请求 | Master 的下一步 |
|---|---|
| “继续”“先停一下” | 对活动轮次按 [暂停与续跑](../loop-recovery/SKILL.md#整轮暂停与续跑) 处理。继续前核对现场和原操作，接回就绪的剩余工作。 |
| “再验一遍”“复验交付” | 对已选定的同一候选或交付成品[只读复验](#只读复验)。明确要求继续待验交付时，回 S4 按正常流程处理。 |
| “这个页面有问题” | 按[专项调用](#专项调用)安排 Verifier 从用户所指的真实入口复现，核对现行用例，再按[回派规则](#回派与人工确认)定负责人。重复出现的问题按[重复缺陷的处理](../../docs/runbook.md#重复缺陷的处理)定位。 |
| “改这条业务规则”“加一个功能” | 由[需求架构](../requirement-architecture/SKILL.md)核对现行基线和拟议变化，列出受影响的场景、契约和用例。用户确认后按[活动轮次替代](../../docs/runbook.md#已确认变更替代活动轮次)登记决定并开始新轮。 |
| “这个交互能不能这样做” | 能通过观察回答的疑点先做[小范围验证](../requirement-architecture/SKILL.md#按需验证技术或交互疑点)。仍需决定的业务取舍交用户确认。 |

## S1–S3 · 选择可以推进的工作

每次从 `status RUN` 重算就绪情况，结合场景交接、当前契约和实际宿主状态。首次验证新执行方式时，先用一个独立场景走通派发、自测和合入。已核对就绪的任务按写入范围和执行位安排。将实现者简报和非作者检查绑定到具体结果。 [pstack Orchestrate](../references/pstack-workflows/SKILL.md#orchestrate)

| 下一步 | 放行条件 | 执行动作 |
|---|---|---|
| 设计或接口协商 | `requirements-check RUN` 已核对 S1 快照和任务，最小底座可用。共享接口可以尚未激活。 | `claim RUN TASK --phase design --epoch EPOCH`。 |
| 依赖接口的实现 | 任务所声明的 `edges` 均已激活，硬依赖 `deps` 已集成，写入范围可用。 | `claim RUN TASK --epoch EPOCH`。确认使用的是返回的契约版本和工作区。 |
| 跨场景联验 | 提供方与使用方的实际提交均可定位，自测有效。 | 按[暂存联验与提升](#暂存联验与提升)安排同一候选和独立验证。 |
| 滚动合入 | 实际待合入结果和父版本仍匹配，所需非作者证据有效。 | 由 Integration Agent 运行 `promote RUN MERGE_OP --epoch EPOCH`，回交提升回执。 |

派发前按 [需求架构 Skill](../requirement-architecture/SKILL.md) 核对依赖类型。需要同批联验的共享接口使用 `edges`，避免把同一组任务误设成等待上游已合入的硬依赖。

### 需求冻结与派发前核对

1. Master 交需求架构师按 [需求架构](../requirement-architecture/SKILL.md) 产出 S1 需求分析。业务范围、规则、验收预期有疑点时，由 Master 组织用户确认。待决事项解决后，确认本轮是否要求人工验收，用 `s1-confirm` 将 `human_acceptance` 与确认出处一并登记，再安排架构师完成场景、功能和任务三步并登记交接。任务交接和初始化沿用该值。
2. Master 用 `skill loop-control --role master --stage S1` 读取跨产物检查小节，沿同一需求修订核对场景、功能、任务与用例。缺失或冲突回对应步骤。按已确认的项目与落盘位置运行 `init --project PROJECT --baseline BASELINE_JSON --s1-record S1_RECORD --suite SUITE_JSON`，多份冻结套件逐份传入，独立交付目录带 `--output OUTPUT`。用 `requirements-check RUN` 核对实际冻结快照、交接回执和计划任务后再派发。 [Spec Kit 跨产物检查](../references/requirements-workflows/SKILL.md#check-traceability)
3. 已登记运行的初始化中断交 [恢复对账](../loop-recovery/SKILL.md#2-对账与下一步)。冻结后改业务范围、规则或验收预期，由用户确认新修订，再按 [活动轮次替代](../../docs/runbook.md#已确认变更替代活动轮次)处理原运行。新派发核对本轮冻结快照和尝试绑定。被替代的依据只供变更对照、历史追溯和原版本复验。

### 契约确认与改版

1. 首次建立共享接口、接口改版或实际组合不匹配时，Master 安排提供方和使用方依据 [契约规范](../contract-coordination/SKILL.md) 对齐同一契约边。双方分别对该边的确切版本执行 `contract-confirm`。Master 核对双方确认后执行 `contract-activate`，再派发依赖该版本的实现。
2. 改版前处理未补记的合入或验收意图，确认受影响的在途设计和实现已暂停或隔离。该边首次激活时，可沿用尚未绑定版本的设计。后续改版时，涉及该边的无版本或过期设计交接失效，仍需设计交接的任务按当前契约重新登记。激活后对照 `affected` 和 `invalidated` 回派或复验。新实现按派发绑定的当前激活版本执行，旧结果与旧证据保留原版本身份。源码原样且已有成功合入依据的任务可安排仅复验。沿用已集成的一方须核对新契约声明的实际兼容组合及相应联验证据。依赖与授权范围仍满足的独立场景可以继续。
3. 对齐技术接口和兼容策略时，核对实际提供方、使用方及提交。涉及业务规则、权限或验收预期的变动，先走[需求冻结与派发前核对](#需求冻结与派发前核对)的新修订流程。实际命令参数见 [契约与派发](../../docs/runbook.md#派发契约和提交)。

### 暂存联验与提升

1. Master 按可合入尝试及共享改动的影响范围安排 [Integration](../prototype-integration/SKILL.md) 暂存。接收操作编号、父版本、实际候选和入口、提交作者及自测证据。对照提交回执的 `revalidation_of`、暂存回执的 `required_cases`、`required_edges`、`retest_cases`（有值时）及本候选已登记的失败，确认本次联验和补查范围。涉及共享改动或风格时，分别按[专项调用](#专项调用)和[风格技能的调度](#风格技能的调度)安排。
2. 关键场景、共享状态、契约变更、仅复验、显式补查及已有失败的重验，由 Master 安排 [Verifier](../prototype-verification/SKILL.md) 对精确暂存候选取证。低风险独立任务进入仅复验时也在此列。核对作者身份，包括原作者、仅复验人员和适用的继承来源，相关人员交由其他验证者取证。契约联验覆盖实际提供方与使用方的版本组合。对低风险独立变更按本轮已有证据要求处理。
3. 对照同一候选和父版本的证据安排 Integration 提升。发现有效失败先留存证据，按[回派规则](#回派与人工确认)处理。源码修复或父版本推进后重新暂存并验证受影响项，已有失败用原冻结用例复验。实际候选已改变而回执待补时，转[恢复对账](../loop-recovery/SKILL.md#2-对账与下一步)。

### 专项调用

| 观察到的工作 | Master 安排的 Skill |
|---|---|
| 共享路由、外壳、主题、租户上下文或持久化改动 | Integration 按当前 S2 或 S3 读取 `prototype-integration --role integration --stage STAGE --purpose shared-change`，给出影响范围及补查项。 |
| 缺陷复现或修复复验 | Verifier 按当前 S2、S3 或 S4 读取 `prototype-verification --role verifier --stage STAGE --purpose defect`，对实际入口和候选留证。 |
| 功能地图、验证步骤或测试数据与现状不符，需专项核对 | Verifier 在 S3 或 S4 读取 `prototype-verification --role verifier --stage STAGE --purpose maintenance`，提供差异证据及影响范围，按[回派规则](#回派与人工确认)处理。 |
| S2 的派发、接管或停工门禁变更，或重复故障涉及不同事件顺序 | Master 读取本地 [statechart](../statechart/SKILL.md)，用 `skill statechart --role master --stage S2` 记录调用并检查状态转移。 |
| S3 的契约改版与迟到结果、候选提升和恢复可能交错 | Master 安排 Integration 或 Verifier 读取本地 [statechart](../statechart/SKILL.md)，用对应角色和 `--stage S3` 记录调用。 |
| S4 的落盘、验收回写或回退门禁发生变化，需检查中断重试与路径占用 | Master 或 Verifier 读取本地 [statechart](../statechart/SKILL.md)，用对应角色和 `--stage S4` 记录调用。 |

用 `skill` 执行表中的读取参数。活动轮次带 `--run RUN --epoch EPOCH`。复验已交付成品按[只读复验](#只读复验)读取与取证。

### 风格技能的调度

Master 按实际工作安排有授权的角色读取 [设计与视觉检查规范](../prototype-design-style/SKILL.md)。执行角色用 `skill prototype-design-style --role ROLE --stage STAGE --run RUN --epoch EPOCH` 取得当前正文。场景工作可带 `--task TASK` 关联本轮任务。

| 调用时点 | 执行角色 | 交接 |
|---|---|---|
| S2 建立共享底座或更新共享壳、主题时 | Integration Agent（`integration`） | 提供已确认的风格、功能地图和样板范围。将项目风格基线与实际样板入口交给场景任务。 |
| S2 设计场景页面时 | 场景 Agent（`scene`） | 随任务简报交付风格基线。将场景方案和视觉状态说明用于合入交接。 |
| S3 复核合入风格时 | Integration Agent 或独立验证 Agent（`verifier`）。关键共享改动由未参与实现的 Verifier 复核。 | 提供实际待合入入口、候选、风格版本及受影响页面。视觉结论随候选交回。 |

局部视觉偏差回场景 Agent。共用导航、主题、样式或组件偏差回 Integration Agent。Master 根据实际使用关系确定复验范围，安排在修复后的新候选上复核。视觉证据和业务行为证据分别交回，按本轮已有放行条件核对。

用户另指定风格，或拟改已确认的设计方向、状态表达或关键页面结构时，由 Master 组织确认，登记风格版本或例外，再推进受影响工作。基线内的组件复用与小幅排版由执行角色处理。

### 派发简报和宿主交接

S2 调用本 Skill 时会带入实施者简报。S3 补派或回派前，在 `skill loop-control --role master --stage S3` 调用加 `--purpose dispatch` 取得同一小节，再用 `claim RUN TASK --epoch EPOCH --stage S3` 登记新尝试。准备或恢复同一尝试时沿用其登记阶段。 [Superpowers 实施者简报](../references/superpowers-workflows/SKILL.md#implementer-brief)

每次领取先产生意图与工作区。把 `claim` 或 `prepare-attempt` 返回的 `skill.content`、`style_skill.content` 和以下任务事实一起交给执行宿主。自动 Codex 派发在台账关联两份 Skill，项目风格文件随风格正文传入。已有设计交接时，将实现尝试的 `input.design_handoff` 随简报传入。

- **目标与完成判据**　业务结果、范围及必验用例。例：C 保存工作台布局并在刷新后恢复，验收用例为 `TC-C01`。
- **定位与权限**　任务和尝试编号、所覆盖的功能编号、S1 契约修订和快照链接、工作区绝对路径、基础提交、允许写入范围、上游结果链接，以及使用的风格和接口契约版本。
- **预期回执**　设计用 `submit --result SUMMARY_JSON` 登记非空设计结论和待决项。实现交实际改动范围、自测证据和阻塞，源码以 RTL 登记的 Git 提交定位。报告应携带尝试编号和宿主身份，供主控对账。

普通宿主由 Master 实际派发，观察到尝试启动后用 `start RUN ATTEMPT --worker WORKER --host-ref HOST_REF --epoch EPOCH` 登记。使用本机 Codex App Server 时，领取后用 `codex-start RUN ATTEMPT --epoch EPOCH` 派发，使用返回的执行身份收件。RTL 核对成功轮次及工具停工证据后生成或登记提交。CLI 的角色字段和执行者标识用于协作核对。 [Codex 执行](../../docs/runbook.md#通过-codex-执行场景)

若场景需要等待，先按 [恢复对账](../loop-recovery/SKILL.md#2-对账与下一步) 核对实际停工。暂停登记后释放执行位并保留原尝试及其实现写入范围。输入仍有效时按原阶段领取续跑。确需替换时按恢复流程撤销旧尝试资格。空出的执行位可派给依赖、写入边界和所需证据均有效的独立场景。

## S4 · 决定交付

1. 从台账确认本轮任务已集成，使用 `map-check RUN` 核对功能地图与候选，交付意图写入前再核对用户确认的路径。React 候选还须有对应的构建清单。使用 `land RUN --epoch EPOCH` 后，记录实际入口和交付操作编号，状态为 `pending_verification`。
2. 交独立验证 Agent 从实际落盘目录完成 [功能地图实走与业务验证](../prototype-verification/SKILL.md#功能地图实走与故障归因)。查看当前落盘候选在各验收浏览器的实际入口、功能标记、冻结业务断言和原始报告。只有暂存证据时继续待验。
3. 依据本轮规则裁决。执行 `finalize RUN DELIVERY_OP --actor MASTER --epoch EPOCH`，需要人工验收时同时给出已确认的 `--approval REFERENCE`。核对实际回执、候选、入口、证据编号和变更编号。新轮核对需求台账和 CHG 的功能点双向链接，再汇报已交付。React 项目按 [Coding Agent 接手](../../docs/runbook.md#coding-agent-接手)移交回执中的源码与成品。准备交接时，在 S4 的本 Skill 调用加 `--purpose delivery-handoff` 取得接手小节。操作中断后交 [loop-recovery](../loop-recovery/SKILL.md)。 [pstack Session pickup](../references/pstack-workflows/SKILL.md#session-pickup)

已登记验收意图的续跑沿用原依据。传入不同的 `--approval` 会停止本次操作。用户确认更换依据后，若原意图尚未回写，用 `cancel-acceptance RUN DELIVERY_OP --epoch EPOCH --reason REASON` 撤销原意图，再按新依据验收。已部分回写时按 [恢复对账](../loop-recovery/SKILL.md#2-对账与下一步) 核对原意图和现场。已交付回执按历史记录查阅。

替换落盘被驳回且需恢复上一份已验收交付时，按 [恢复对账](../loop-recovery/SKILL.md#2-对账与下一步) 执行原交付操作的落盘回退并核对回执。

### 只读复验

1. 锁定运行、候选提交、实际入口和原冻结用例。复验已交付成品时，优先从 `DATA/projects/PROJECT_ID/runs/RUN/deliveries/DELIVERY_OP.json` 读取原回执和成品清单。只有运行或操作待定位时才查询 `status RUN`。按交付清单（台账中的 `payload.manifest`）核对实际目录的文件相对路径及 SHA-256。复验未落盘候选时，使用该提交现有的暂存入口或已登记的构建入口，并核对实际提交及文件。若同一路径已被其他版本替换，报告原对象无法在该路径复验，等用户指定要验的对象。
2. 交验证 Agent 使用原用例及数据修订，从目标的实际 `file://` 入口执行 `browser ENTRY SUITE REPORT`。在原型、源码与运行台账以外的验证目录使用新报告路径。复验后再次核对产物仍对应同一候选。无法证明版本或用例相符、缺少原有可运行产物时，报告具体阻塞。
3. 报告附候选、入口、通过与失败项及新报告路径。保留原报告、交付回执、项目变更记忆和台账。只读复验使用无运行关联的 Skill 读取方式和独立浏览器报告。待验交付需要正式补证或裁决时走 S4。已交付成品出现差异时先核对原成品和证据，确认需修正后按本次变更进入当前流程，以新候选复验。

## 回派与人工确认

| 观察到的情况 | 回派 |
|---|---|
| 场景实现或自测问题 | 回场景 Agent。指出失败用例、版本、实际结果和需要重验的范围。 |
| 共享接口不一致 | 回提供方和使用方，由 [contract-coordination](../contract-coordination/SKILL.md) 对齐版本和联验证据。 |
| 父版本或组装问题 | 回 Integration Agent。修复产生新的待合入结果，重验受影响部分。 |
| 尝试过期、契约输入变动或旧执行仍占用 | Master 核对实际宿主、有效尝试和绑定输入。按 [恢复对账](../loop-recovery/SKILL.md#2-对账与下一步) 处理旧尝试。旧执行仍在途时先确认停工或隔离，再按有效输入重派。 |
| 写入范围冲突或提交越界 | Master 对照冻结任务、实际变更与写入占用。写入已被有效尝试占用时等待释放，期间可安排其他就绪任务。提交越界时按原授权范围修正。需要重划范围时交需求架构师提出任务调整，按冻结任务及已有确认边界重新登记或回到 S1。 |
| 风格偏差或例外 | 按[风格技能的调度](#风格技能的调度)回派、复验或组织确认。 |
| 功能地图的入口、关联或说明过时 | 回需求架构师按授权修正地图，复核受影响功能。 |
| 验证环境、选择器、测试步骤或数据异常 | 回对应环境、用例或数据负责人。保留阻塞和实际运行记录。 |
| 浏览器范围或验收预期不明确，或本轮要求人工验收 | 由 Master 核对已有约定。需要用户决定时组织确认并登记依据。 |
| 业务规则、范围或验收预期有变化 | 回 [requirement-architecture](../requirement-architecture/SKILL.md)，先由用户确认再冻结新修订。 |

同一成因的缺陷有重复证据时，在 S2、S3 或 S4 的本 Skill 调用加 `--purpose repeat-defect`，对照旧报告定位共因，再按 [运行手册](../../docs/runbook.md#重复缺陷的处理)登记回派与复验。 [pstack Correct](../references/pstack-workflows/SKILL.md#correct)

用户确认首次或变更后的落盘路径、旧成果处置、业务取舍和本轮约定的人工验收。迁移、副本或目标所有权无法由现有记录确认时，由 Master 带核对结果组织用户决定。机械性恢复及按现行基线回派由 Master 处理。汇报时区分已集成、已落盘待验和已交付，并列出真实阻塞及其责任人。
