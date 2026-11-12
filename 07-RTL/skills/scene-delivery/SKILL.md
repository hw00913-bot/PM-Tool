---
name: scene-delivery
description: "Design, implement, self-test, and hand back one RTL business scene from an assigned attempt, with scoped Git changes and version-bound browser evidence."
---

# 场景设计、实现与自测

工作输入取自本次派发，包括尝试编号、任务包、冻结需求、授权工作区、基础提交、功能和用例、实际绑定的契约版本及上游产物。`skill.content` 与 `style_skill.content` 分别提供场景方法和项目风格。实现任务沿用 `input.design_handoff` 中仍与本次绑定相符的设计结果。发现缺项或版本冲突时记录受影响行为、相关编号和待补输入。 [Superpowers 实施者简报](../references/superpowers-workflows/SKILL.md#implementer-brief)

## 按尝试类型推进

| 尝试类型 | 专业工作 | 产物 |
| --- | --- | --- |
| **设计尝试** | 对照现行功能地图和场景需求，确定入口、动作、状态变化及异常反馈。列出需协商的接口约定、同版样例和验收用例。 | 非空设计 JSON 摘要，含场景方案、拟用入口、状态变化、契约与样例建议及阻塞。 |
| **实现尝试** | 在指定工作区和授权路径内修改，复用共享底座及已绑定的契约和同版 Mock，覆盖约定的成功、异常和保存回读。 | 实际源码提交、改动范围、自测证据、所用契约与风格版本。 |

## 自审、自测和入账

React 场景在工作区修改 JSX 或 TSX 源码。共享路由、组件和依赖锁文件以任务的授权路径为写入边界。Codex 执行者交回源码和检查结果，由 RTL 按 [Codex 收件步骤](../../docs/runbook.md#通过-codex-执行场景) 核对轮次、工具停工及授权范围，并生成或登记提交。React 对该提交用 `build RUN --attempt ATTEMPT --epoch EPOCH` 获取自测入口。依赖和生成文件由 Loop 在独立目录处理。静态模式使用已提交工作区的 `index.html`。

1. 从派发的基础提交到实际结果提交检查完整变更。对照任务包复核授权路径、必跑用例和异常反馈，清理临时测试文件。准备验证时用干净工作区和实际提交。
2. 自测使用本次尝试的实际入口、冻结用例和固定数据。核对页面反馈、写入结果及用例要求的回读。报告放在原型目录之外。
   有契约边的任务记录所用 Mock 和对应版本，列出需要用实际提供方和使用方产物复验的路径。 [Pact 的 Mock 与提供方验证](../references/engineering-verification/SKILL.md#consumer-mock-and-provider-verification)
3. 从工具目录对实际提交对应的入口生成报告。原始报告用于登记自测证据。

```sh
python3 -m loop browser ENTRY SUITE_JSON REPORT_JSON
python3 -m loop --data DATA evidence RUN self SHA --actor WORKER --context CONTEXT_JSON --report REPORT_JSON --epoch EPOCH
```

自测的上下文 JSON 为 `{"attempt_id":"实际尝试编号"}`。`SHA` 取报告实际验证的源码提交，`WORKER` 用本次尝试登记的执行者。手工宿主可在提交结果登记前对已生成的 Git 提交取证。Codex 收件产生提交后，使用该提交取证。 [验证与提交参数](../../docs/runbook.md#浏览器验证合入和交付)

场景交接摘要列出以下信息，并链接到实际提交和证据。

| 字段 | 内容 |
| --- | --- |
| 场景与尝试 | 任务 ID、尝试 ID、基础与结果提交 |
| 实现范围 | 实际路径、功能编号、实际页面入口及状态 |
| 依赖 | 契约边与版本、风格基线、上游产物 |
| 自测 | 实际入口、用例及数据修订、原始报告和 `self` 证据编号。列出通过、失败、阻塞和未执行项。 |
| 阻塞与待验证项 | 未决问题、缺少的依赖及其版本。涉及共享契约时，记录已用 Mock 以及待用实际产物核对的用户路径。 |

## 阻塞和偏差记录

- 自测失败时保留原始报告、复现步骤和实际结果，在修复后的提交上重跑受影响的用例。
- 契约行为、版本或公共组件不匹配时，指出所用版本、受影响的功能和文件，以及实际结果与约定的差异。
- 尝试失效、写入边界不清或已确认的业务规则、验收预期、风格例外需要调整时，记录冲突内容、已核对的输入和待裁决事项。
