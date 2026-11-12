---
name: pstack-workflows
description: "为 RTL 的主控分流、并行调度、暂停与接手、隔离试验、影响分析、浏览器验证和缺陷复盘提供本地 pstack 工作法。按引用小节查阅。"
---

# pstack 工作法在 RTL 中的应用

按调用处的链接查阅对应小节。具体操作、角色权限和门禁以 RTL 的运行台账及对应操作规范为准。来源定位采用已核对的本地 `08-pstack` 包，`plugin.json` 登记版本 `0.15.36`、作者 Lauren Tan、许可 MIT。原引用的仓库定位为 `cursor/plugins` 下的 `pstack/`。以下路径均相对于插件根目录，内容已按 RTL 场景整理。

## Playbooks

根据用户要达成的结果选择入口。只读核对从现有运行、交付和证据开始。修缺陷先复现原行为，暂停和接手使用原运行的检查点。需求变更进入确认流程。独立试验可以帮助决定设计方案，正式原型按已确认范围执行。

来源位置 `skills/poteto-mode/SKILL.md` 的 `Playbooks`。

## Orchestrate

- 首次验证新执行方式时，先让一个场景走通派发、自测、独立验证和合入。每个尝试的授权文件有明确负责人，已就绪工作按依赖和写入范围安排。
- 派发简报写明任务目标、实际工作区、基础版本、授权路径、上游结果及完成证据。
- 主控查运行台账和实际产物，独立验证者从待合入候选重新取证。候选变化后对受影响路径重验，失效的尝试按台账回派。

来源位置 `skills/poteto-mode/playbooks/orchestrate.md` 的 `Topology` 和 `Supervision`。

## Pause safely

用户要求暂停时停止新增派发，在可恢复的边界留下持久检查点。记录运行、阶段、候选、尝试、实际工作区、已验证证据和恢复第一步。核对各执行宿主的状态，状态未知时保留记录及写入占用。

来源位置 `skills/poteto-mode/playbooks/pause-safely.md` 的 `Pause safely`。

## Session pickup

接手时从已有检查点、台账、实际目录和候选重建状态。核对已完成、待补回执和仍未执行的工作，再从第一个待办继续。向 Coding Agent 交付时用同样的结构写明可打开入口、源码候选、已验证行为和接手第一步。

来源位置 `skills/poteto-mode/playbooks/session-pickup.md` 的 `Session pickup`。

## Prototype

先写清待决定的问题和可观察结果。在隔离目录用最小界面或脚本验证关键取舍，比较方案时保持数据和视口一致。记录实走结果、试验位置和建议。记录待用户决定的取舍和影响，将已确认的结果写入正式基线。

来源位置 `skills/poteto-mode/playbooks/prototype.md` 的 `Prototype`。

## Blast radius

从差异追到真实消费关系，重点看共享路由、状态、租户上下文和持久化。挑一两个最能区分安全与回归的条件，在确切暂存候选中实走相关功能。用功能编号、触发动作、预期结果和报告定位记录结论，按实际需要登记额外合入用例。

来源位置 `skills/blast-radius/SKILL.md` 的 `Steps`。

## Create verification

从用户实际使用的入口启动，核对候选与测试数据，用稳定定位执行真实动作，观察页面反馈和写入结果。报告保存在原型目录之外，清理临时环境后仍能定位。验证流程初次交接前至少走通一个已登记功能的完整路径。

来源位置 `skills/create-verification-skill/SKILL.md` 的 `Generate the skill`、`Seed the feature map` 和 `Prove the generated skill before handing it over`。

## Maintain verification

把功能地图、源码和实际用户路径逐项对照。行为符合已确认预期时，记录地图、步骤或数据的差异及受影响功能。行为偏离预期时，保留实际行为、原用例与失败证据。地图或验证输入修订后，核对修订版本并重走实际路径。

来源位置 `skills/maintain-verification-skill/SKILL.md` 的 `Pass`。

## Bug fix

在同一用户入口用相同的前置数据、操作和断言保留旧候选失败证据，并验证修复候选通过。只有旧候选和数据确实可取时才重放。已有证据可复用，缺少复现条件时记录限制并按本轮门禁验证。

来源位置 `skills/poteto-mode/playbooks/bug-fix.md` 的 `Bug fix`。

## Correct

同类错误再次出现时，定位共同成因并关联此前失败证据。优先检查是否能收敛写入职责、消除重复入口或补强已有门禁。可取得旧候选时证明检查能拦住原错误，并对修复候选取证。共享流程修改交维护者审阅。

来源位置 `skills/correct/SKILL.md` 的 `Find the mistake classes`、`Fix each class at the highest level that works` 和 `Fix and prove`。
