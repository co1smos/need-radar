# Need Radar：ChatGPT × Hermes 设计讨论记录

**状态：已收敛，没有当前必须由用户决定的设计问题。**

Hermes session：`20261006_114451_8a8410`。从 bootstrap、代理问答到文档定稿始终沿用同一 session，没有重开。

本文件是讨论索引与结论，不是具体问答全文。

**具体问题和当时的回答请读：[Q1–Q30 问答全文](qa-transcript.md)。** 新文件保留 Hermes 原始提问与建议、ChatGPT 对应的批量回答和 Hermes 实际结束消息，已核对同一个 Hermes session 的公开消息。实际是一批 30 问、一次批量代理回答、一次收敛确认，不是 30 轮分别往返。

## 对话过程

| 轮次 | 内容 | 文档 |
|---|---|---|
| 输入 | 用户已经确认的目标、约束、实验设计以及尚未实测的依赖假设 | [owner-brief](../owner-brief.md) |
| Bootstrap | 要求 Hermes 使用 grill-with-docs；ChatGPT 代理可逆决策，只上报真正需要用户意图的问题 | [bootstrap](00-bootstrap.md) |
| Hermes 第一轮 | 30 个检查点，覆盖实验、证据、judge、故障、报告、可观测性、权限和验收 | [Hermes frontier](01-hermes-frontier.md) |
| ChatGPT 代理回答 | 逐组回答 Q1–Q30，纠正双重 canonical report、证据跨臂借用及个人反馈模块等风险 | [proxy answers](02-proxy-answers.md) |
| Hermes 第二轮 | 阅读回答和依赖核查记录后返回 `FRONTIER_EMPTY` | [closure](03-hermes-closure.md) |
| 独立定稿轮 | Hermes 创建 design.md 与 decisions.md；ChatGPT 做链接、边界和一致性检查 | [design](../design.md)、[decisions](../decisions.md) |

## 收敛后最重要的边界

**只改 extraction。** 本轮 v0 找 explicit pain，v1 找 workaround/latent friction；数据字节、source、retrieval、模型和资源上限、输出结构及 judge 规则保持一致。以后测试 source/retrieval 时，可以有意改变抓到的数据，但必须保留各臂 snapshot，并固定下游 extraction/judge。

**公共过滤不能提前替某个策略做判断。** 去掉无效数据、明确重复和客观限制即可；不能先用 complaint 关键词筛一遍，再声称 workaround strategy 不好。

**统一标准，不同发现角度。** 最终判断始终是具体、真实、有价值、sizeable、可解决且有合理切入口的 friction。手动操作本身不等于痛点；单个严重案例可以成立，但不能凭空推成“大量用户”。

**不让证据跨臂泄漏。** 先各自在同样的规则下整理候选、评测，再做跨臂匹配计算重合与独有发现；不能先混合两边 evidence 再评判谁更好。

**正式报告与实验故障隔离。** Shadow 或 judge 出错不拖垮合法的 serve 报告，也不能把 shadow 自动顶替到正式渠道。缺少评测时明确显示未评测，不伪造通过结果。

**Markdown 是最终报告的唯一内容源。** 候选 JSON 属于中间数据，不是第二份可独立编辑的报告。HTML/PDF/Discord 来自同一份冻结的 Markdown；晚到的评测不静默改写已经发出的文件。

**可观测性追到 deterministic 代码。** 保存版本、配置、输入和转换记录、context/truncation 决策及可复现 prompt 内容；只有 hash 不足以查错。多个执行用各自 trace，通过 snapshot/run/artifact IDs 关联。

**不增加个人喜好模块。** 不评估“用户想不想做”，不收集个性化推荐反馈。产物到发现并证明 friction 为止。

## 已记录但暂不同时实施的实验例子

| 维度 | 后续可比较的例子 | 比较时不变的部分 |
|---|---|---|
| Source | 精选 AI-dev communities vs 更广泛开发者 communities；Reddit vs GitHub Issues | 可比较的检索资源策略、extraction、judge |
| Retrieval | recent feed vs 问题/workaround queries；posts only vs posts+comments；keyword vs semantic search | source universe、extraction、judge |
| Extraction | explicit pain、workaround、重复决策、failure/recovery、自建脚本、missing capability | 同一份冻结输入、模型预算、输出结构、judge |

任何一轮只测试一个维度；表格是 backlog，不是多套同时上线的 pipeline。

## 当前需要用户决定什么？

**没有阻塞设计收敛的问题。**

真实数据接入、付费额度、数据保留/外部 telemetry、Discord 频道与发送时间等仍未获授权，单独记录在 [activation-checklist](../activation-checklist.md)。这些是将来启用时确认的事项，不是为了完成本次讨论必须回答的问题。

Treg/OpenMagpie/Agent-Reach 的选择仍以实际能力验证为准；本次查阅文档不等于已在 VPS 完成抓取实测。

## 未执行的事项

未实现应用代码，未安装依赖，未创建 cron，未发送 Discord 消息，未调用付费社交数据接口，未配置凭证，未部署，未初始化 Git。

接续 session 的方法和运行记录见 [session.md](session.md)。
