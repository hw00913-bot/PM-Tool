---
name: statechart
description: 用 TLA+ 和 TLC 检查 04 需求分析中的业务状态、跨场景消息与验收条件。适用于接口已核验、需要比较取消、回调、超时、重试或并行事件顺序的场景。
---

# 业务状态核验

选择一个结果取决于事件顺序的业务场景。S5 从 S3 的流程和 G2 已核验的接口事实建模，S8 对照当前版本复核已建模型的发现与处置。阶段入口见 [S5 交互时序](../../references/workflow.md#s5-交互时序分析) 和 [S8 全局一致性核验](../../references/workflow.md#s8-全局一致性核验)。

## 建立模型

1. 对照当前 `BR-*`、`FLOW-*`、`SC-*`、`SEQ-*`、`API-*`、`AC-*`。记录模型所用的来源、版本和原文位置，把未确认的条件列为假设或待决项。按需区分业务对象状态、请求是否在途、回调是否到达以及参与方可执行的动作。
2. 用 `.tla` 定义初始状态和命名动作，用 `.cfg` 给出有限参数和检查项。将可能交错的提交、取消、超时、重试、回调和补偿分别建成动作。只纳入有依据的转移，把争议选项分别检查。选择能表达当前问题的小范围，并写明范围及省略项。
3. 用 `INVARIANT` 表达已经确认的约束。比如已确认取消结果不可逆时，检查迟到成功回调是否能让业务对象恢复成功。检查最终可达结果时，写明是否依赖消息最终送达、人工处理或其他条件，标明合法终态。

## 运行与取证

在项目、冻结包和正式输出目录之外准备隔离工作目录，放入 `BusinessFlow.tla` 和 `BusinessFlow.cfg`。从 04 的 `requirement-analysis-loop` 源码根目录执行下例。使用个人安装副本或 Pstack 位于其他位置时，将 `RAL_STATECHART_TOOL` 设为本机 Pstack `skills/statechart/scripts/statechart` 可执行文件的绝对路径。将 `RAL_STATECHART_AUDIT_DIR` 设为隔离工作目录的绝对路径。

```sh
RAL_SKILL_ROOT="$(pwd -P)"
RAL_STATECHART_TOOL="${RAL_STATECHART_TOOL:-$RAL_SKILL_ROOT/../../08-pstack/skills/statechart/scripts/statechart}"
(
  cd "$RAL_STATECHART_AUDIT_DIR" &&
  "$RAL_STATECHART_TOOL" BusinessFlow.tla --config BusinessFlow.cfg --out result
)
```

每次使用新的或空的输出目录。本地工具需要 TypeScript 运行时和 Java/TLC。已有 JAR 时用 `TLA2TOOLS` 指向 `tla2tools.jar` 的绝对路径，按需用 `JAVA` 指定 Java。未指定 JAR 时，Pstack 通过 mise 获取依赖，首次运行需要网络。用 `--view view.mjs` 可按业务对象与外部消息划分图中的状态区。

读取 `report.md` 和 `tlc.out`，有 `chart.mmd` 时连同反例一起引用。退出码为 `1` 且报告给出反例路径时，按动作顺序核对原始规则、场景分支和接口消息。记录规格、配置、运行命令、检查范围、反例路径和未检查项。工具受阻时记录未运行或受阻及原因，按现有流程继续业务复核。

## 回填业务结论

- 模型假设或抽象有误时，修正模型并重跑，记录差异。反例暴露规则冲突、责任缺口或缺失的验收条件时，在 `runtime/open-items.json` 关联现有或新增 `ISSUE-*`，记录涉及的 `BR/SC/SEQ/API/AC`、事件路径、影响和待确认问题。 [问题清单契约](../../references/artifact-contracts.md#open-itemsjson)
- 业务方确认处置后，更新原事实源和对应 `AC-*`。影响已确认事实时，按最早受影响阶段失效重跑。涉及 G2 接口事实的变化回到 S4 重新核验，更新时序与后续验收后再请求 G3。历史冻结包保留原版本。 [质量门禁](../../references/quality-gates.md)
- Statechart 报告供专项复核。正式分析、业务确认和冻结依照现有阶段证据与 G1/G2/G3，交接以当前已确认的结构化事实和验收条件为准。

## 本地来源

借鉴与 04 同级的 `08-pstack` 中的 `skills/statechart/SKILL.md`、`skills/statechart/scripts/statechart` 和 `skills/statechart/scripts/run.ts`。已核对 `08-pstack/plugin.json` 中的 Pstack 版本 `0.15.36`、作者 Lauren Tan 和 MIT 许可。
