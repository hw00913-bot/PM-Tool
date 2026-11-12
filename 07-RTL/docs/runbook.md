# RTL 运行手册

从工具目录运行 `python3 -m loop`。查询或写入运行台账时，在子命令之前给出 `--data <绝对路径>`，或为本次会话设置 `RTL_DATA_DIR`。工具源码、运行数据和独立原型项目分别存放。以下命令里的大写参数为本轮已有记录的值。

## 启动与续跑

**显式启动口令**　「使用 rtl 开始规划项目」。用户直接提出该请求时，Master 先进入 S0。读取资料、核对现有项目和收集待确认项属于规划。项目路径、验收基线和既有成果处置明确后，再按下面的条件初始化。

已有项目收到「继续」「再验一遍」「改规则」等请求时，先按 [Master 请求分流](../skills/loop-control/SKILL.md#按用户当前请求选择入口) 选定运行和操作。「先停一下」和跨会话恢复按 [暂停与接手](../skills/loop-recovery/SKILL.md#整轮暂停与续跑) 核对实际状态。

1. 读取 [AGENTS.md](../AGENTS.md)、[功能地图](feature-map.md) 和 [Skill 清单](../skills/registry.json)。Master 先确认原型项目绝对路径、交付目录、既有内容、冻结基线及真实浏览器范围。
2. `python3 -m loop --data DATA runs` 列出已有运行。已有活动轮次时，使用 `status RUN` 检查任务、尝试和未完成意图。接管时使用 `takeover RUN --epoch EPOCH`，并使用返回的新代次执行后续写入。
3. 新一轮使用 `init --project PROJECT --baseline BASELINE_JSON --s1-record S1_RECORD --suite SUITE_JSON`。有多份冻结用例文件时逐份重复传入 `--suite`。默认输出是 `PROJECT/prototype`，另有确认目录时使用 `--output OUTPUT`。交付目录与需求台账、设计规范、功能地图、变更记录及其所在的受管目录保持分离。该操作为项目建立 Loop 管理的源码 Git 仓库，后续在隔离工作区开发。静态模式导入 `PROJECT/prototype`。若台账已登记而基础导入中断，使用 `prepare RUN --epoch EPOCH` 续做。项目迁移经确认后使用 `rebind PROJECT_ID --project PROJECT --output OUTPUT`，活动轮次同时给出当前代次。RTL 登记新旧路径，沿迁移记录定位旧回执。替换迁移后的成品仍须显式授权，并核对新位置与原清单完全一致。
4. 通过 `skill SKILL_ID --role ROLE --stage STAGE` 读取角色入口和适用参考。将返回的 `content` 交给执行者。关联本轮时带 `--run RUN --epoch EPOCH`，按实际任务筛选时带 `--task TASK`。用返回的 `operation_id` 在 `status RUN` 的操作记录中查主文件及每个已加载小节的路径、章节和 SHA-256。台账保存这些标识，正文每次从本地读取。

接管、整轮暂停后续跑、契约改版回派或 S4 补证与回退时，可用 `python3 -m loop --data DATA status RUN --next` 读取简要引导。它从同一份只读台账快照返回当前代次和建议，S4 同时按原验收规则读取相关报告归档。`argv` 是可用命令的参数数组，`command` 是对应的 Shell 显示文本。先完成 `checks` 中列出的宿主、Git、目录、证据或人工确认核对，再选择适用的命令。缺少必要输入时，结果只给出核对事项。写入操作执行时仍走原有门禁。

### 现行依据与历史记录

- `runs` 和 `status` 直接读取历史台账。首次打开写入会话时，RTL 在单一 SQLite 事务中补齐已知表结构并登记 Schema 版本。升级保留原始基线、证据、契约和回执内容。遇到未知版本或不匹配的旧表结构时，先核对台账来源与工具版本。

- 业务规则和验收预期来自本轮已确认并冻结的需求契约、四步交接和用例。`requirements-check RUN` 核对任务及冻结快照，`map-check RUN` 核对当前功能地图。
- 接口契约按 `status RUN` 中的 `active` 标志及当前尝试绑定核对。旧版本保留其原始确认、适用范围和证据关联。跨版本组合依据本轮激活契约明确列出的兼容范围和实际联验证明。需要恢复历史条款时，以现行契约为依据另提新版本并由双方重新确认。
- 仍在活动的既有轮次沿用其冻结输入，补齐适用的执行与恢复证据。已替代或已交付的记录用于查询、继承源码和按已登记意图恢复。启动新轮使用本轮确认、任务及验收输入，继承来源另用 `--from-run` 指定。

默认调用按登记的角色、阶段和 S1 步骤加载小节。遇到特定工作，再加对应的 `--purpose`。

| 用途 | 入口 |
| --- | --- |
| `prototype` | 需求架构师在 S0 或 S1 做隔离小试验 |
| `pause` | Master 在恢复入口建立安全停点 |
| `dispatch`、`delivery-handoff`、`repeat-defect` | Master 在 S3 派发、S4 移交或 S2 至 S4 处理重复缺陷 |
| `shared-change` | 见 [主控专项调用](../skills/loop-control/SKILL.md#专项调用) |
| `defect`、`maintenance` | Verifier 复现缺陷或在 S3/S4 维护验证 |

调度或恢复问题取决于事件交错时，按 [主控专项调用](../skills/loop-control/SKILL.md#专项调用) 读取本地 [Statechart Skill](../skills/statechart/SKILL.md)。例如在 S2 运行 `python3 -m loop skill statechart --role master --stage S2`。关联活动或已暂停轮次时，给出 `--data DATA`、`--run RUN` 和当前 `--epoch EPOCH`，保存本次 Skill 调用回执。

新项目默认使用 `react`，从 `PROJECT/prototype-src`、同项目最近已交付的 React 源码或内置模板建立基线。原有项目按台账记录的技术栈开始新轮。更换 `--data` 时，RTL 根据已核验的项目交付历史识别技术栈。没有交付历史但已经存在原生 `prototype/index.html` 的项目默认采用静态模式。可用 `--frontend static` 或 `--frontend react` 指定。提供的基线如果也填写了 `frontend`，两处须一致。

基线 JSON 包含已确认的四步产物和验收清单。把项目产物按 [四步输入示例](../skills/requirement-architecture/references/requirements-example.json) 保存为 `PLAN_JSON`。以下命令用该计划、实际用例文件和已确认的 `CONTRACT_JSON` 生成基线，沿用契约修订及人工验收要求。使用多个场景套件时，每份都登记摘要及其中的全部编号。

```sh
python3 - PLAN_JSON SUITE_JSON CONTRACT_JSON BASELINE_JSON <<'PYTHON'
import hashlib, json, sys
from pathlib import Path
plan_path, suite_path, contract_path, output = sys.argv[1:]
plan = json.loads(Path(plan_path).read_bytes())
contract = json.loads(Path(contract_path).read_bytes())
suite_bytes = Path(suite_path).read_bytes()
suite = json.loads(suite_bytes)
cases = [case["id"] for case in suite["cases"]]
baseline = {
    "revision": contract["revision"],
    "mandatory_cases": cases,
    "fixture_revision": suite["fixture_revision"],
    "suite_revision": suite["suite_revision"],
    "suite_digests": {hashlib.sha256(suite_bytes).hexdigest(): cases},
    "acceptance_browsers": [suite.get("engine", "chromium")],
    "human_acceptance": contract.get("human_acceptance", False),
    "requirements": plan,
}
Path(output).write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PYTHON
```

新轮须登记用例及测试数据修订，登记实际 JSON 文件的 SHA-256。每份可运行的全量或场景用例文件分别登记摘要与完整用例编号，每个已确认的验收浏览器都须覆盖所有必跑项。用 `acceptance_browsers` 登记浏览器引擎，默认 `chromium`。`init` 读取实际文件，校验摘要、用例编号和约定断言的实际步骤。S2 自测、S3 联验和 S4 验收都检查实际执行的用例文件。同一项目的需求修订号和用例修订号分别绑定已确认的业务内容和用例摘要。已冻结轮次的编号通过 `memory/requirements-revisions.json` 跨台账保留，包括后来已替代的轮次。运行数据来自该用例文件或已绑定的候选成品。配置 `human_acceptance: true` 的版本需要已确认的人工验收依据。

### S1 需求契约与需求台账

需求架构师按 [四步交接规范](../skills/requirement-architecture/SKILL.md) 先读取 `analysis`，在同一份 `S1_RECORD` 上记录读取。将分析产出的 `revision`、`requirements`、`pending` 和本轮是否要求人工验收的 `human_acceptance` 保存为 `CONTRACT_JSON`。基线及 `tasks` 交接使用同一布尔值。待决事项解决且用户确认后，由 Master 记录确认依据。

```sh
python3 -m loop skill requirement-architecture --role architect --stage S1 --step analysis --s1-record S1_RECORD
python3 -m loop s1-confirm --project PROJECT --contract CONTRACT_JSON --approval '实际用户确认出处' --s1-record S1_RECORD
```

随后依次读取 `scenes`、`features`、`tasks`。每次带 `--s1-record` 读取时，评审记录同时登记所加载的小节，随后随本轮冻结快照保存。每步完成后执行 `s1-handoff --s1-record S1_RECORD --step STEP --artifact BASELINE_JSON`，记录该步规范化产物的摘要。每步产物汇入基线的 `requirements`，`approval` 沿用确认出处。RTL 检查四步顺序、待决事项、启动契约和实际交接产物。修改上游产物后，重新登记该步及受影响的下游步骤。每次新轮用本次确认记录，已有确认已被轮次使用时重新登记。新轮 `init` 登记任务，在项目 `memory/baselines/rtl-s1-RUN.json` 冻结本轮版本，在 `docs/需求台账.md` 生成可读索引。用 `requirements-check RUN` 核对契约、四步交接和实际任务。领取、启动和提交均会核对原冻结快照及生成的台账。初始化若在 SQLite 登记前中断，重试原 `init`，默认运行编号随同一确认记录保持稳定。SQLite 已登记后的中断或活动轮次台账丢失，用原 `prepare RUN --epoch EPOCH` 补齐。现有人工维护的同名台账需要先迁移，工具会保留该文件并报出冲突。

新轮在 `requirements.features[].assertions` 填写用例编号、精确断言和 `checkpoint`。S4 核对归档报告的实际用例、步骤下标、此前的操作顺序和功能入口。业务修改或移除时，在对应变更的 `assertion_changes` 登记前后实际断言，并把变更行放入 `s1-confirm` 输入的 `change_assertions` 供用户确认。旧断言以已交付基线核对，新断言对应本轮冻结的验收步骤。变更涉及的断言用例使用新冻结的用例文件。复用同一预期时，在同一变更条目登记 `assertion_reuse` 的确认依据和理由。

需求台账和每次验收的 `CHG` 双向链接，均可查到本轮冻结快照。S4 按冻结记录中从需求规则、场景与断言得出的差异回写，区分业务变更、技术调整和仅复验。功能地图沿用五列。迁移到新追溯基线时，沿用适用的功能和用例编号，将当前地图场景列更新为本轮业务场景编号。初始化和派发会预检已存在的地图关系，S4 再核验成品路由。移除最后一个功能时保留表头，正式验收仍要验证可打开的原型入口及移除用例。跨台账通过项目的 `memory/requirements-history.json` 定位原台账及冻结快照。早期已交付记录在首次写入时迁移。保留原运行台账、CHG 明细和冻结快照，历史缺失或无法核对时先恢复对应记录。

### 从 04 冻结包接续 S1

选择 04 已生成的版本化冻结 ZIP，或包含清单、交接文件和内容的已解包目录。先只读核对包摘要与 04 版本台账中的有效批准，再查看投影。RTL 默认使用同级 `02_LOOP_PROTOTYPE` 的既有核验器。其他安装布局可将 `RTL_ANALYSIS_VERIFIER_ROOT` 设为受信任的 02 工具绝对路径。无上游版本登记的历史包仅返回来源核对信息。

```sh
python3 -m loop import-analysis PACKAGE > 04-projection.json
python3 -m loop skill requirement-architecture --role architect --stage S1 --step analysis --s1-record S1_RECORD
python3 -m loop s1-confirm --project PROJECT --contract CONTRACT_JSON --approval '实际用户确认出处' --s1-record S1_RECORD --analysis-package PACKAGE
```

沿用冻结包的 US、SC、FUNC 和 AC 编号，把来源投影用于本轮契约和场景计划。RTL 仍须确认实际任务、文件授权和可执行用例。逐项在基线顶层的 `analysis_mapping.features` 登记本期 FUNC 的实施场景归属和全部来源 AC 的断言时点。一个 FUNC 关联多个来源场景时，选定本轮的实施归属，完整来源场景仍随交接记录保留。存量能力保留来源以便追溯。

```json
{
  "analysis_mapping": {
    "features": {
      "FUNC-001": {
        "scene_id": "SC-001",
        "acceptance_cases": {
          "AC-001": [{"case": "TC-AC-001", "checkpoint": 1}]
        }
      }
    }
  }
}
```

`checkpoint` 对应该功能已确认断言的实际用例步骤下标，从 0 开始。按常规步骤登记 scenes、features、tasks，再执行 `init`。S1 记录保存包清单摘要、冻结版本和验收映射。RTL 在初始化、领取和后续执行门禁重新核验上游批准。04 撤销或更换基线后，按新冻结包重新登记 S1。现有运行状态仍可查看，独立 RTL S1 继续使用自己的确认依据。

### 同一账号的台账占用

同一台电脑及系统账号下，RTL 用独立于 `--data` 的持久登记簿协调所有台账。macOS 的位置是 `~/Library/Application Support/RTL/reservations.sqlite`，其他受支持 Unix 系统使用 `~/.local/state/rtl/reservations.sqlite`。项目编号相同，或项目、落盘路径与其他活动台账的数据目录存在重叠时，同一时间只允许一个活动写入者。路径重叠检查同时处理大小写别名。同一台账可管理互不重叠的项目。暂停与待恢复的落盘回退期间保留占用。终态已登记且待恢复操作结束后释放占用。若释放前中断，重试原命令或让后续申请核对原台账终态。台账丢失、被替换或无法核对时，先找回原台账和占用记录。

升级后，如果仍有旧版活动轮次，在从其他台账启动前，针对每份已知旧台账运行 `python3 -m loop --data DATA register-ledger`。登记簿能核对已登记的台账，首次升级仍需提供既有台账的位置。

### 整轮暂停与跨会话续跑

1. 收到用户的暂停决定时先执行 `pause-run RUN --epoch EPOCH --reason REASON`。可用 `--checkpoint CHECKPOINT_JSON` 保存阶段、已验证产物和下一步。台账记录 `pause_id` 并停止派发、合入和交付。
2. 查看 `status RUN` 的 `pause`。待核对的宿主执行标为 `unknown`，整轮显示 `stopping`。确认工具已停工后执行 `pause RUN ATTEMPT --stopped --host-ref HOST_REF --epoch EPOCH`。需要替换时按宿主方式完成隔离。 [Codex 停工和隔离](#通过-codex-执行场景)
3. 新会话先读 `status` 和实际宿主，再用 `takeover RUN --epoch OLD_EPOCH` 接管。用户要求继续，且宿主状态均已核对后，用 `resume-run RUN PAUSE_ID --epoch NEW_EPOCH` 解除暂停。以原阶段重新 `claim`。输入仍有效且工作区保留时，沿用同一尝试。Codex 通过 `codex-start` 开启接续轮次，其他宿主在实际恢复后以原执行身份和宿主引用登记 `start`。

### 已确认变更替代活动轮次

1. 核对原 Git 候选、台账及在途验收。未补记的合入按原操作恢复。验收意图按本页的中断步骤恢复或撤销。
2. 冻结新基线并记录用户决定。确认工具停工或工作区隔离后，执行 `supersede RUN --epoch EPOCH --reason REASON --approval REFERENCE`。仍登记为在途的尝试须带 `--stopped` 并通过停工核对。旧轮转为 `superseded`，保留台账、旧候选、工作区和报告。要恢复被驳回候选覆盖的上一版入口时，执行 [落盘回退](#落盘回退)。
3. 使用 `init --project PROJECT --baseline NEW_BASELINE --s1-record NEW_S1_RECORD --suite SUITE_JSON` 启动新轮，按需重复传入 `--suite`。若选择继承旧轮源码，追加 `--from-run OLD_RUN`，并核对同一项目和技术栈。新轮重新登记范围、任务和证据。已继承且源码原样的场景可使用下述仅复验流程。

## 派发、契约和提交

- S1 计划中的任务由 `init` 登记。工程补充可用 `task-add RUN --epoch EPOCH TASK_JSON`，按 S1 任务格式关联现行功能、用例、授权路径和完成条件。路径相对于隔离原型根目录，目录路径授权其子项。关键或共享变更设 `risk: true`。
- `claim RUN TASK --epoch EPOCH --phase design` 可在契约激活前登记设计尝试。省略 `--phase` 则领取实现任务，已声明的依赖和契约须就绪。S3 新派发带 `--stage S3`，其余新尝试默认 S2。恢复同一尝试沿用已登记阶段。将尝试输入、工作区、`skill.content` 和 `style_skill.content` 随简报交给执行者。实现尝试附有 `input.design_handoff` 时，一并交接其原尝试和摘要。`prepare-attempt` 可准备原工作区并重新读取本地 Skill。使用其他宿主时，在观察实际启动后执行 `start RUN ATTEMPT --worker WORKER --host-ref HOST_REF --epoch EPOCH`。 [Codex 启动与自动交接](#通过-codex-执行场景)
- 设计结果通过 `submit RUN ATTEMPT --actor WORKER --result SUMMARY_JSON --epoch EPOCH` 登记非空摘要。接管时通过 `status` 查询结果。其他宿主的实现提交先在工作区登记 Git 变更，再用 `submit` 登记实际提交。Codex 实现由 RTL 核对停工及变更范围后生成提交。暂存合入前必须有绑定实际提交的有效自测。以前已合入的实现失效，或从同项目旧轮继承且场景源码原样时，可用 `submit RUN ATTEMPT --actor WORKER --revalidate --epoch EPOCH`。复验仍须绑定新尝试的基础、契约、当前冻结用例和暂存非作者证据。
- 共享契约使用 `contract-propose RUN EDGE VERSION PROVIDER CONSUMER SPEC_JSON --epoch EPOCH`，然后两方分别 `contract-confirm RUN EDGE VERSION TASK --epoch EPOCH`，最后由 Master 执行 `contract-activate RUN EDGE VERSION --epoch EPOCH`。提案绑定当时的激活状态。依据已变化的提案或已退役版本须用新版本重新提出和确认。接口 JSON 至少有 `behavior` 和非空 `cases`。兼容过渡可显式登记 `compatible_versions`，每项含 `provider`、`consumer` 契约版本。两方任务事先声明同一个 `edges` 编号。新轮用 `case_anchors` 按用例登记 `provider` 和 `consumer`，各填 `feature_id`、可见功能标识的 `step` 和该方实际业务操作或断言的 `interaction`。两个步骤编号均从 0 开始。联验须实际执行含双方步骤的同一冻结套件。
- 契约改版前先暂停或隔离受影响的在途设计和实现。该边首次激活时可沿用未绑定版本的设计。后续改版时，涉及该边的无版本或过期设计交接失效。需要设计交接的任务按当前契约重新登记。激活后旧证据留在历史，相关实现按当前依赖重新核对，独立场景可继续。

查看 [C 场景的任务示例](../skills/requirement-architecture/references/requirements-example.json) 中的 `tasks`。

## 通过 Codex 执行场景

使用本机已登录的 Codex App Server。需要使用默认本地宿主时，可先执行 `codex app-server daemon start`。隔离验证可以连接单独启动的本机 App Server Unix socket，在首次 `codex-start` 带 `--socket SOCKET_PATH`。接管时从台账使用原 socket。Codex 在 RTL 已绑定的工作区内执行，任务、实际绑定的契约内容和本次加载的 Skill 随派发传入。

```sh
python3 -m loop --data DATA claim RUN TASK --epoch EPOCH
python3 -m loop --data DATA codex-start RUN ATTEMPT --epoch EPOCH
python3 -m loop --data DATA codex-status RUN ATTEMPT
python3 -m loop --data DATA codex-interrupt RUN ATTEMPT --epoch EPOCH
```

`codex-start` 为尝试登记一个持久线程，再为当前派发登记一次执行。观察到执行后，台账记录 `worker` 和 `host-ref`。`codex-status` 返回当前派发和 RTL 尝试状态。续跑待启动时，上一轮结果在 `previous`，当前派发尚无轮次。记录短暂不可读时显示 `history-pending`，稍后重查。宿主完成后可读取当前轮次的 `final_response`。

自动派发随工作区和授权范围传入场景及风格 Skill、适用的设计交接和当前契约。派发回执关联两份 Skill 的加载记录。项目提供 `docs/design-style.md` 时一并记录其摘要。

Master 用 `submit RUN ATTEMPT --actor WORKER --epoch EPOCH` 收件。RTL 核对当前轮次成功结束、工具停工和实际变更范围，必要时生成 Git 提交，并在结果里登记 `codex_execution`。用该提交构建、执行自测并登记证据。合入与交付按各自门禁核对。

接管后先 `takeover` 取得新代次，再用 `codex-status` 查询原尝试。若丢失 `turn/start` 回执，重试 `codex-start` 会按原派发查回轮次。若丢失 `thread/start` 回执，先在原宿主核对线程 ID 和工作区，可用 App Server 的 `thread/loaded/list` 和 `thread/read` 查找。随后执行 `codex-attach RUN ATTEMPT THREAD --epoch EPOCH`。RTL 只接受同一工作区、尚无执行记录的空闲持久线程。补登后重试 `codex-start`。已有 Codex 派发意图的尝试由 Codex 入口接管。

`codex-interrupt` 请求停止指定轮次。暂停或替代旧轮前，RTL 同时核对轮次终态、实际工具停工和工作区进程。确认后可用 `pause RUN ATTEMPT --stopped --epoch EPOCH` 登记停工并释放并发位。后续仍可沿用有效的尝试、线程和工作区。若宿主重启后无法证明工具已退出，或原派发意图无法与实际轮次对账，可用 `fence RUN ATTEMPT --isolate-codex REASON --epoch EPOCH`。RTL 将旧工作区移入隔离目录、登记撤权并关闭悬置派发。新尝试使用独立线程和工作区，隔离目录保留变更供排查。

## React 构建和交接

Integration 管理路由、共享组件和依赖锁文件。各场景任务授权 `src/pages/` 或项目约定的组件、业务规则目录。提供的 `prototype-src/` 应含 `package.json`、`package-lock.json` 和能生成 `dist/index.html` 的 `npm run build`，导入前清理依赖和生成目录。

1. **构建自测源码。** 场景完成 Git 提交后，运行 `build RUN --attempt ATTEMPT --epoch EPOCH`，在返回的 `entry` 上执行浏览器用例。报告登记为 `self`，目标填写源码提交。
2. **构建待合入源码。** 执行 `stage` 后，用 `build RUN --operation MERGE_OP --epoch EPOCH` 构建这一份暂存源码。独立验证使用返回的 `entry`，按相应任务登记 `integration` 和 `contract` 证据，再提升候选。初始底座可以直接用 `build RUN --epoch EPOCH` 检查。
3. **核对构建身份。** 每个新提交都导出到独立目录执行 `npm run build`。依赖由 `npm ci` 安装或从已安装快照复制。安装和构建都会清除继承的 `NODE_PATH`，项目依赖通过锁文件登记。产物登记源码提交、锁文件摘要和成品文件清单。工作区只存源码，保持 Git 状态干净。源代码或父版本变化时生成对应的新构建。
4. **落盘复验与接手。** `land` 使用已登记的构建成品，独立验证从正式路径直接打开 `index.html`。通过后 `finalize` 的 `source` 字段指向该提交的源码交接目录，包含启动说明与锁文件。Coding Agent 将该目录复制到自己的工作区开发，交付回执关联原源码和已验成品。
5. **续跑。** 缺少构建时，用当前有效尝试或合入操作的原编号补构建。发现产物与清单不一致时保留现场并排查。下一轮可从上次交付的源码重新起步。切换台账时，`--from-run OLD_RUN` 可按项目的已验收历史查找原运行、源码和回执。未指定继承来源且项目未提供 `prototype-src/` 时，沿用最新的已核验 React 交付。保留原运行台账、Git 仓库和源码交接目录。同台账自动继承早期 React 交付时，RTL 也保留继承候选和原作者来源。

浏览器入口使用相对资源和可按普通脚本加载的成品，运行 Mock 随包。静态模式按以下通用验证步骤处理，React 自测和暂存检查使用构建返回的 `entry`。

### React 依赖缓存

安装快照保存在台账的数据目录 `DATA/npm-dependencies/`，键绑定 `package-lock.json`、`package.json`、Node/npm 环境和安装配置。锁文件字节改变时使用新键。并行构建首次填充同一键时只发布一份安装快照，每个构建使用可独立修改的依赖副本。RTL 对照 `DATA/npm-dependency-integrity/` 中独立保存的摘要核对该副本的文件内容、模式和链接。摘要缺失或不符时会重新安装。适用于当前环境的依赖缺失时，本次安装可按 npm 结果继续，依赖树不发布为可复用快照。

构建回执的 `dependency_install.mode` 显示 `installed`（已安装并填充缓存）、`cache-hit`（已核验依赖副本）或 `uncached`（直接安装）。复用或发布的快照带有 `tree_sha256`，`commands` 记录该提交实际执行过的 npm 命令。首版使用 macOS 文件系统的写时复制。无法克隆，或存在项目安装脚本、本地依赖、项目 `.npmrc`、未经核对的依赖安装脚本时，继续逐次运行 `npm ci`。可在同一数据目录无 React 构建运行时清理安装快照和摘要目录。

活动轮次若使用了没有依赖树摘要的旧缓存命中构建，用原候选、尝试或合入操作的 `build` 入口重建。已落盘的待验候选须与重建产物完全一致才能继续原验收。若清单改变，按原落盘处置流程处理，再验证新候选。

### Coding Agent 接手

S4 完成后，由 Master 在交付说明中引用既有 `finalize` 回执并附上一段可转交的接手说明。以本轮实际产物和已确认业务资料为依据，包含以下内容。

- **从哪里开始。** 交付变更编号、实际可双击入口、回执的源码目录与候选提交。注明适用的依赖锁和源码启动／构建说明。Coding Agent 将源码复制到自己的工作区后开展改动。
- **改动落在哪里。** 链到项目 `docs/feature-map.md`、已确认基线和适用的共享契约。对相关功能给出实际页面路由、源码入口和本轮确认的业务边界。
- **哪些行为由 Mock 提供。** 给出实际 Mock 实现路径和读取／写入边界，标明接真实接口时需要替换的部分。区分可双击演示已验证的行为和仍待真实环境验证的事项。
- **第一步和验证依据。** 给出当前功能入口和可执行的第一步，标明对应的冻结用例、报告及待替换的数据来源。引用当前候选的回归证据与已知限制。

交付说明作为人工可读摘要，原回执、源码快照和落盘成品由工具保留。已验候选缺少业务或 Mock 信息时，在交接中标为待确认。交接所用参考随主控 Skill 的 `--purpose delivery-handoff` 加载。 [主控专项调用](../skills/loop-control/SKILL.md#专项调用)

## 浏览器验证、合入和交付

浏览器验证默认从 `tools/browser-verification` 加载锁定的 Playwright `1.61.0-alpha-1781023400000`。在 RTL 工具根目录运行安装命令。

```sh
npm ci --prefix tools/browser-verification --ignore-scripts
```

默认使用 Chromium。已有本机浏览器时，可用 `RTL_BROWSER_EXECUTABLE` 指定其可执行文件。需要由 Playwright 管理 Chromium 时，单独运行以下命令安装浏览器。

```sh
./tools/browser-verification/node_modules/.bin/playwright install chromium
```

默认执行前核对本地 `playwright` 和 `playwright-core` 的版本及加载位置。依赖缺失、安装不完整或版本与锁文件不符时，按提示执行上面的 `npm ci`。缺少锁定驱动对应的浏览器时，用工具目录下的 Playwright 命令安装所需引擎，或配置本机浏览器。

可用 `RTL_PLAYWRIGHT_MODULE` 显式指定另一份已安装的 Playwright。支持绝对路径和 Node 可解析的模块名，以 `./` 或 `../` 开头的路径相对于调用命令的当前目录。显式覆盖所需的浏览器由该驱动的配置决定。

浏览器验证命令不访问台账。准备独立的用例 JSON 和一个尚未使用的报告路径，两者放在原型目录之外：

```sh
python3 -m loop browser /absolute/path/index.html /absolute/path/suite.json /absolute/path/report.json
```

验证脚本从 `file://` 启动、检查加载和交互、记录实际执行步骤及浏览器版本。支持 `click`、`fill`、`select`、`reload` 和 `text`、`visible`、`value`、`disabled` 等断言。每项用例使用新的浏览器上下文。设计规范可用 `css` 断言核对实际渲染的字号、尺寸和颜色，提供 `property` 与预期 `value`。像素长度允许 0.1px 的浏览器舍入差异，其他值按原值比较。

```json
{
  "fixture_revision": "fixtures-v1",
  "suite_revision": "suite-v1",
  "cases": [{
    "id": "TC-C01",
    "steps": [
      {"op": "fill", "selector": "#layout", "value": "compact"},
      {"op": "click", "selector": "#save"},
      {"op": "reload"},
      {"op": "text", "selector": "#result", "value": "compact"}
    ]
  }]
}
```

1. **场景自测。** 场景 Agent 调用 [scene-delivery](../skills/scene-delivery/SKILL.md#自审自测和入账)，为尝试工作区的实际提交生成报告。用 `evidence RUN self SHA --actor WORKER --context CONTEXT --report REPORT --epoch EPOCH` 入账，上下文 JSON 含 `attempt_id`。原始 `--report` 的同一份字节用于解析、摘要和归档，冲突的报告上下文字段会阻断入账。
2. **暂存。** Integration 先用 `skill prototype-integration --role integration --stage S3 --run RUN --epoch EPOCH` 加载当前指引。专项用途由 [主控专项调用](../skills/loop-control/SKILL.md#专项调用) 选择。`stage RUN ATTEMPT [ATTEMPT...] --epoch EPOCH` 生成父版本、待合入提交、验证路径与操作编号。React 先构建该暂存提交，再对返回的 `entry` 跑必需检查。静态模式直接验证暂存入口。证据类型 `integration` 需要 `operation_id`。受共享改动影响的额外用例和契约分别用 `--require-case CASE`、`--require-edge EDGE` 登记到本次暂存，可重复传入。共享接口另登记 `contract`，上下文增加 `edge`、`version`，以及 `provider` 和 `consumer` 各自的 `task`、`commit`。符合 [主控联验证据要求](../skills/loop-control/SKILL.md#暂存联验与提升) 后，`promote RUN OPERATION --epoch EPOCH` 核对当前候选和各项证据。高风险及复验任务的通过证据须来自覆盖该任务已冻结断言时点的套件。属于当前用例和范围的独立验证失败会阻止提升，包括低风险任务和额外用例。契约失败与通过使用同一套冻结锚点筛选，再按实际契约和候选归属。重做暂存或回派时，按适用的失败用例和原验证条件重新取证。
3. **功能地图。** 项目的 `docs/feature-map.md` 用下列表头登记功能、实际入口、契约和必跑用例。用例或契约多个时以逗号分隔。React 用实际成品路由，例如 `index.html#/guide`。用 `map-check RUN` 预检，落盘前也会检查。这个预检核对文件和编号。路由、入口、业务步骤或验证数据变化时，再按 [功能地图实走与故障归因](../skills/prototype-verification/SKILL.md#功能地图实走与故障归因) 对照实际页面和冻结预期。
4. **实际落盘。** `land RUN --epoch EPOCH` 放入完整候选，返回实际入口和交付操作编号，此时为待验。已获准替换且现有内容与已登记的上轮交付、被替代轮次保留的落盘清单或静态导入基础一致时，使用 `--allow-replace`，工具保留原目录。更换台账后，同一路径的旧交付须与原台账已验收的清单完全一致。其他既有内容需要先确认独立落盘路径。
5. **独立验收。** 非作者 Verifier 用 `skill prototype-verification --role verifier --stage S4 --run RUN --epoch EPOCH` 取得 [验证指引](../skills/prototype-verification/SKILL.md) 和适用参考。新轮从返回的实际 `index.html` 运行 `browser ENTRY SUITE REPORT --feature-map PROJECT/docs/feature-map.md --feature-entries`。报告记录实际入口、可见功能标记和用例各步的 URL。将冻结断言的关键步骤安排在功能地图的登记入口，跨页面用例分别在对应功能入口执行。页面可用 `<h1 data-rtl-scene="SCN-C" data-rtl-feature="FUNC-C01">布局偏好</h1>` 声明场景及功能，同一元素可用空格列出多个功能编号。普通文档应呈现可见内容，原生锚点须命中可见元素。哈希路由须有匹配的当前导航和可见页面标题，或在对应页面上用可见的 `data-rtl-route="/route"` 标识。每个登记的浏览器分别通过必跑用例、功能标记和实际入口的业务执行校验。适用的补充用例在同一浏览器上已报失败时，按原定义复验通过。地图、成品或适用用例变化时重新取证。用 `evidence RUN delivery SHA --actor VERIFIER --context CONTEXT --report REPORT --epoch EPOCH` 记录报告，上下文含交付 `operation_id`。Master 执行 `finalize RUN OPERATION --actor MASTER --epoch EPOCH`，按需提供 `--approval REFERENCE`。要求人工验收时须提供实际确认依据，待确认等占位值会被拦截。台账、交付清单和项目变更记忆指向同一候选。最新轮次的生成台账、CHG 或冻结快照丢失时，可重试原 `finalize` 恢复。
6. **中断。** 接管后对照 `status` 和实际产物。对原合入操作重试 `promote`，对待验目录重试 `land`，对验收后缺回执的目录重试 `finalize`。替换落盘目录中断且入口仍缺失时，先恢复原 `land` 再推进新候选。Git 已前移、合入回执未登记且候选需返修时，用 `rollback-integration RUN OPERATION --reason REASON --epoch EPOCH` 恢复已登记的父版本，保留原失败证据，再回派任务。合入待恢复时，冻结文件丢失可先执行 `prepare RUN --epoch EPOCH`。原自测报告丢失时，由场景执行者在同一尝试、提交和冻结用例上重新取证，再重试原合入。原报告丢失时，用同一候选和冻结用例重新取证，恢复原验收决定并同步回执与记忆的证据引用。验收重试可省略 `--approval` 或提供原依据。依据变化按 [主控验收流程](../skills/loop-control/SKILL.md#s4--决定交付) 处理。若必须撤销已登记且尚未回写的验收意图，执行 `cancel-acceptance RUN DELIVERY_OP --epoch EPOCH --reason REASON`。已部分回写的验收按原意图恢复。换了父版本或候选就重新暂存或验证。

| 功能 | 场景 | 入口 | 契约 | 必跑用例 |
| --- | --- | --- | --- | --- |
| FUNC-C01 | SCN-C | index.html#layout | - | TC-C01 |

执行者标识是本机 Master 记录的交接身份。实际 Agent 派发与暂停由所用宿主完成，运行台账不将未经观察的宿主动作标记为已发生。

### 落盘回退

若替换落盘后验收被驳回，用 `rollback-landing RUN DELIVERY_OP --reason REASON --epoch EPOCH` 恢复上一份已验收交付。命令按原交付编号核对实际待验目录、已验收记录与备份摘要，保留被驳回内容和回退回执。尚有验收意图时，先按上面的中断步骤处理。回退中断期间保留路径占用，重试同一操作和原因即可恢复。

## 重复缺陷的处理

同一成因的缺陷第二次有实际证据时，由 Master 对照原变更与报告，在项目的 `memory/project.md` 记下触发条件、受影响功能、证据和负责方，引用既有验收记录。优先消除产生错误的重复入口或共享状态问题，或把该行为补进现有验证。用可取得的旧候选证明检查能失败，再在修复后的精确候选上证明通过。旧候选或数据不可复现时，保留原因和能确认的证据。

涉及共享 Skill 或门禁的变更由维护者人工审阅，涉及已确认业务行为或验收预期的变化按基线修订处理。重复缺陷的参考随主控 Skill 的 `--purpose repeat-defect` 加载。 [主控专项调用](../skills/loop-control/SKILL.md#专项调用)
