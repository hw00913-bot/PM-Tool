# S1 需求分析

读取用户输入和原始资料。改版时查同一项目上一份已验收的冻结契约和变更记录，以原条款定位比较起点。按条款整理事实、需求契约草案及待决事项。

1. **提取有出处的内容。** 用稳定需求编号登记名称、角色涉及的目标、适用范围、业务规则和可观察验收。来源写到能定位原文的位置，例如文档章节、消息或已确认决定。改版沿用原规则的编号，注明现行行为和拟议差异。 [BMad 需求提取规范](../../references/requirements-workflows/SKILL.md#extract-requirements)
2. **检查疑点。** 查范围内外、角色及权限、数据归属、状态转移、失败和例外。把材料冲突列出来源及影响。将需要用户决定的事项列入待决清单，按确认答复更新对应条款并保留出处。推断和假设先留在草案中。 [Spec Kit 需求澄清](../../references/requirements-workflows/SKILL.md#clarify-requirements)
   能用观察回答的疑点，在 `--step analysis` 的 Skill 调用加 `--purpose prototype`。若本步已登记且只是补读参考，省略 `--s1-record`。记录隔离试验的观察结果、样例位置和仍需用户决定的取舍。 [pstack Prototype](../../references/pstack-workflows/SKILL.md#prototype)
3. **写可验收的契约。** 每条规则写清一个可以观察的行为。验收说明前置条件、触发动作和预期结果，包括关键失败时是否阻断写入。先核查条款是否自洽、范围是否明确、结果是否可观察，再回看原材料中会影响后续设计的决定是否有条款或出处。 [OpenSpec 规格写法](../../references/requirements-workflows/SKILL.md#write-testable-specs)
4. **整理契约与确认依据。** 草案保留每条规则、来源、待决事项和影响范围。供 `s1-confirm` 登记的契约包含 `revision`、`requirements` 和已清空的 `pending`，按本轮确认填写 `human_acceptance`。将同一确认依据写入基线的 `requirements.approval`，将当轮条款写入 `requirements.requirements`，基线沿用本次确认的修订编号。每条填写 `id`、`name`、`source`、`goal`、`scope`、`rule`、`acceptance`。业务规则、权限或验收预期改版时使用新修订。
