---
name: prototype-integration
description: "Stage accepted RTL scene submissions against the current parent, assess shared impact, and promote the verified candidate using exact-result evidence."
---

# 暂存合并、联调与候选提升

## 触发与输入

合入输入包括当前代次、父版本、尝试、提交、可写范围、契约组合、风格基线和已有自测证据。用 `status RUN` 对照实际工作区。命令在 Loop 工具目录运行，使用 `--data DATA` 或 `RTL_DATA_DIR`。调度由 [主控](../loop-control/SKILL.md#暂存联验与提升) 安排，参数格式见 [运行手册](../../docs/runbook.md)。

## 1. 决定本次合入范围

- 候选可以先吸收已自测的独立场景。涉及共享边时，组合包含参与联验的实际提供方和使用方。允许使用方与已集成的一方配对的前提是本轮契约明确允许，而且有该版本组合的证据。
- 检查每个尝试仍是任务的当前提交，冻结需求、当前契约和工作区均与该尝试相符。证据绑定提交、尝试编号和必跑用例。将越权文件、已变更的工作区和依赖业务规则调整的接线列入阻塞，记录实际路径和受影响范围。
- 从任务包和功能地图确定合入用例。记录场景、共享状态和契约的改动，以及所有实际实现作者，供独立证据核对。

### 共享改动的影响复核

共享文件按已授权的共享任务范围接线。将写入范围、涉及的验收用例和关联功能列入合入输入。缺少对应任务或范围时，按阻塞交回这些信息。

复核共享路由、外壳、主题、租户上下文或持久化改动时，对照当前父版本和本次变更，沿实际使用关系定位可能受影响的场景。 [pstack Blast radius](../references/pstack-workflows/SKILL.md#blast-radius)

即使场景没有契约依赖，也检查它是否消费了变更的共享能力。为最值得验证的一到两项写清受影响功能编号、原有行为、触发方式及可观察预期。优先使用已有用例。把本次必须通过的补查用 `stage --require-case CASE` 和 `--require-edge EDGE` 写入暂存操作，每个选项可重复。用例须来自已冻结的可执行文件。

改动公共存储时，沿实际读写关系检查相关场景的写入和回读。接口版本相同的场景也可能受影响。将需要补查的用例绑定到实际暂存候选。

## 2. 生成可验证的合并结果

```sh
python3 -m loop --data "$DATA" stage "$RUN" "$ATTEMPT_A" "$ATTEMPT_B" --epoch "$EPOCH"
```

保留返回的 `operation_id`、`parent`、`candidate`、`path` 与 `attempt_ids`。React 执行 `build RUN --operation OPERATION --epoch EPOCH`，记录返回的 `entry`、源码提交和成品摘要。Loop 把授权提交的变更应用到当前候选父版本，在隔离位置建立暂存提交。补丁冲突时记录实际文件、冲突原因与受阻提交。

验证交接记录包含以下信息。

| 交接字段 | 用途 |
|---|---|
| 操作编号、父版本、候选提交、入口 | React 绑定构建返回的 `entry` 与源码提交，静态模式绑定 `path/index.html`。 |
| 实际尝试与提交、相关功能编号、作者及自测证据编号 | 定位变更来源和自测覆盖，核对独立验证者身份。 |
| 本轮必跑或合入用例、数据修订 | 复现业务路径和负向场景。 |
| 契约边与版本、提供方和使用方的任务及提交 | 绑定真实 A/B 组合的 `contract` 证据。 |
| 风格基线和受影响页面 | 定位待核对的状态、组件及已登记例外。 |
| 共享改动的功能编号、风险与补查步骤 | 在候选入口复验受影响的原有行为，记录实际结果。 |

合入证据绑定实际候选、所需 `integration` 用例及共享接口的 `contract` 组合。使用方以 Mock 验证的结果登记为自测。跨场景联验在同一暂存候选核对实际双方，并按当前契约锚点登记步骤和提交。 [原型验证 Skill](../prototype-verification/SKILL.md)

## 3. 提升、重试或回派

若改版、迟到提交和提升重试可能互相影响，按主控安排读取本地 [statechart](../statechart/SKILL.md)，从模型反例提取需要实测的操作顺序。

```sh
python3 -m loop --data "$DATA" promote "$RUN" "$OPERATION" --epoch "$EPOCH"
```

| 现场状态 | 处理 |
|---|---|
| 父版本仍匹配，暂存提交及所需证据均有效 | 提升该候选。交付 Master 操作编号、父版本、候选、已集成尝试与证据编号。 |
| 暂存验证失败 | 已登记的独立验证失败会阻止提升，包括低风险任务和额外用例。保留失败结果与候选，列出受影响任务和待复验项。只修复验证环境时，在同一操作上取得通过的复验证据后重试提升。源码修复时重新暂存，原失败用例随受影响任务在新候选上复验。 |
| 父版本被其他合入推进 | 基于新父版本重新暂存，产生新操作并复验。旧结果的报告仍归旧候选。 |
| 实际候选已指向该结果，台账缺回执 | 记录实际引用和原操作编号，按 [恢复对账](../loop-recovery/SKILL.md#2-对账与下一步) 核对。 |
| 验收回写或落盘回退仍未完成 | 先按原交付操作对账，核对实际目录及回执，再继续候选推进。 |

例：A/B 在父版本 G1 上产生候选 H1，验证期间 C 已提升为 G2。重新用 G2 暂存出 H2，检查 C 和 A/B 合并后的受影响路径，再尝试提升 H2。该顺序取自 Bors 先验证合并结果再推进分支的做法。RTL 使用 Git 的预期旧值检查阻止过时提升。 [Bors 合并队列设计](../references/engineering-verification/SKILL.md#merge-queue) · [Git 条件更新](../references/engineering-verification/SKILL.md#conditional-ref-update)

合入阻塞的回派和需要用户决定的变更见 [主控](../loop-control/SKILL.md#回派与人工确认)。
