---
name: contract-coordination
description: "Define or revise a versioned RTL shared interface and describe compatibility, affected behavior, and evidence for the exact provider and consumer implementation pair."
---

# 跨场景接口约定与联验证据

输入包括本轮冻结的业务依赖、双方任务、当前激活契约与可用产物。一个接口边有稳定编号，提供方和使用方任务声明同一 `edges` 编号。任一参与方换成不同任务时建立新边。

## 提案与双方确认

1. **写可验证的约定。** 记录调用或数据入口、字段、状态、成功与异常行为、Mock 样例和双方共用用例。明确字段缺失、停用、越权等情况下的可观察结果。分别记录业务语义变化和接口表达变化。
2. **记录双方意见。** 提供方和使用方分别核对同一版本的字段、行为、样例、验证用例及各自的实现约束。保留两方的确认记录和未决分歧。
3. **区分自测与联验输入。** 使用方可用约定版本的 Mock 自测。联验依据实际提供方和使用方的产物，包含本次暂存的实现或符合组合要求的已集成实现。

以下示例展示共享契约及兼容组合的填写格式。行为描述、编号和步骤下标取自本轮冻结用例及双方确认。

```json
{
  "behavior": "新版响应增加状态说明，并保留上一版使用方识别的字段。缺少必需输入时返回约定的错误结果。",
  "cases": ["CASE_ID"],
  "case_anchors": {
    "CASE_ID": {
      "provider": {"feature_id": "PROVIDER_FEATURE", "step": 0, "interaction": 1},
      "consumer": {"feature_id": "CONSUMER_FEATURE", "step": 2, "interaction": 3}
    }
  },
  "compatible_versions": [{"provider": "VERSION", "consumer": "PREVIOUS_VERSION"}]
}
```

双方的 `step` 分别指向冻结用例中观察可见功能标识的位置，`interaction` 指向该方紧随其后的业务操作或断言。两个编号从 0 开始，按实际用例填写。联合套件包含双方步骤，场景可保留独立的自测套件。

从工具目录用本轮实际编号登记提案和双方确认。

```sh
python3 -m loop --data DATA contract-propose RUN EDGE VERSION PROVIDER CONSUMER SPEC_JSON --epoch EPOCH
python3 -m loop --data DATA contract-confirm RUN EDGE VERSION PROVIDER --epoch EPOCH
python3 -m loop --data DATA contract-confirm RUN EDGE VERSION CONSUMER --epoch EPOCH
```

契约状态流转见 [主控契约流程](../loop-control/SKILL.md#契约确认与改版)。

提案绑定该边当时的激活状态。当前版本推进后，以新版本编号提出修订并取得双方确认。已替代的版本及其确认记录供追溯。派发使用当前绑定，联验可引用现行契约明确允许的旧实现组合。

`compatible_versions` 是提案允许联验的版本组合。若新提供方移除旧使用方仍读取的字段，提案移除相应兼容对，并记录使用方受影响的行为和预期用例。兼容判断使用双方实际尝试绑定的版本。改版说明列出受影响任务、功能、用例、可复用的样例及需要共同验证的组合。记录共同验证关系与硬依赖之间的冲突。

## 验实际组合后再合入

按**提供方实现提交、使用方实现提交、目标契约版本和实际暂存候选**组织验证。此规则借鉴 Pact 的提供方与使用方版本矩阵。RTL 再绑定暂存操作、用例集修订、测试数据修订和实际执行者。 [Pact 版本矩阵](../references/engineering-verification/SKILL.md#verified-version-pairs)

| 暂存中的组合 | 联验证据要求 |
| --- | --- |
| 双方均绑定当前契约 | 在双方实际实现上运行当前契约的必跑用例。 |
| 当前提供方与已集成的上一版使用方 | 核对当前契约已登记该兼容对，并用双方实际实现取得该组合的联验证据。 |
| 实际组合缺少兼容声明，或缺一方实际产物 | 记录版本不匹配或缺失产物的位置。 |

联验使用候选实际入口及冻结用例，报告来自非作者执行者。`CONTEXT_JSON` 填写对应暂存操作编号、契约版本及双方实际提交。

```json
{
  "operation_id": "MERGE_OP",
  "edge": "EDGE",
  "version": "VERSION",
  "provider": {"task": "PROVIDER", "commit": "PROVIDER_SHA"},
  "consumer": {"task": "CONSUMER", "commit": "CONSUMER_SHA"}
}
```

```sh
python3 -m loop browser STAGED/index.html SUITE_JSON REPORT_JSON
python3 -m loop --data DATA evidence RUN contract STAGED_SHA --actor VERIFIER --context CONTEXT_JSON --report REPORT_JSON --epoch EPOCH
```

报告记录用例及数据修订、双方实际执行的步骤、观察结果、补充用例结果和证据编号。契约用例的通过与失败均按本轮冻结锚点匹配的套件归属。证据只覆盖所记录的操作、候选、版本和双方实现。适用用例失败时保留原报告，并使用原用例定义登记复验结果。 [运行手册的证据字段](../../docs/runbook.md#浏览器验证合入和交付)

## 产物要求

- 契约提案、双方确认、接口样例及联验用例以同一契约版本定位。
- 影响说明登记接口字段或行为变化、兼容组合、受影响任务与用例。分歧和 Mock 差异附具体样例与版本。
- 联验结果记录双方提交、实际候选、执行证据，以及不一致或未验证的条件。
