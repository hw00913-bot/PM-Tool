---
name: requirement-architecture
description: "Analyze RTL requirements and produce traceable S1 contracts, business scenes, functions, acceptance cases, and scoped task packages."
---

# 需求契约与四步交接

输入包括用户确认的材料、本轮落盘位置与确认依据、现有功能地图和变更明细。改版以同一项目上一份已验收的冻结契约定位原行为。各步引用同一份本轮需求契约修订。

有 04 冻结输入时，按 [从 04 冻结包接续 S1](../../docs/runbook.md#从-04-冻结包接续-s1) 校验当前上游批准并保留来源编号。以本轮实际用例和断言时点登记 AC 映射，实施任务、写入范围及验证仍按四步交接确认。

| 步骤 | 本地规范 | 产物 |
| --- | --- | --- |
| 需求分析 | [analysis](references/analysis.md) | 带逐条来源、业务规则和验收结果的已确认契约 |
| 场景梳理 | [scenes](references/scenes.md) | 带需求引用、业务流程和跨场景依赖的清单 |
| 功能点拆分 | [features](references/features.md) | 稳定的功能编号、业务场景、验收用例和本轮变更 |
| 任务拆分 | [tasks](references/tasks.md) | 功能覆盖、可执行任务包、写入范围和完成条件 |

用 `python3 -m loop skill requirement-architecture --role architect --stage S1 --step STEP --s1-record S1_RECORD` 读取并登记步骤。返回正文含本步骤的参考小节。分析产出契约草案和待决事项，场景、功能及任务以已确认的契约为输入。后续每步用 `s1-handoff --s1-record S1_RECORD --step STEP --artifact BASELINE_JSON` 登记规范化产物。上游产物改版时，重新核对并登记受影响的下游产物。已有运行时可改用 `--data DATA --run RUN --epoch EPOCH` 记录本次读取。

## 基线与追溯

四步产物合入基线 JSON 的 `requirements` 对象，沿用本轮修订、冻结用例及数据摘要。核对交接摘要、实际套件和约定的断言时点。已登记任务和交接回执可通过 `requirements-check RUN` 核对。上一份冻结契约用于差异比对和旧版复验。当前实现和验收取本轮已确认的需求、用例与断言。 [四步输入示例](references/requirements-example.json)

功能地图使用「功能、场景、入口、契约、必跑用例」五列，场景填写本轮业务场景编号。拟用入口应可与候选的实际入口核对。每项功能对应场景标识、功能标识和在该入口执行的冻结断言。需求台账中的功能历史与 `CHG` 明细双向关联。

## 按需验证技术或交互疑点

当可观察的技术或交互疑点会影响场景边界、验收方式或实现时，在当前 `skill` 调用加 `--purpose prototype`，并在隔离临时目录做最小验证。S1 调用保留当前 `--step`。若只为已登记的步骤补读参考，省略 `--s1-record`，保留已登记的交接记录。用相同数据和视口比较方案。例如验证新组件能否双击离线打开时，按拟用构建方式生成样例，再从 `file://` 打开并操作。记录问题、观察结果、样例路径、建议和待确认取舍。隔离样例的证据绑定样例入口，正式证据绑定正式候选及冻结用例。 [pstack Prototype](../references/pstack-workflows/SKILL.md#prototype)

## 差异与待决事项

- 需求来源冲突或规则待决时，列出原始依据、待决问题、候选取舍和影响范围。
- 场景缺口、功能覆盖不足或任务依赖成环时，指出涉及的产物和编号，提出补齐或重拆方案。共享路径写明拟议的唯一写入者。
- 已冻结的业务范围、规则或验收预期发生变化时，列出原修订、拟议差异、受影响的场景、功能、用例及任务。
