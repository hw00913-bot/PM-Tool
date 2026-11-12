# RTL Skill 包

[登记清单](registry.json)维护可调用入口、角色和阶段。每次新调用读取当前 `SKILL.md` 和适用的本地参考小节。角色入口的 `content` 含所加载的小节正文。当前业务与验收取本轮冻结的需求契约和用例，接口实现取尝试绑定的激活版本。旧契约和旧报告供差异、追溯和原版本复验。状态、权限和证据由执行模块检查。

## 从当前任务找到入口

调用时点、负责人、回派和人工确认统一查 [主控调度](loop-control/SKILL.md)。遇到中断、暂停或回执待补时查 [恢复流程](loop-recovery/SKILL.md)。专业入口说明工作方法、判断标准、产物和证据。

| 当前情况 | 读取入口 | 交接的关键产物 |
| --- | --- | --- |
| 新建项目、派发、裁决，或收到「继续」「再验一遍」「改规则」「页面有问题」 | [loop-control](loop-control/SKILL.md) | 明确本次请求、目标运行或候选、下一动作和产物 |
| 「先停一下」、跨会话续跑，或操作结果与回执待核对 | [loop-recovery](loop-recovery/SKILL.md) | 暂停检查点、原编号的核对结果、补记回执和就绪队列 |
| S2 至 S4 的并行与恢复门禁变更，或故障取决于事件顺序 | [statechart](statechart/SKILL.md) | 有界模型、反例路径、隔离复现与可复跑用例 |
| 需求进入项目，或补充影响现行规则 | [requirement-architecture](requirement-architecture/SKILL.md) | 需求契约、场景清单、功能点、任务包和需求台账 |
| 场景 Agent 领取设计、实现或自测 | [scene-delivery](scene-delivery/SKILL.md) | 实际提交、授权变更、自测报告与 `self` 证据 |
| 两个场景需要约定或修改共享接口 | [contract-coordination](contract-coordination/SKILL.md) | 双方确认的契约、影响分析、精确组合证据 |
| 建底座、设计界面或检查合入风格 | [prototype-design-style](prototype-design-style/SKILL.md) | 项目风格基线、状态样例、视觉检查与例外 |
| 场景自测通过，等待合成与联验 | [prototype-integration](prototype-integration/SKILL.md) | 待合入结果、父版本、提升回执 |
| Verifier 复现故障、核对待合入候选、联验共享接口或做落盘回归 | [prototype-verification](prototype-verification/SKILL.md) | 绑定实际候选和入口的 `integration`、`contract` 或 `delivery` 证据 |

## 运行和维护

在工具根目录用 `python3 -m loop --data DATA skill SKILL_ID --role ROLE --stage STAGE --run RUN --epoch EPOCH` 读取并记录本次调用。S0 尚未建立运行时，可省略 `--data`、`--run` 和 `--epoch` 先读流程。整轮暂停期间可带运行编号和当前代次读取 `loop-recovery` 或 `statechart` 并留下关联回执。需要按任务筛选时带 `--task TASK`，任务取自 `--run` 指定的运行。可用 `--purpose PURPOSE` 补充当前工作的适用小节。实际命令见 [运行手册](../docs/runbook.md)，模块与用例见 [工具功能地图](../docs/feature-map.md)。

人工维护时，先确认改动涉及流程说明、执行门禁还是业务验收，再更新对应说明和验证用例。冻结后的业务与验收预期按基线修订流程生效。

## 本地参考技能

主流程在使用处用相对路径链接到本地技能的具体小节。每节写明适用于 RTL 的做法，以及可定位的上游仓库、文件、章节和已核对的版本或分支。参考由登记清单按角色、阶段、S1 步骤、用途和任务契约选择，每次调用读取本地最新正文。关联运行的调用返回 `operation_id`，可在 `status RUN` 查找文件路径、章节和摘要。启动前的 S1 调用在评审记录保留同样的来源信息。

- [pstack 工作流](references/pstack-workflows/SKILL.md)
- [Superpowers 交接流程](references/superpowers-workflows/SKILL.md)
- [需求工作法](references/requirements-workflows/SKILL.md)
- [工程验证](references/engineering-verification/SKILL.md)
