# S1 功能点拆分

输入是已确认需求契约和场景清单。沿主流程、例外、权限边界及持久化结果找出可独立描述的业务行为。一个功能点可以由多个用例验证。为每条用例写清前置数据、用户动作和通过时可观察的结果。

登记到 `requirements.features`。每条填写 `id`、`name`、`scene_id`、`requirement_ids`、`rule`、`cases`、`assertions`。断言以 `{case, step, checkpoint}` 记录。`step` 写实际断言和预期，`checkpoint.index` 是它在 `steps` 中从 0 开始的下标，`checkpoint.after` 是它之前按原顺序执行的全部浏览器操作（不含其他断言）。`after: []` 表示初始状态。保存、刷新等结果用对应操作之后的检查。样例见 [四步输入示例](requirements-example.json)。更新既有行为时沿用原功能编号。已确认移除的功能用 `status: removed` 保留历史定位。本轮移除要登记能证明旧行为已消失的必跑用例及实施任务。现行功能和本轮移除共同覆盖冻结的必跑用例，功能地图的场景列填写业务场景编号。

先按现行规格定位原有行为和功能编号，再在本轮的变更记录中登记实际差异与验收依据。 [OpenSpec 的规格与变更分离](../../references/requirements-workflows/SKILL.md#separate-spec-and-change)

登记到 `requirements.changes` 的字段是 `feature_id`、`kind`、`before`、`after`。类型为 `add`、`modify`、`remove`、`technical`、`verify`。只登记本轮实际涉及的功能。技术调整和仅复验分别保留事实及预期。规则修改和功能移除要冻结新版用例。变更条目的 `assertion_changes` 用一组 `{before, after}` 对应改版前后的验收断言。两侧分别填写旧轮和本轮功能中的完整 `{case, step, checkpoint}`。旧轮没有断言时才可填写 `before: null`。同一用例和时点不再保留已替换的旧预期。改变可观察结果的类型时，在同一对断言的 `retarget_reason` 中记录理由并一并确认，例如从核对控件文字改为验证该控件已隐藏。若结果相同且条件有变化，使用 `assertion_reuse` 记录用户确认和原因，再冻结包含新前置条件的用例。把这些变更条目完整放入 `s1-confirm` 输入的 `change_assertions`。 [OpenSpec 的规格改版方式](../../references/requirements-workflows/SKILL.md#write-testable-specs)

`requirements.changes` 的前后描述作为 S1 计划说明。RTL 对照上一份已验收契约与本轮冻结条款，生成 `normative_changes` 并用于交付记录的变更前后栏。当前规则和预期以本轮冻结条款及断言为准。
