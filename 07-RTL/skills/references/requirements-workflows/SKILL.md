---
name: requirements-workflows
description: Support RTL S1 with locally adapted practices for extracting and clarifying requirements, slicing scenarios, checking traceability, planning tasks, and recording testable changes.
---

# RTL 需求工作参考

按当前步骤读取对应小节。产物字段见 [需求架构](../../requirement-architecture/SKILL.md)，确认与放行规则见 [主控](../../loop-control/SKILL.md#需求冻结与派发前核对)。以下来源均于 2026-11-11 核对上游 `main` 的对应文件。

## Extract requirements

1. 对照用户输入、原始材料和上一份已交付契约提取会影响业务行为、设计或验收的内容。每条保留原材料位置、适用范围和可观察结果。已确认的约束、待证假设和待决问题分别记录。
2. 改版沿用仍有效的条款编号。发现来源冲突时，记录双方出处、受影响的规则及待决定事项。退役的编号继续用于历史追溯。

来源 `bmad-code-org/BMAD-METHOD@main` · `skills/bmad-agent-po/references/spec.md` · `Memory and derivation`、`The Operation`、`Spec Law`。

## Clarify requirements

1. 查用户目标与范围、角色权限、数据归属与状态、异常路径以及通过条件。将疑点按影响和不确定程度排序，优先处理会改变场景边界、实现方向或验收结果的问题。
2. 一次围绕一个决定提出可答的问题。将用户答复写回实际受影响的条款和验收预期，保留确认出处，再检查关联条款是否仍自洽。待确认的问题保留在待决清单中。

来源 `github/spec-kit@main` · `templates/commands/clarify.md` · `Outline`。

## Slice scenarios

1. 围绕角色完成的可见业务结果拆分场景。每个场景写清初始条件、操作、结束状态和主要异常，并给出可以单独演示或验收的方式。
2. 如一个场景依赖另一个场景产生的事实，记录该事实、提供者和受影响结果。用已确认的前置状态或契约样例做场景自测，实际双方行为留在联验核对。

来源 `github/spec-kit@main` · `templates/spec-template.md` · `User Scenarios & Testing`。

## Check traceability

只读交叉核对需求、场景、功能、任务和用例。逐项找没有上游依据的任务、没有任务或用例覆盖的行为、互相矛盾的结果及共享文件的写入冲突。记录差异、编号和出处，交接更新后核对对应差异。

来源 `github/spec-kit@main` · `docs/reference/agentic-sdd.md` · `speckit.analyze`。

## Plan tasks

1. 按可独立交接的业务结果安排任务。每个任务标明稳定编号、关联功能、实际文件范围、依赖、可检查产物和验证用例。
2. 把必须先合入的共同底座与可并行的场景工作分开。无硬依赖且写入范围互不重叠的任务可并行。共享文件指定写入方，跨场景约定交给契约联验。

来源 `github/spec-kit@main` · `templates/tasks-template.md` · `Format`、`Dependencies & Execution Order`。

## Write testable specs

1. 每条规则描述一个可观察的业务行为。用具体的初始数据、动作和预期结果写验收用例，覆盖影响正确性的错误与边界。预期涉及持久化时，在保存、刷新或重新进入等对应动作后检查结果。
2. 对照上一份已交付契约区分新增、修改和移除。修改时写出完整的新行为和能直接证明它的断言，移除时说明如何观察旧行为已消失。纯实现变化和仅复验按 RTL 现有变更类型登记。

来源 `Fission-AI/OpenSpec@main` · `docs/writing-specs.md` · `What makes a good requirement`、`What makes a good scenario`、`Pick the right kind of delta`。

## Separate spec and change

先以现行规格定位业务行为及编号，再为本轮写清改变的范围、受影响能力和验收结果。将 UI 或技术实现方案放在设计和任务交接中。交付后由本轮变更记录链接到验收证据和冻结基线，保留旧轮记录的定位。

来源 `Fission-AI/OpenSpec@main` · `schemas/spec-driven/schema.yaml` · `proposal`、`specs`、`design`、`tasks`。
