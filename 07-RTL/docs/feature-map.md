# RTL 工具功能地图

本页用于从工具能力定位 Skill、模块与验证点。用例编号对应设计验收项，执行结果由实施或运行记录提供。

| 工具功能 | Skill | 模块位置 | 验证点 |
|---|---|---|---|
| 项目识别、请求分流、路径确认与阶段调度 | [loop-control](../skills/loop-control/SKILL.md) · [只读下一步引导](runbook.md#启动与续跑) | `loop/__main__.py`、`loop/next_actions.py`、`loop/state.py`、`loop/reservations.py` | 同一台账快照的运行编号、代次和恢复命令，F12 跨台账唯一活动写入者、大小写路径别名 |
| 整轮暂停、主控接管、旧轮替代与回执补记 | [loop-recovery](../skills/loop-recovery/SKILL.md#整轮暂停与续跑) | `loop/lifecycle.py`、`loop/dispatch.py`、`loop/artifacts.py`、`loop/delivery.py` | F1 派发和跨会话停工恢复、F3 提升、F11 落盘或登记中断 |
| 台账版本和历史记录读取 | [现行依据与历史记录](runbook.md#现行依据与历史记录) | `loop/ledger_schema.py`、`loop/payload_types.py`、`loop/state.py` | 只读查询保持旧台账字节一致，写入前原子升级，沿用原编号续跑和验收 |
| 并行调度与恢复规则的模型检查 | [statechart](../skills/statechart/SKILL.md) | 本地 Pstack 执行器、隔离模型及真实 CLI 复现记录 | 派发重试、停工证明、契约改版、提升及落盘回退的事件顺序 |
| 需求契约、四步交接与功能变更追溯 | [requirement-architecture](../skills/requirement-architecture/SKILL.md) | `loop/requirements.py`、`loop/requirements_run.py`、`loop/requirements_history.py`、项目 `docs/需求台账.md` | F7 契约冻结与派发校验、F8 功能及用例断链、S4 变更回链 |
| 04 冻结包接续 S1 | [导入与验收映射](runbook.md#从-04-冻结包接续-s1) | `loop/upstream_analysis.py`、`loop/analysis_mapping.py`、`loop/s1_review.py` | 上游批准有效性、原编号与断言时点、撤销后阻断初始化和执行 |
| 场景工作区、设计、实现和提交交接 | [scene-delivery](../skills/scene-delivery/SKILL.md) | `loop/dispatch.py`、`loop/artifacts.py` | F1 有效尝试唯一、F13 依赖等待不占住执行位、设计结果随实现派发 |
| Codex 场景派发、接管、停工与收件 | [Codex 执行手册](runbook.md#通过-codex-执行场景) | `loop/codex_host.py`、`loop/codex_client.py`、`loop/codex_tools.py`、`loop/dispatch.py` | 原派发对账、工具终态与工作区进程、未知派发隔离、受控提交 |
| 契约确认、激活和影响范围 | [contract-coordination](../skills/contract-coordination/SKILL.md) | `loop/contracts.py` | F2 破坏性改版、迟到结果及独立场景继续 |
| 底座和风格一致性 | [调用与回派](../skills/loop-control/SKILL.md#风格技能的调度) · [设计与视觉检查](../skills/prototype-design-style/SKILL.md#3-视觉检查) | 项目 `docs/design-style.md`、`templates/react/src/layouts/`、实际待合入结果 | F10 禁用视觉和业务阻断分别留证、派发绑定项目风格摘要 |
| React 源码底座、构建与交接 | [场景实现](../skills/scene-delivery/SKILL.md) · [暂存合入](../skills/prototype-integration/SKILL.md) · [Coding Agent 接手](runbook.md#coding-agent-接手) | `templates/react/`、`loop/frontend.py`、`loop/delivery.py` | 精确源码构建、文件摘要校验、源码与落盘成品对应 |
| React 隔离构建的依赖复用 | [React 依赖缓存](runbook.md#react-依赖缓存) | `loop/npm_dependencies.py`、`loop/dependency_integrity.py`、`loop/frontend.py` | 命中前核验私有副本、锁文件变更失效、并行构建副本独立、安装缺包时跳过缓存、旧待验产物来源核验 |
| 暂存联验、共享改动复核和候选提升 | [prototype-integration](../skills/prototype-integration/SKILL.md#共享改动的影响复核) | `loop/artifacts.py`、`loop/verification.py`、`loop/contracts.py` | F4 局部回派、F5 父版本变化阻断旧结果、原始报告一致、失败证据归属契约套件 |
| 双击验证、功能地图实走、验收和落盘回退 | [prototype-verification](../skills/prototype-verification/SKILL.md#功能地图实走与故障归因) · [只读复验](../skills/loop-control/SKILL.md#只读复验) | `loop/feature_map.py`、`loop/verification.py`、`loop/delivery.py` | F6 过期证明、F8 实际入口与冻结业务断言、F11 落盘验证、一次性登记和回退 |
| 当前 Skill 查找与调用记录 | [Skill 登记](../skills/registry.json) | 工具运行入口与调用记录 | F9 最新本地内容、角色权限及硬门禁 |

## 原型项目地图约定

每个原型的 `docs/feature-map.md` 用五列登记稳定功能编号、业务场景、实际入口、适用契约和必跑用例。业务预期通过功能及用例编号关联本轮冻结基线。脚本、固定数据和证据在 Loop 数据目录按项目和版本定位。Master 从失败用例沿功能、场景和契约回派。独立场景可以只关联场景用例。
