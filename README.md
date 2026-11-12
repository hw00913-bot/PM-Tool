# PM-Tool

把业务需求整理成可核验的冻结包，再交给 RTL 实现并验收可双击打开的页面原型。

| 工具 | 职责 | 入口 |
| --- | --- | --- |
| 04 Requirement Analysis Loop | 分析用户故事、业务规则、接口、场景、功能和页面。记录来源与验收条件，经 G1 场景范围、G2 业务与接口冲突、G3 最终基线三次人工确认后冻结版本。 | [04 Skill](04_requirement-analysis/requirement-analysis-loop/SKILL.md) · [版本与冻结流程](04_requirement-analysis/requirement-analysis-loop/VERSION_WORKFLOW.md) |
| 07 RTL | 在 S1 确认本轮契约、场景、功能、任务和可执行用例。场景 Agent 在隔离工作区并行执行，Integration 负责联验与合入，Verifier 从实际落盘入口独立验收。默认使用 React + Umi + Ant Design，交付 `index.html` 和对应源码。 | [RTL 说明](07-RTL/README.md) · [运行手册](07-RTL/docs/runbook.md) · [功能地图](07-RTL/docs/feature-map.md) |

将工具仓库、业务工作区和 RTL 的 `--data` 目录分别放置。冻结包、验收报告和运行台账保存在对应业务项目与数据目录。

## 安装到 Codex

先克隆到长期保留的本地路径。

```sh
git clone https://github.com/hw00913-bot/PM-Tool.git
cd PM-Tool
```

首次安装时，在仓库根目录执行以下命令。04 的入口链接到整个 Skill 目录，RTL 的入口指向仓库内的工具根目录。Codex 支持用户级 Skill 目录及符号链接。 [OpenAI Codex Skill 文档](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills)

```sh
(
  set -eu
  pm_repo="$(pwd -P)"
  pm_skills="$HOME/.agents/skills"
  test -f "$pm_repo/04_requirement-analysis/requirement-analysis-loop/SKILL.md"
  test -f "$pm_repo/07-RTL/AGENTS.md"
  mkdir -p "$pm_skills"
  for pm_name in requirement-analysis-loop rtl; do
    if [ -e "$pm_skills/$pm_name" ] || [ -L "$pm_skills/$pm_name" ]; then
      printf '请先核对现有 Skill %s\n' "$pm_skills/$pm_name" >&2
      exit 1
    fi
  done

  ln -s "$pm_repo/04_requirement-analysis/requirement-analysis-loop" \
    "$pm_skills/requirement-analysis-loop"
  mkdir "$pm_skills/rtl"
  cat > "$pm_skills/rtl/SKILL.md" <<EOF
---
name: rtl
description: 运行 RTL 多 Agent 原型 Loop。用于使用 rtl 开始规划项目或明确要求用 RTL 导入冻结包、续跑、派发、复验和验收。
---

# RTL 多 Agent 原型 Loop

从 $pm_repo/07-RTL 工具目录运行 python3 -m loop。
先读取该目录下的 AGENTS.md、skills/loop-control/SKILL.md 和 docs/runbook.md。
按技能登记清单加载各角色的本地 Skill。
用户明确要求「使用 rtl 开始规划项目」时进入 S0。
已有轮次沿用原 --data 目录，用 status RUN --next 核对代次、操作编号和恢复事项。
EOF
)
```

在 Codex 中用 `$requirement-analysis-loop` 开始需求分析。新原型项目发送「使用 rtl 开始规划项目」，已有轮次用 `$rtl` 续跑。已有同名入口时核对其指向，可沿用本机的 `~/.codex/skills` 安装位置。按以上方式安装后，在原克隆目录执行 `git pull --ff-only` 即可更新两套工具。移动克隆目录时同步更新链接和 RTL 入口路径。

## 将 04 冻结包交给 RTL

### 1. 在 04 生成已批准的版本化包

新项目按 [04 版本工作流](04_requirement-analysis/requirement-analysis-loop/VERSION_WORKFLOW.md#新项目和现有项目接入) 初始化共享版本台账并把分析工作区 `attach` 到实际版本。已有项目沿用原台账和版本，分析工作区的版本名称须与台账一致。完成 S8 全局核验及 G1、G2、G3 人工确认后，在仓库根目录执行下列命令。

`ANALYSIS_DIR` 和 `BUSINESS_ROOT` 分别是分析工作区与业务项目根目录的绝对路径。`REVIEWER`、`APPROVAL_FILE`、`APPROVAL_NOTE` 使用真实裁决者、审批证据文件和结论。

```sh
pm_04="$(pwd -P)/04_requirement-analysis/requirement-analysis-loop"

python3 "$pm_04/scripts/project_versions.py" bind "$ANALYSIS_DIR" --stage analysis
python3 "$pm_04/scripts/project_versions.py" approve "$ANALYSIS_DIR" \
  --stage analysis --role baseline --reviewer "$REVIEWER" \
  --evidence "$APPROVAL_FILE" --note "$APPROVAL_NOTE"

# S9 尚未启动时执行 start
python3 "$pm_04/scripts/loop.py" start "$ANALYSIS_DIR"
python3 "$pm_04/scripts/package_delivery.py" "$ANALYSIS_DIR" --project-root "$BUSINESS_ROOT"
python3 "$pm_04/scripts/loop.py" complete "$ANALYSIS_DIR" --stage S9
```

使用打包命令打印的冻结 ZIP 路径。也可使用含 `delivery-manifest.json`、`prototype-handoff.json` 和冻结内容的完整解包目录。核验时要能读取 04 的版本台账、批准所绑定的工作区及相关登记文件。

### 2. 在 RTL 只读核验并确认 S1

从既有本机安装提供受信任的 `02_LOOP_PROTOTYPE`，其中须有 `orchestrator/scripts/import_analysis.py` 和 `orchestrator/scripts/project_versions.py`。若它与 `07-RTL` 同级可用默认查找路径，其他布局用 `RTL_ANALYSIS_VERIFIER_ROOT` 指定工具根目录的绝对路径。

在 `07-RTL/` 下运行以下命令。`FROZEN_PACKAGE` 指向上一步的 ZIP 或完整解包目录，`PROJECTION_JSON` 和 `S1_RECORD` 放在本轮独立工作区。`PROTOTYPE_PROJECT` 是已确认的原型项目绝对路径。

```sh
export RTL_ANALYSIS_VERIFIER_ROOT="/absolute/path/to/02_LOOP_PROTOTYPE"
python3 -m loop import-analysis "$FROZEN_PACKAGE" > "$PROJECTION_JSON"
python3 -m loop skill requirement-architecture --role architect --stage S1 \
  --step analysis --s1-record "$S1_RECORD"
```

核对投影中的当前版本、批准和 US、SC、FUNC、AC 来源，再编制本轮契约 `CONTRACT_JSON`。取得真实确认后，以实际依据填写 `S1_APPROVAL`。

```sh
python3 -m loop s1-confirm --project "$PROTOTYPE_PROJECT" --contract "$CONTRACT_JSON" \
  --approval "$S1_APPROVAL" --s1-record "$S1_RECORD" --analysis-package "$FROZEN_PACKAGE"
```

在本轮基线 `BASELINE_JSON` 的 `analysis_mapping.features` 逐项登记 FUNC 的实施场景、来源 AC 对应的实际用例和断言下标（从 0 开始）。按顺序登记 `scenes`、`features`、`tasks` 的 Skill 读取和 `s1-handoff`。将 `RTL_DATA_DIR` 设为独立运行数据目录的绝对路径，并以实际套件文件 `SUITE_JSON` 初始化。 [RTL 从 04 接续 S1](07-RTL/docs/runbook.md#从-04-冻结包接续-s1)

```sh
python3 -m loop --data "$RTL_DATA_DIR" init --project "$PROTOTYPE_PROJECT" \
  --baseline "$BASELINE_JSON" --s1-record "$S1_RECORD" --suite "$SUITE_JSON"
```

用 `--output` 指定另行确认的落盘目录，默认使用原型项目内的 `prototype/`。有多份套件时重复传入 `--suite`。RTL 会在后续门禁复核上游批准；04 批准撤销或冻结包换版后，按新批准重新登记 S1。

## 本地依赖

| 用途 | 需要准备 |
| --- | --- |
| 基础运行 | Python 3.11+，以及支持 POSIX 文件锁的环境，如 macOS 或 Linux。Git 用于克隆仓库和 RTL 隔离工作区。 |
| 04 冻结包交接核验 | 与 04 版本协议兼容的本地 02 工具。RTL 用 `RTL_ANALYSIS_VERIFIER_ROOT` 定位其核验器。 |
| RTL 默认 React 构建 | Node.js 22.12+，或 20.19+ 的 20.x，以及 npm。RTL 按锁文件在隔离目录安装和构建。交付的 `index.html` 可直接用浏览器打开。 |
| RTL 浏览器验收 | Node.js、npm 和验收浏览器，默认 Chromium。从 `07-RTL/` 运行 `npm ci --prefix tools/browser-verification --ignore-scripts` 安装锁定的 Playwright。可用 `RTL_PLAYWRIGHT_MODULE` 覆盖模块，用 `RTL_BROWSER_EXECUTABLE` 指向本机浏览器。 [浏览器运行手册](07-RTL/docs/runbook.md#浏览器验证合入和交付) |
| RTL 自动派发 Codex | 已登录的本机 Codex App Server。默认本地宿主可用 `codex app-server daemon start` 启动。 [派发与接管](07-RTL/docs/runbook.md#通过-codex-执行场景) |
| 04 可选业务流程与泳道 HTML | 本地 PlantUML 命令，或 PlantUML JAR 加 Java。时序和页面结构使用原生 SVG。 [PlantUML 配置](04_requirement-analysis/requirement-analysis-loop/references/plantuml-rendering.md) |
| 04 可选 Mermaid 候选图 | Node.js 20+、npm、Playwright 和本地浏览器，按工具的锁文件安装候选渲染依赖。 [Mermaid 配置](04_requirement-analysis/requirement-analysis-loop/references/mermaid-candidate.md) |
| 04 可选 Statechart 检查 | 本机 Pstack 执行器、TypeScript 运行时及 Java/TLC。按需用 `RAL_STATECHART_TOOL` 指定执行器，用 `TLA2TOOLS` 指定已有的 TLC JAR。 [04 Statechart](04_requirement-analysis/requirement-analysis-loop/skills/statechart/SKILL.md) |
| 07 可选 Statechart 检查 | 执行器随 07 提供。准备兼容的 TypeScript 运行时及本机 Java/TLC，将 `TLA2TOOLS` 指向已有的 JAR。可用 `JAVA` 指定 Java，并用 `RTL_STATECHART_TOOL` 覆盖执行器路径。 [07 Statechart](07-RTL/skills/statechart/SKILL.md) |
