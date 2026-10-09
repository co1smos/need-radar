# Need Radar

独立项目：持续发现 AI 开发/学习中具体、有价值、可解决、有合理规模切入口的 friction。

**当前状态：设计讨论已完成；ticket #1 离线 serve tracer、ticket #7 合成离线 assessment 与 ticket #13 HTML 投影已实现。未连接实时来源、模型、定时任务或 Discord。**

项目路径：`/home/ubuntu/projects/need-radar`

## 整体架构图

![Need Radar 整体架构](docs/diagrams/system-overview.svg)

[浏览器查看架构图](docs/architecture.html) · [中文说明与 Mermaid 源码](docs/architecture.md)

模型已选 **DeepSeek V4.1 Flash**，Discord 频道已指定为 **`1557157266824634469`**。这里只更新设计记录，没有改全局 Hermes 配置、启用任务或发送消息。凭证和实际接入见 [runtime selections](docs/runtime-selections.md)。

## 从这里读

| 文档 | 用途 |
|---|---|
| [整体架构图](docs/architecture.md) | 已排版 SVG、浏览器版、可编辑 Mermaid 和模块边界 |
| [Ticket review](docs/tickets-review.md) | 唯一当前 ticket review draft；架构讨论已收敛，尚未发布到 tracker |
| [Target & source scope](docs/target-scope.md) | AI application-layer 目标边界；核心 Reddit + X；GitHub 可选且不阻塞核心；HN backlog |
| [Runtime selections](docs/runtime-selections.md) | 已确认模型/频道、Treg 凭证何时需要、尚未启用的配置 |
| [具体问题与实际回答全文](docs/grill/qa-transcript.md) | Q1–Q30 原始提问、Hermes 建议、对应的代理回答及实际结束消息，已核对原会话 |
| [ChatGPT × Hermes 讨论摘要](docs/grill/discussion.md) | 中文结论、原始记录导航和后续实验例子；不是问答全文 |
| [Design](docs/design.md) | 系统边界、extraction-only 实验、固定 judge、报告和完整 observability |
| [Offline assessment](docs/assessment.md) | 固定 rubric、arm-blind evidence、failure isolation 与合成离线验证 |
| [Decisions](docs/decisions.md) | 区分用户已定要求、可逆代理默认值与未来启用审批 |
| [Activation checklist](docs/activation-checklist.md) | 真实接入前的预算、数据处理、频道配置和技术核查；目前全部未启用 |
| [Owner brief](docs/owner-brief.md) | 本次对话中已经确认的意图和约束 |
| [Evidence notes](docs/evidence-notes.md) | 已核实的文档/CLI 信息及尚未实测的依赖假设 |

## 已定的第一轮实验

```text
同一份 source/retrieval -> 冻结输入
                           |
              +------------+------------+
              |                         |
       v0: explicit pain        v1: workaround/latent friction
            SERVE                       SHADOW
              |                         |
              +------ 同一固定 judge ----+
              |       非阻塞比较
              |
       正式 report.md -> HTML / PDF / Discord

Shadow 仅归档实验产物，不发布。
```

Judge 对两版使用相同目标：是否发现了真实、有价值、sizeable、可解决且切口合理的 friction。评测链路比较两版，但不阻塞正常的正式报告。

仅改变 extraction；source/retrieval 的不同策略保留为后续单变量实验。没有个人喜好或个性化反馈模块。

## Ticket #1：离线 serve tracer

使用 Python 3 标准库运行合成 fixture；不安装依赖、不访问来源或调用模型：

```sh
demo_dir="$(mktemp -d)"
python3 -m need_radar --output "$demo_dir"
```

CLI 输出 `status=success`，并在目录中保存有序 `snapshot.json`、解析后的 v0 `prompt.json`、明确标记为合成边界的 `model-response.json`、已校验的 `candidates.json`、规范 `report.md` 和 SQLite `lineage.sqlite3`。所有阶段保留输入/输出哈希和前序阶段链接。合成 fixture 不是实时来源或模型验证。

`report.md` 是规范报告，并明确标注合成/离线状态与验证边界：引用校验只确认摘录是保留文本的精确子串，不判断语义支持。Fixture 在任何产物写入前先做秘密值脱敏；不合规输入会退出失败，只写 `validation.json` 和对应 lineage，不会生成成功 snapshot。

## Ticket #13：确定性 HTML 投影

同一命令在 Markdown 成功后生成独立 `report.html`；HTML 只投影规范 Markdown，不重新总结。`report.html` 自带源 Markdown SHA-256，旁边的 `report.html.manifest.json` 关联父 report artifact ID、输入/输出哈希、renderer 配置和验证状态。渲染 trace/logs 保存在 `presentation/`。CSS 内嵌，不加载外部资源；只启用安全的 HTTP(S) 与文档内证据链接。渲染失败会记录失败 manifest/trace，但不会改变 serve 状态，也不会覆盖 `report.md`。

可重跑的合成离线检查（测试会拒绝网络、凭证路径和 canonical live-state 读取，并使用 Hermes `TMPDIR`）：

```sh
TMPDIR=/home/ubuntu/.hermes/cache/scratch PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests:. python3 -m unittest tests.test_report_html -v
```

本次实测：renderer/E2E 安全检查 `Ran 5 tests ... OK`；完整 Python offline suite `Ran 207 tests ... OK`。均为合成离线验证，不代表实时来源覆盖或目标 VPS 字体/分页验证。

HTML 只证明合成内容的确定性转换、UTF-8 中文文本保留、链接/转义和离线资源边界；没有验证目标 VPS 的字体/浏览器排版。PDF 暂缓：仓库没有已验证的 PDF renderer，且不安装软件。

脱敏覆盖敏感字段名、常见 token 形式以及 `Authorization`、`Cookie` 等凭证文本，是尽力而为而非通用秘密检测；未知格式仍可能漏过，因此不要提供真实凭证。无法解析的 JSON 也会保存不含原始输入的 `invalid_input` 记录及 lineage。

## Ticket #2：本地 observability

同一个离线命令也会保存 `selection.json`、`context.json`、`truncation.json`、`trace.jsonl`、`logs.jsonl` 和 `observability.json`。Trace 使用本地 trace/span/parent ID，保留脱敏后的阶段输入输出、prompt、合成模型调用、验证结果和报告；结构化阶段日志通过相同的 run/trace/span ID 关联，并记录阶段状态、耗时及 artifact/call lineage。SQLite 与 artifact lineage 共用稳定 run、snapshot、artifact、report 和 call ID。引用校验针对实际发送给模型的 prompt context。

Langfuse candidate boundary 保留 `trace.jsonl` 中的脱敏本地 span，并在 `langfuse-otlp.jsonl` 逐条写入 OTLP/HTTP JSON trace requests；离线契约测试检查 OTLP envelope、普通确定性 span、prompt 输入以及 trace/parent context。它不调用 Langfuse SDK，也不验证远端 ingestion。`observability.json` 明示 remote status 为 `unverified`、外部 export disabled，且脱敏覆盖为 best-effort/incomplete；本实现不读取凭证、不连接远端。合成 end-to-end check：

```sh
demo_dir="$(mktemp -d)"
python3 -m need_radar --output "$demo_dir"
cat "$demo_dir/observability.json"
```

预期 CLI 状态为 `success`，observability 中 `remote_export.status` 为 `unverified`。这只证明本地 synthetic slice，不是远端验证。离线回归（含子进程网络/受保护路径拒绝、Langfuse 边界本地 sink、成功模型调用下的上下文截断诊断）：

```sh
PYTHONPATH=tests python3 -m unittest discover -s tests -p 'test_cli.py' -v
```

2026-10-08 实测：CLI 返回 `status=success`，本地写入 10 个 span 和 10 条结构化阶段日志；`remote_export.status=unverified`、`enabled=false`，未尝试远端验证。

已运行的离线检查命令：

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q need_radar tests
```

## 接续约束

Hermes session：`20261006_114451_8a8410`，全程保留，详见 [session record](docs/grill/session.md)。

后续 agent 先读 [AGENTS.md](AGENTS.md)。ticket #1 仅实现离线 tracer；未做 Git 初始化、依赖安装、付费数据接入、cron 或 Discord 发布。任何实时接入与上线均需另行授权。
