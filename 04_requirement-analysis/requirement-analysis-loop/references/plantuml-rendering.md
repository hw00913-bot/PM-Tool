# 按图类型选择本地渲染

## 定位

PlantUML **仅用于** `FLOW-*` 业务流程和 `SC-*` 场景泳道。它将已核验的 `business_flows[].steps` 和 `flow_diagram` 排版为 SVG；业务事实、稳定 ID、条件和追溯关系仍维护在 JSON 中。

业务图默认 `compare`：展开 PlantUML 主图，折叠保留原生核验基线。精简时可选择 `plantuml`；业务图的 `native` 只供诊断，不能通过 S8 正式门禁。PlantUML 不替代原生几何核验。

交互时序与页面结构固定使用原生 SVG，直接内嵌 HTML，不使用 PlantUML。这两类的 `native` 是正式交付模式，不是诊断兜底；独立生成不查找或调用 PlantUML。

## 业务图的本地运行时

必须使用本地命令或本地 JAR，禁止调用 PlantUML Server、Kroki、mermaid.ink 等公网渲染服务。技能不捆绑 JAR，也不会自动下载依赖。

按以下优先级定位运行时：

1. `--plantuml-command <path-or-name>`；
2. `REQUIREMENT_ANALYSIS_PLANTUML_CMD`；
3. `--plantuml-jar <absolute-path>`；
4. `REQUIREMENT_ANALYSIS_PLANTUML_JAR`；
5. `PATH` 中的 `plantuml`。

使用 JAR 时本机还须存在 Java；Java 不在 `PATH` 时使用 `--java-command <absolute-path>` 或 `REQUIREMENT_ANALYSIS_JAVA_CMD`。团队固定 PlantUML 版本，避免同一事实源出现布局漂移。

## 生成模式

```bash
# 全部正式图：FLOW/SC 使用 PlantUML 对比版，SEQ/PAGE 使用原生 SVG
python3 <skill-dir>/scripts/render_atlases.py <project-dir> \
  --plantuml-jar <absolute-path-to-plantuml.jar> \
  --java-command <absolute-path-to-java>

# 仅业务流程与场景泳道
python3 <skill-dir>/scripts/render_atlases.py <project-dir> --kind scenarios \
  --scenario-renderer compare --plantuml-command <local-plantuml>

# 仅交互时序：无需 PlantUML，等价于 --sequence-renderer native
python3 <skill-dir>/scripts/render_atlases.py <project-dir> --kind sequences

# 仅页面结构：无需 PlantUML
python3 <skill-dir>/scripts/render_atlases.py <project-dir> --kind pages

# 业务图诊断版：不能用于通过 S8
python3 <skill-dir>/scripts/render_atlases.py <project-dir> --kind scenarios \
  --scenario-renderer native
```

`business-scenario-atlas.html` 先展示跨场景 FLOW，再展示 SC 详细泳道；`interaction-sequence-atlas.html` 展示 SEQ 消息；`page-structure-atlas.html` 展示菜单层级与 PAGE 归属，不表达业务流或消息先后。三份 HTML 均内嵌 SVG、CSS 和交互脚本，支持缩放、复位、全屏、下载 SVG；查看不需要网络或 PlantUML。

业务图和时序图采用 `desktop-wide@1` 桌面全宽阅读壳，目录默认收起；窄图铺满可用宽度，宽图按可读比例横向滚动，手动适应宽度提供整图总览。全屏包含工具栏。不为两类图考虑移动端阅读，也不通过缩字、删标签或改变事实去迎合窄屏。

仅 `FLOW-*.puml`、`SC-*.puml` 写入 `runtime/plantuml/`，用于审计复现，不进入事实源。不再生成 SEQ/PAGE 的 PlantUML 源码；历史文件不因技能升级自动删除，历史冻结包不改写。

## 门禁与失败行为

- 业务场景图的所有渲染模式先执行 `inspect_swimlane` 和 `audit_swimlane_layout`。
- 业务图需要的 PlantUML 不存在、超时、返回错误图、外部链接或可执行 SVG 时立即失败，不静默回退公网。
- S8 要求全部 FLOW 和本期 SC 的本地 PlantUML SVG 完整；`data-plantuml-render="pass"` 仅代表引擎成功，不代表视觉通过。
- S5/S8/打包要求每个 SEQ 恰有一个原生 SVG，与完整 SEQ 事实指纹及消息覆盖一致；拒绝混入 PlantUML。显式传入 `--sequence-renderer plantuml` 或 `compare` 会报错，不覆盖既有图。
- 页面结构要求原生 SVG 完整覆盖所有 PAGE 叶节点、事实指纹匹配；无 UI 时明确展示无页面，不造页面。
- 在允许的浏览器环境检查所有图，重点查看长文本、多路分支、汇合、自调用、长时序、条件/API 标签、缩放及下载。环境阻止浏览器时报告限制，不把自动检查当成浏览器验证。

## 转换边界

PlantUML 活动图接受单开始节点、一个或多个结束节点及可规约的有向无环控制流；决策支持两路或多路分支，转换器识别汇合点。非决策多出边、回退环或无法规约的交叉汇合会明确失败，不篡改事实迎合版式。业务确需重试/循环时保留真实模型并用原生版诊断；只有转换器具备对应语义和验证后才扩大正式支持。

原生时序按 `messages` 顺序绘制，完整保留步骤、类型、条件、API 和消息正文；响应虚线、自调用回环，文本换行后动态增高，不截断。不会从零散条件猜测组合片段；需要 `alt/else` 等语义时先扩展并确认 SEQ 契约，再扩展原生渲染器。

原生页面结构按菜单路径前缀构树、挂接 PAGE 叶节点；自动换行并按子树分配空间，节点重叠时拒绝输出。
