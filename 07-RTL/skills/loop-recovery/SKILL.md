---
name: loop-recovery
description: "Pause an active RTL run at an operator’s request, resume a checkpoint, or take over after interrupted dispatch, merge, verification, local export, or acceptance. Reconcile each durable intent with observed work before retrying."
---

# 主控接管与回执恢复

当用户明确要求整轮暂停或继续、主控中断、调用报错后结果不明，或台账与实际候选、工作区、交付目录不一致时使用。恢复目标是保留有效工作、撤销过期提交，并让同一次已发生的副作用只登记一次。

以下命令从 Loop 工具目录执行，省略 `python3 -m loop --data DATA` 前缀。大写参数取自本轮记录。命令细节见[运行手册](../../docs/runbook.md)。

核对原操作后，若重复故障涉及接管、停工或回退与其他动作的顺序，按主控安排读取本地 [statechart](../statechart/SKILL.md)，将可达反例映射到隔离恢复操作。

## 整轮暂停与续跑

用户明确要求整轮暂停时，在本 Skill 调用加 `--purpose pause`，读取安全停点和检查点小节。 [pstack Pause safely](../references/pstack-workflows/SKILL.md#pause-safely)

先执行 `pause-run RUN --epoch EPOCH --reason REASON`。用 `--checkpoint CHECKPOINT_JSON` 记录阶段、当前工作、关键路径与续跑第一步。台账即刻锁住新的派发、提升、落盘与验收。保留待对账的操作及各尝试的工作区，再联系宿主逐一停工。

通过 `status RUN` 的 `pause` 核对检查点、候选、在途尝试、宿主引用及停工状态。宿主不明时保留 `unknown` 和写入占用。按下表区分普通宿主和 Codex 的停工、隔离与续跑。

用户要求继续时，按 [主控入口](../loop-control/SKILL.md#按用户当前请求选择入口) 定位运行。新 Master 读取持久检查点与实际宿主，使用 `takeover` 换代次。暂停期间可用 `skill loop-recovery --role master --stage STAGE --run RUN --epoch EPOCH` 读取恢复正文并登记回执，`EPOCH` 使用当前代次。所有宿主状态已核对后执行 `resume-run RUN PAUSE_ID --epoch EPOCH`。输入仍有效时按原阶段领取同一尝试，在原执行者实际恢复后登记启动。需要换人或输入已失效时先确认并隔离原执行，再将待派发任务交回 [主控](../loop-control/SKILL.md#派发简报和宿主交接)。

续跑时沿用检查点，并核对已完成的实际产物和待办。 [pstack Session pickup](../references/pstack-workflows/SKILL.md#session-pickup)

## 1. 接管并区分身份

1. 用 `status RUN` 核对稳定项目编号、绝对项目与输出路径、冻结基线、当前候选、尝试列表和操作回执。对照宿主中的实际会话、Git 引用或实际目录。先暂停新增派发、候选提升和落盘。核对 `status RUN` 的 `pause`。暂停仍有效时，保留写入占用，并可核对宿主及已有证据。
2. 新 Master 接管活动轮次时运行 `takeover RUN --epoch OLD_EPOCH`，保存返回的新代次。原 Master 续跑时核对自己持有的当前代次。对账后，按用户要求及检查点确认可恢复的执行。读取本地当前 Skill，已有产物按它实际使用的输入核对。
3. 按操作编号核对 `intent`、`done` 或 `superseded`。**操作编号**标识本次派发、提升或交付的副作用。**尝试编号**标识场景执行。重试同一次副作用沿用原编号，换执行者或重新派发产生新尝试。重新暂存不同父版本或候选产生新合入意图。

从已有台账续到中断点的思路来自 Superpowers。Temporal 的幂等操作则解释了为什么外部动作成功、回执尚未登记时，应先核对实际动作再重试。RTL 将这两点用于本地宿主、Git 和目录。 [Superpowers 恢复规则](../references/superpowers-workflows/SKILL.md#resume-from-ledger) · [Temporal 幂等操作](../references/engineering-verification/SKILL.md#idempotent-recovery)

## 2. 对账与下一步

| 中断点 | 应核对什么 | 继续方式 |
|---|---|---|
| **初始化** | 已有运行、代次有效，候选或基础导入尚未登记。 | 在原运行上使用 `prepare RUN --epoch EPOCH`。 |
| **派发已登记，工作区缺失或未绑定** | 尝试仍有效，宿主尚未启动。 | 用 `prepare-attempt RUN ATTEMPT --epoch EPOCH` 绑定原工作区，再按原意图派发。 |
| **普通宿主已启动，台账未记启动** | 宿主回执能匹配原尝试、执行者和工作区。 | 接回原执行并补 `start RUN ATTEMPT --worker WORKER --host-ref HOST_REF --epoch EPOCH`。若台账已显示该身份为 `running`，继续收原结果。 |
| **Codex 启动回执缺失** | 用 `codex-status RUN ATTEMPT` 对照原线程、轮次、工作区与派发意图。 | 丢失轮次回执时，以当前代次重试原 `codex-start`。丢失线程回执时，先核对原宿主上的同工作区空闲持久线程，再用 `codex-attach RUN ATTEMPT THREAD --epoch EPOCH` 补登。 [Codex 执行步骤](../../docs/runbook.md#通过-codex-执行场景) |
| **暂停或迟到结果** | 核对原运行、尝试、契约、执行者和基础版本。Codex 还须核对该轮次终态及实际工具停工证据。 | 确认停工后登记 `pause RUN ATTEMPT --stopped --host-ref HOST_REF --epoch EPOCH`。当前输入有效时可续跑原尝试。已隔离、被替换或输入过期的结果留作历史。 |
| **确需替换执行者，或 Codex 无法证明停工** | 核对原尝试与工作区。普通宿主核对实际停止或隔离。Codex 宿主重启后还须查原轮次工具，轮次终态本身不足以证明停工。 | 普通宿主确认后用 `fence ... --stopped`。Codex 可用 `fence RUN ATTEMPT --isolate-codex REASON --epoch EPOCH` 隔离旧工作区、关闭旧意图，再派新尝试。隔离或停工待核对时保留占用和依赖阻塞。 |
| **提升已登记，回执缺失** | 比较意图中的父版本、暂存候选和实际 Git 候选。可用 `git -C REPOSITORY rev-parse --verify refs/rtl/candidate` 只读核对。 | 实际 Git 候选等于原父版本或原待提升结果时，运行 `promote RUN MERGE_OP --epoch EPOCH`，让门禁复核后执行或补记。实际 Git 已推进而原门禁或暂存无法恢复时，Master 核对原意图并登记 `rollback-integration RUN MERGE_OP --reason REASON --epoch EPOCH`。工具按原父版本和候选撤销未完成提升，保留尝试与证据。撤销中断时沿用原命令，完成后可重新暂存和验证。 |
| **落盘已登记，未确认完成** | 对照原交付意图、候选、目标目录、暂存和既有内容。 | 仍是同一候选与路径时重走 `land RUN --epoch EPOCH`，核对返回的交付操作编号。原目录已移入备份且新目录还未切换时，先完成原落盘再推进候选。旧目标有意外内容时保留现场，记录路径、实际内容和所有权待核对项。 |
| **文件已落盘，浏览器验证未完成** | 校对实际目录仍是原候选，报告覆盖冻结用例，路径、版本和验证输入一致。 | 从实际 `index.html` 继续或重做独立验证并登记 `delivery` 证据。保持待验，直到必跑证据齐备。 |
| **验收意图已登记，变更记忆或最终回执缺失** | 验证实际候选、目录和独立报告，确认已登记的人工验收依据仍适用。 | 用 `finalize RUN DELIVERY_OP --actor MASTER --epoch EPOCH` 恢复同一验收决定。证据丢失时用同一实际成品及冻结用例补证，核对回执和记忆引用新报告。确需放弃且尚未回写时，按[运行手册](../../docs/runbook.md#浏览器验证合入和交付)登记撤销。 |
| **替换落盘被驳回，或落盘回退中断** | 核对原交付操作、上一份已验收成品与备份、当前目录及已有回退意图。 | 按原交付操作执行 `rollback-landing RUN DELIVERY_OP --reason REASON --epoch EPOCH`。已有回退意图时沿用原参数。回退后核对恢复入口和被驳回候选归档。 |

`REPOSITORY` 位于运行数据的 `projects/<project_id>/runs/<run_id>/repository`，两个编号取自 `status`。上表中的命令执行门禁仍有效。若已登记验收意图但实际目录或证据无法再匹配，挂起对应交付，保留现场和不匹配项。已经完成的操作引用原回执，缺失的回执通过表中原操作补记。保留历史证据、回执与变更记录，候选和台账按相应命令的门禁更新。

## 3. 演练示例与恢复出口

**F1 · 宿主启动后主控中断。** 台账只有 B 的派发意图，宿主能查到 B 的同一尝试和工作区。接管后用新代次登记原执行者。核对该任务最终只有一个有效尝试，旧主控代次的写入会被拒绝。

**F11 · 落盘后中断。** `G2` 已在确认路径，实际路径回归还缺一项。恢复时核对目录并补验证。在同一交付操作编号下完成验收，确认只生成一份变更记录与回执。若期间生成 `G3`，以新候选重新落盘与验证。

恢复回执包含新代次、核对的操作编号、实际证据、补记的回执、可继续尝试和阻塞项。项目身份、目标所有权、旧成果处置或业务取舍仍待决定时，把核对结果交给 [主控](../loop-control/SKILL.md#回派与人工确认)。
