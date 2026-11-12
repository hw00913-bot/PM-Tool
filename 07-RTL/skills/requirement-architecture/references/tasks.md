# S1 任务拆分

输入是本轮确认的需求、场景和功能清单。按可以独立交接的业务结果、写入范围和依赖切任务。一个任务可覆盖多个功能，一个功能也可由实现任务和共享任务协作完成。共用文件指定单一写入者。

登记到 `requirements.tasks`。每条填写 `id`、`name`、`feature_ids`、`input`、`deliverable`、`done`、`paths`、`cases`，按需填写 `deps`、`edges`、`risk`。写明可用的底座和前置证据，完成条件应指向可以核对的实际产物。任务包标明关联的冻结契约修订及授权路径。

分别记录设计输入、实现前置项和联验所需的实际提交。`deps` 表示须先合入的任务，`edges` 表示共享契约和联验关系。同批联验的 A/B 用 `edges` 表达共享接口关系。

核对从需求到场景、功能、任务和用例的链路。逐项报告缺少依据、用例或交接范围的产物编号及建议修正。冻结后的工程补充任务仍须引用已有有效功能和该功能的用例。 [Spec Kit 跨产物检查](../../references/requirements-workflows/SKILL.md#check-traceability) · [任务拆分模板](../../references/requirements-workflows/SKILL.md#plan-tasks)
