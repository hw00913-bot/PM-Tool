# Mermaid 复杂流程候选工具

## 定位与事实边界

这是需求分析 Loop 内部的可选工具，不是第二个 Loop，也不是新的自动门禁。适用于 S3 比较多分支、回路、长中文标签的普通流程排版。正式 FLOW/SC 仍按 PlantUML 规则生成，SEQ/PAGE 仍使用原生 SVG。

- SC 只消费场景索引及该场景 flow_diagram：完整保留原节点 ID、边端点、分支条件、path_id、责任主体和来源引用。为了 Mermaid 标识符安全可使用内部 ID，不能重新编号业务事实。
- SC 候选在节点中标注责任，**并没有绘制真实泳道边界**。不能把责任文字、阶段目录或普通 subgraph 冒充泳道。
- FLOW 只消费 business-rules.json 中既有 steps，按有序步骤连接；不会从文字猜分支、增加起止业务事实或展开循环。
- graph.json 和 source-snapshot.json 均是可重建的审计中间物，不在其中独立修订需求。修订源事实仍遵循先失效、再更新、重新确认的既有规则。
- 该工具不消费 Wiki 正文，不让原型 Loop 读取候选材料，也不改变功能拆分标准。

## 本地准备与渲染

始终使用全新的输出目录。项目内只允许 runtime/mermaid-candidates/ 下的新子目录；验证技能时使用项目外的隔离临时目录。不能指定正式 outputs、deliverables、技能目录或已存在的输出位置。

准备候选，只运行 Python，不启动浏览器：

    python3 <skill-dir>/scripts/render_mermaid_candidate.py <project-dir> --diagram-id SC-001 --out <new-candidate-dir> --prepare-only

核对 graph.json 与 Mermaid 源码后，在本地浏览器运行被允许的环境中渲染：

    node <skill-dir>/tools/mermaid-flow/scripts/render.mjs --input <new-candidate-dir>/graph.json --out <new-candidate-dir>

也可对全新的目录一次准备并渲染：

    python3 <skill-dir>/scripts/render_mermaid_candidate.py <project-dir> --diagram-id FLOW-001 --out <another-new-dir>

复核来源、派生文件与渲染证据：

    python3 <skill-dir>/scripts/verify_mermaid_candidate.py <project-dir> <candidate-dir>

仅核对渲染输入绑定，不加载绘图依赖或浏览器：

    node <skill-dir>/tools/mermaid-flow/scripts/render.mjs --input <candidate-dir>/graph.json --out <candidate-dir> --check-input

核验器会从当前源事实重新派生内容，并对比文件摘要；不只信任候选清单里的哈希。来源变化、修改图后自行重写哈希、错图报告、缺失或篡改的成品都会被拒绝。

## 运行依赖

Python 3.9+、Node.js 20+。本地绘图固定使用 Mermaid 11.17.2、ELK 0.1.9、esbuild 0.28.2、Playwright 1.62.1；版本不符直接报告，不静默使用其他版本。依赖清单位于 tools/mermaid-flow/package.json，锁文件的下载地址使用官方 npm registry。

运行时依次取显式 --runtime、REQUIREMENT_ANALYSIS_MERMAID_RUNTIME 环境变量、Codex 的 skill-runtimes/requirement-analysis-mermaid 独立缓存（仅当存在清单），最后取工具目录。运行时只存公共依赖，不存业务原文。

首次安装须在经授权的独立依赖目录放入这两份清单，再运行：

    npm ci --ignore-scripts --no-audit --no-fund --registry=https://registry.npmjs.org

不把 node_modules 或浏览器打包进技能。安装依赖不上传业务素材；查看生成 HTML 不需要网络或 Node。不要自动运行浏览器下载命令。可使用已安装且获准的 Playwright Chromium，或显式 --browser / FLOW_BROWSER 指定获准浏览器。浏览器不可用、访问被拒或平台禁止执行时，停止实际渲染并说明限制；不能切换浏览器、间接执行或调用在线服务绕过。

## 产物与状态

准备完成生成：

- graph.json、完整流程.mmd：从当前事实派生。
- source-snapshot.json：相关来源的审计快照，不是新事实。
- candidate-manifest.json：合同 mermaid-candidate@1；source_files 绑定项目相对路径与字节摘要，files 绑定上述三份派生文件；formal_delivery 始终为 false。

实际渲染且几何检查未阻断时，增加 index.html、完整流程.svg；verification.json 和 rendered_files 保存本次实际检查及成品摘要。若几何不可靠则不生成 HTML，只保存可复核的阻断报告；后续修正须准备新目录，不能覆盖旧证据。

候选 render 状态为 not_run / blocked / passed；browser 状态为 not_run / failed / passed。passed 仅表示本次候选自动检查通过，仍保留 visual_review=not_performed、business_acceptance=not_claimed。准备/源码检查成功不等于渲染成功。

页面使用桌面全宽画布、默认关闭的浮层目录、搜索与来源详情、拖拽、适宽/100%/可读比例/整图和全屏。全屏包含工具栏与候选声明；不以移动端阅读作为验收项。HTML 内嵌 SVG 与数据；下载 Mermaid/SVG 时需保留同目录的对应文件。

## 图形与安全检查

渲染先检查 SVG 安全性，再做有限 M/L 路径直角化，保留原始首尾端点，最后核验实际 SVG 几何。检查节点互相重叠、线穿节点、不同边共线叠压、标签归属及遮挡、路径校正后的标签脱离、形状真实边界和箭头端点。未知形状/标记或无法证实的几何不能借用矩形包围盒假定通过；正常正交交叉会提示人工判断。

只消除可安全辨认的微小折点；无法处理的路径明确阻断，不重新猜连接关系。当前几何检查对可测矩形、圆角矩形、椭圆和凸多边形有支持；也支持单条闭合凸 path 中控制点严格位于弦段上的 C 命令（实际是直线，Mermaid classic 起止节点使用这种输出），不会采样近似任意曲线。开放、多轮廓、非凸、非线性曲线或复合形状（如圆柱数据库形状）继续阻断。端点回穿按已验证形状内部检查，不用菱形的矩形包围盒冒充内部；边标签通过精确 ID（含 ELK 的 `_0` 后缀）归属，不能按文字或距离匹配。需要扩大支持时先补实际 SVG 证据与回归，不能取消检查。

每条有文字的边必须核对本边标签，不以“图中存在相同文字”代替。内嵌 JSON、文本、属性均需转义；SVG 禁止可执行元素与远程资源。查看器有内容安全策略和精确脚本哈希；修改查看器脚本须同时更新哈希并运行测试。

自动检查不代替实际视觉比对。浏览器可用后至少核对多角色、密集分支、回路、长中文条件与旁注，确认缩放、全屏和下载；与 PlantUML 对照时使用同一份业务事实。只有准备和模拟 DOM 测试时，应明确写“实际浏览器绘图及视觉确认未验证”。

## 可选 Axure 静态输入助手

保留 tools/mermaid-flow/scripts/inspect_axure.py，用于用户指定 ZIP 的安全解包和静态元件清单。其输出只是需求输入候选，不自动确认文字、可见性、线的端点或业务去向：

    python3 <skill-dir>/tools/mermaid-flow/scripts/inspect_axure.py prepare --input <source.zip> --out <new-source-dir>
    python3 <skill-dir>/tools/mermaid-flow/scripts/inspect_axure.py page --root <source-root> --html <target-page.html> --out <new-inventory.json>

不包含会执行来源 JavaScript 的 capture_axure.mjs。只有静态方式不能辨明的内容保留待确认；不把原型跳转当业务连线。原图需先经过正常需求分析与确认，再由 SC/FLOW 候选入口绘图。只复制 SKILL.md 无法运行此工具。

## 维护与来源

基于用户提供的“复杂流程图转Mermaid_Skill.zip”中 convert-axure-to-mermaid 1.0.0 改造。原 ZIP SHA-256：1436e951f3c6f50ea161a774c90d59fa34040a2f23fd31fd4c30a42c08e2ba1e。复用其静态提取、图生成、有限路径处理与查看器基础；新增来源适配、证据绑定、保守几何门禁与安全/桌面适配。原包的预生成示例和测试声称不作为本工具的验证证据。

维护测试：

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s <skill-dir>/tests -v
    node --test <skill-dir>/tools/mermaid-flow/tests/*.test.mjs

这些测试含隔离来源校验及模拟 SVG DOM，不会启动浏览器。Mermaid 专门泳道语法可作为后续独立评估项，但当前工具不生成 swimlane-beta，不自动升级为正式泳道后端。
