---
name: engineering-verification
description: 为 RTL 的暂存提升、故障接续、文件入口验证、界面状态和契约联验提供本地工程参考。按当前角色入口链接读取对应小节。
---

# 工程验证参考

以下上游位置于 2026-11-11 核对。

## Merge queue

S3 先在当前父版本上生成暂存候选。将候选、父版本和对应证据交给同一个提升操作。父版本推进后，以新父版本重新暂存，在新结果上执行受影响用例。多份各自通过的提交合在一起时，以组合后的验证结果为准。

上游位置为 `rust-lang/bors` 的 `docs/design.md` 中 **Auto builds** 小节。来源版本为 `main`。

## Conditional ref update

提升或恢复合入前，从运行台账取得预期旧引用和目标引用。读取实际 Git 状态，用包含预期旧值的更新操作完成切换。旧值不符时回到台账和现有恢复入口核对。回滚同时归档被撤销的候选时，归档与引用更新属于同一次 Git 引用事务。

上游位置为 `git/git` 的 `Documentation/git-update-ref.adoc` 中 **DESCRIPTION** 和 `--stdin` 事务说明。来源版本为 `master`。

## Idempotent recovery

重试原操作前，先对照已登记的意图、预期状态和实际对象。对象已到达预期状态而台账缺回执时，按原编号补记。仍处于已知起点时按原意图继续。遇到其他状态时保留现场并定位差异，再按 RTL 现有恢复流程处理。

上游位置为 `temporalio/documentation` 的 `docs/develop/python/best-practices/error-handling.mdx` 中 **Make Activities idempotent** 小节。来源版本为 `main`。

## File URL verification

从实际待验的 `index.html` 构建文件 URL，在浏览器打开并核对实际入口。RTL 的 S4 在正式落盘路径、登记的浏览器及地图实际入口执行冻结断言，保留步骤位置和原始报告。包含子页面或资源时，覆盖用户会实际访问的路径。

上游位置为 `microsoft/playwright` 的 `tests/page/page-goto.spec.ts` 中 `NA-002` 和 `NA-004`。来源版本为 `main`。

## Design tokens and states

按界面用途选项目风格基线中的语义 token。共用组件把默认、悬停、键盘焦点和禁用状态映射到对应 token，优先用共用配置修复多个场景间的漂移。联验时检查实际渲染，涉及禁用的业务规则另核对交互是否被阻断。

上游位置为 `primer/primitives` 的 `DESIGN_TOKENS_GUIDE.md` 中按钮与交互状态示例。来源版本为 `main`。

## Consumer mock and provider verification

使用方可以按已确认接口用 Mock 先自测。联验时回到实际候选，定位提供方真实行为，再验证使用方对该结果的处理。交接写明两侧实现、契约版本和用例，便于从失败结果定位责任场景。

上游位置为 `pact-foundation/pact-specification` 的 `README.md` 中 **Introduction** 小节。来源版本为 `master`。

## Verified version pairs

按实际提供方版本、使用方版本和契约边登记验证。存在兼容声明时，再查该组合的实际联验证据。组合改变后，按本轮冻结锚点匹配的套件取证，并绑定实际暂存操作与候选。

上游位置为 Pact 官方文档 **Can I Deploy** 中介绍 Pact Matrix 的段落。对应文档路径为 `pact_broker/can_i_deploy`。
