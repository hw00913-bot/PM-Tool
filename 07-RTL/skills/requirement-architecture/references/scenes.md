# S1 场景梳理

输入是本轮确认的需求契约及修订。以角色达成一个可以独立验收的业务结果作为场景边界，记录触发条件、前置状态、主流程、异常分支和结束状态。用需求编号指出每个场景承载的规则。多个页面可以属于同一个业务结果。

登记到 `requirements.scenes`。每条填写 `id`、`name`、`requirement_ids`、`actor`、`trigger`、`preconditions`、`main_flow`、`alternatives`、`outcome`。有跨场景前置事实或事件时写明它如何影响当前场景，并用可选的 `depends_on` 引用相关场景。结果及反馈归属实际处理该业务的场景。共享接口的技术字段和版本记录在接口契约。

每个有效场景有需求依据和可观察的结束状态。发现新规则或冲突时，记录受影响条款和待澄清问题。 [Spec Kit 的独立验收场景](../../references/requirements-workflows/SKILL.md#slice-scenarios)

场景边界取决于可实测的交互时，在 `--step scenes` 的 Skill 调用加 `--purpose prototype`。若本步已登记且只是补读参考，省略 `--s1-record`。记录试验验证了哪个边界及还需确认什么，再把确认的结论写入场景。 [pstack Prototype](../../references/pstack-workflows/SKILL.md#prototype)
