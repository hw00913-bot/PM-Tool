---
name: statechart
description: "用 TLA+、TLC 和 Mermaid 专项核对 RTL 的并行派发、接管停工、契约改版、合入或落盘恢复。给出可在隔离环境重放的反例，并记录检查边界。"
---

# RTL Statechart

输入是当前 RTL 规则、涉及的 CLI 操作、台账状态和已知故障证据。挑选一个会因事件顺序变化而产生风险的流程，检查允许的状态组合与转移。调用时点和放行依据见 [主控专项调用](../loop-control/SKILL.md#专项调用)。

## 建立模型

1. 在 RTL 工具目录、原型目录及运行台账之外建立隔离工作目录。用 `.tla` 写初始状态、命名动作与规则，用 `.cfg` 登记检查项和有限的参数。按问题选变量，分别表达 Master 代次、派发操作、尝试、宿主轮次、实际写入或工作区隔离。涉及契约或交付时再加入激活版本、候选、落盘操作和验收证据。写明省略的状态与假设。
2. 将可能交错的动作拆开。比如写入派发意图、宿主启动、回执落库、接管、重试、收到轮次终态、取得停工证据、释放执行位，各自独立。允许丢回执或中断时，从副作用前、后两侧检查恢复。
3. 把必须始终成立的规则登记为 `INVARIANT`。按实际问题选取，例如同一派发操作至多启动一次，旧代次无法推进台账，确认旧工具停工或工作区已隔离后才释放执行位。契约改版检查旧输入及证据是否正确失效。落盘恢复检查确认的候选、路径和回执是否始终对应。
4. 首次建模可用两个执行位、三个场景和一次接管。先用已知故障校准模型。检查恢复是否能完成时，写明前提，例如最终可取得停工证据或能完成工作区隔离。区分正常结束、保留证据待处理和真正无法恢复的状态。

## 运行与解释结果

使用本机 Pstack 的可执行工具。从 RTL 工具根目录执行下列示例。将 `AUDIT_DIR` 指向隔离工作目录。Pstack 安装在其他位置时，将 `RTL_STATECHART_TOOL` 指向本地 `skills/statechart/scripts/statechart` 可执行文件的绝对路径。

```sh
RTL_ROOT="$(pwd -P)"
STATECHART_TOOL="${RTL_STATECHART_TOOL:-$RTL_ROOT/../08-pstack/skills/statechart/scripts/statechart}"
(
  cd "$AUDIT_DIR" &&
  "$STATECHART_TOOL" Audit.tla --config Audit.cfg --out result
)
```

工具需要支持 TypeScript 的运行时及 Java/TLC。已有本地工具时，用环境变量 `TLA2TOOLS` 指定 `tla2tools.jar` 的绝对路径，按需用 `JAVA` 指定 Java。未指定 JAR 时，Pstack 通过 mise 获取依赖，首次安装需要网络。每次使用新的或空的输出目录。需要为状态和动作命名时，可加 `--view view.mjs`，按状态区分别标记台账、宿主和工作区，保留反例上影响判断的转移。

读取 `report.md` 与 `tlc.out`，有图时使用 `chart.mmd`，按需查阅 `tlc-RULE.out`。工具退出码 `1` 且有报告时读取反例。记录状态数、模型范围和报告列出的未检查项。检查中断、依赖缺失或工具错误时登记阻塞。检查通过时写明覆盖的模型范围与已核对的规则。图超出工具预算时，用报告中的反例路径交接。

## 反例复现与交接

把反例按动作顺序映射成隔离运行上的真实 CLI 调用、宿主事件和故障注入。记录初始状态、对应命令或观察、每步实际结果及日志位置。例如用台账核对代次和派发编号，用宿主记录核对轮次及工具状态，用 Git 或目录核对实际候选与副作用。模型命中与实际复现分别记录，出现差异时先核对模型假设与运行入口。可复现的路径补成回归用例。

交付规格、配置、可复跑命令、报告和可用图。每个发现附规则、最短反例和实际复现证据。RTL 的提交、提升与验收继续按对应运行门禁取证。 [运行手册](../../docs/runbook.md)

## 本地来源

基于本地同级 `08-pstack` 的 `skills/statechart/SKILL.md`、`skills/statechart/scripts/statechart` 和 `skills/statechart/scripts/run.ts`。已核对 `08-pstack/plugin.json` 中的 Pstack 版本 `0.15.36`、作者 Lauren Tan 和 MIT 许可。
