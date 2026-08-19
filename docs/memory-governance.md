---
title: Long-Term Memory v2 治理方案
date: 2026-07-17
tags:
  - architecture
  - memory
  - chroma
  - langgraph
---

# Long-Term Memory v2 治理方案

## 1. 问题与真实数据审计

本次只读检查了本机 Docker Chroma 中 `user_id=1` 的长期记忆。审计时共有：

- 7 条 `task_summary`；
- 3 条用户 pattern；
- 0 条具有 schema-v2 质量元数据的 Active 记忆。

其中，同一个小说协作会话产生了 4 条摘要，包含“Token 耗尽”“任务未完成”“未调用工具”等失败过程；一次“写玄幻短篇小说”的任务约束又被推断成 `fiction_genre`、`story_format` 和 `multi_agent_collaboration` 三条长期偏好。

根因不是单一 importance 阈值过低，而是四条链路共同失控：

| 链路 | 原行为 | 风险 |
|---|---|---|
| 写入时机 | `save_memory` 在 `finalize_task` 之前后台写入 | 失败、取消、未验证任务也可能进入长期记忆 |
| 准入判断 | 对完整对话摘要计算 importance，达到阈值即存 | 长文本、失败分析、创作请求很容易得到高分 |
| 偏好提取 | 高 importance 即调用 LLM 推断 pattern | 一次性任务约束被误判为永久偏好 |
| 检索兜底 | 语义搜索没有摘要时，宽泛查询并补入旧摘要 | 无关历史被强行注入新任务 |

上下文压缩摘要还曾直接写入 Chroma，混淆了 Redis/checkpoint 工作记忆与真正长期记忆。

### 1.1 删除后仍召回的实机故障

2026-07-20 的实机复测发现：用户删除显式 `user_note` 后，新会话询问开发偏好仍回答“使用 uv”。数据与 Trace 共同证明这不是浏览器缓存：

- 父 `user_note` 已从 conversation collection 删除；
- 旧实现从该 note 派生的 `uv_dependency_management`、`pytest_testing`、`post_modification_checks` 三条 pattern 仍作为独立 Active 记录存在，而且没有来源 ID；
- 任务开始时的自动检索正确注入 0 条，但模型随后主动调用 `search_memory`，该第二检索入口绕过了统一的召回审计并返回残留 pattern；
- Memory Ledger 顶部把 conversation 与 pattern 相加显示 3，而当前 Task outcomes 标签为空，页面没有解释这 3 条其实位于 Preferences。

因此本轮按三个不变量修复：**派生记录必须有来源、删除必须级联、所有检索入口必须共用过滤与 Trace**。

## 2. 开源项目调研

星数为 2026-07-17 页面显示值，仅用于说明项目成熟度，不作为方案正确性的证明。

| 项目 | 当日 GitHub 星数 | 借鉴点 | 本项目决策 |
|---|---:|---|---|
| [OpenHands](https://github.com/OpenHands/OpenHands) | 81.1k | 项目知识以仓库级 microagent/指令存在，并按触发条件加载 | 长期工程知识必须有项目语义和相关性门槛 |
| [Mem0](https://github.com/mem0ai/mem0) | 61k | 从消息提取事实；无事实返回空；用 ADD/UPDATE/DELETE/NONE 或关联去重治理 | 不再存整段对话；写入必须可拒绝、可解释、可去重 |
| [Letta](https://github.com/letta-ai/letta) | 23.8k | 区分可编辑 core memory 与 archival/message search | 稳定偏好与任务历史分型；管理页可检查和删除 |
| [LangMem](https://github.com/langchain-ai/langmem) | 1.6k | hot path 与 background manager 分离，负责抽取、合并和更新 | 记忆整理放到任务终态后的后台路径 |
| [LangGraph memory template](https://github.com/langchain-ai/memory-template) | 示例项目 | schema 决定记忆形状；profile 用 patch，事件集合用 insert；使用 debounce 防重复 | v2 强类型元数据，偏好 upsert，任务结果作为受控集合 |

没有直接引入 Mem0/Letta：当前项目已有 Chroma、用户隔离、删除 API 和 LangGraph 生命周期，引入新存储层会扩大迁移面，也无法自动修复“何时存、存什么”的产品策略问题。

## 3. v2 决策

### 3.1 三层边界

1. **会话恢复记忆**：RedisSaver checkpoint，保存消息和 Agent 状态。
2. **上下文工作记忆**：压缩摘要和 transcript，只服务当前长任务恢复。
3. **长期记忆**：Chroma，仅保存通过准入的可复用工程结果和显式用户信息。

工作记忆不再自动复制到长期记忆。

### 3.2 自动写入准入矩阵

| 条件 | 结果 |
|---|---|
| `task_status != succeeded` | 拒绝：`task_not_succeeded` |
| 用户明确说“请记住/保存到长期记忆” | 接受：`user_note` |
| 非工程任务，例如小说、闲聊 | 拒绝：`non_engineering_task` |
| 只有 todo、task tracking、delegation 等编排调用 | 拒绝：`no_durable_evidence` |
| 工程任务有成功读写/命令/验证/文件变更，且 importance ≥ 0.65 | 接受：`task_outcome` |
| 与现有 Active 记忆距离 < 0.3 | 拒绝：`duplicate_active_memory` |

准入策略是确定性的。失败和明显无关任务会在调用摘要/importance LLM 之前被拒绝，减少费用和内部流式输出风险。

显式 `user_note` 不再经过任务总结模型扩写，直接保存为 `[User Note]` 原子内容。它不会再同时提取 3 条同源 pattern，因此 Memory Ledger 的记录数不再把一个事实膨胀成“四条记忆”。

### 3.3 v2 元数据

```text
schema_version=2
quality_status=active
memory_type=task_outcome | user_note
task_status=succeeded
admission_reason=verified_engineering_outcome | explicit_user_request
source=finalized_task
trace_id=<task trace>
execution_mode=single_agent | multi_agent
importance=<0..1>
content_format=atomic_note | structured_task_summary
retrieval_enabled=true | false
retrieval_count=<injection count>
last_retrieved_at=<timestamp>
```

### 3.4 偏好策略

- 只有“我偏好、我习惯、以后、默认、每次、remember、from now on”等明确长期信号才进入抽取器。
- “这次、本次、当前任务、这篇、for this task、this time”等一次性限定默认拒绝。
- Prompt 明确禁止从助手回答、工具选择或单次任务主题推断用户偏好。
- 相同 `pattern_type + pattern_key` 使用 upsert，更新值并增加 `evidence_count`，不再创建冲突记录。
- Active pattern 必须带 `provenance_version` 以及 `source_memory_ids/source_trace_ids/source_session_ids`；缺少来源的历史 pattern 动态降为 `legacy: missing_source_provenance`。
- 删除父 memory 时先删除所有关联 pattern，再删除父记录。没有逐来源值历史时，不尝试猜测性重建多来源 pattern，而是保守删除整条派生记录。

### 3.5 检索策略

- Agent 在**每次用户任务**检索，不再只在新 Chat 第一轮检索。
- 自动检索只读取 `quality_status=active && retrieval_enabled=true`；Legacy 和禁用记录只出现在审计候选中，不会注入。
- 英文和中文查询都必须通过 Chroma 距离硬门槛；中文再使用连续字符 bigram 与工程标识符重排，排除“你的/什么/问题/刚才”等通用词，并要求足够的有效重合。
- “刚才/上一条/上一个问题/previous message/last question”等近指问题不检索 Chroma；它们以当前会话 checkpoint 为权威来源，Trace 记录 `recent_conversation_reference` 跳过原因。
- 召回内容存放在任务级 `retrieved_memory_context`，每次 LLM 调用时合并到唯一 `SystemMessage`，不再伪装为用户消息；它也不写回消息历史或 Redis checkpoint，避免旧召回块跨任务累积。
- 没有相关结果时只记录空的 Recall receipt，不再执行宽泛兜底。
- “列出我的记忆”使用确定性 list API，而不是用向量查询假装完整列表。
- 自动上下文检索与模型主动调用 `search_memory` 是两个入口，但共用 Active、`retrieval_enabled`、语言相关性门槛、召回计数和 `memory_retrieval` Trace schema；工具检索不再成为审计旁路。

### 3.6 召回证据边界

四个概念必须分开：

1. **Stored**：记录通过准入并写入 Chroma。
2. **Recalled**：记录通过检索门槛并被选中。
3. **Injected**：记录进入了本次模型上下文；`retrieval_count` 在这里增加。
4. **Applied**：模型实际依据该记录作出正确行为。

当前系统可确定证明 1–3，不能从一次模型输出可靠归因第 4 步。因此 Trace 中固定记录 `application_status=not_attributed`，Memory Ledger 也明确提示 “recalled/injected 不等于 applied”。

## 4. Legacy 兼容与迁移

v2 初次迁移没有批量删除或覆盖用户已有 Chroma 数据。

- 缺少 v2 元数据的旧摘要和 pattern 在 API 中动态标记为 `legacy`。
- Legacy 仍可在 Memory Ledger 的 Legacy 过滤器中检查并逐条删除。
- Legacy 永远不参与自动上下文注入。
- 缺少来源的 schema-v2 pattern 即使元数据曾写为 Active，也会在读取时动态进入 Legacy，不再被 `search_memory` 返回。
- 后续如需批量清理，应先实现 preview API，展示记录数、session、原因，再由用户确认执行；不得在启动时静默删除。

本次故障不是批量迁移：用户已经删除父 note，并要求修复删除后仍召回的问题，因此只对已核实的三个孤儿 ID 执行精确清理：`uv_dependency_management`、`pytest_testing`、`post_modification_checks`。清理后 `user_id=1` 为 0 条 Active pattern；用原故障中的英文查询复跑 `search_memory` 返回 0 条结果。其余 3 条小说相关 Legacy pattern 和 4 条 Legacy summary 未被自动删除。

## 5. 配置

```env
MEMORY_ADMISSION_MIN_IMPORTANCE=0.65
MEMORY_RELEVANCE_MAX_DISTANCE=0.8
MEMORY_CJK_LEXICAL_MIN_SCORE=0.08
MEMORY_CJK_RELATIVE_SCORE=0.75
MEMORY_DEDUP_MAX_DISTANCE=0.3
```

阈值应通过记忆评测集调整，不能只凭主观感受降低。

## 6. 当前验证

- 当前仓库后端完整回归：442 passed；Ruff 通过。
- Ruff：本次修改文件通过。
- 前端完整回归：16 passed，其中 Memory Ledger 5 passed；前端生产构建通过。
- 本地 embedding 初始基线：6 个用例通过 5 个，Precision@3 27.78%，无关负例误注入率 100%，证明原始中文检索门槛不可接受。
- 加入中文词法重排和相对门槛后：6/6 通过，Recall@3、Precision@3、MRR 均为 100%，负例误注入率 0%，Forbidden injection 0。
- 原始报告见 `benchmarks/results/20260720T093639Z-memory-recall.json` 和同名 Markdown。该结果只证明小型合成集上的**本地检索与过滤**，不证明模型正确采用记忆。
- 真实模型是否正确采用已注入记忆仍未单独归因评测；现有 Agent v2 `deepseek-v4-flash` 25/30 报告不能替代该指标。

## 7. 下一阶段评测

当前 `memory-recall-v1` 已建立 6 个不调用真实用户数据、不调用聊天模型的检索用例。下一步扩展到至少 20 个 admission + recall + behavior 用例：

- 应存：明确长期偏好、已验证代码修复、可复用仓库事实、显式记住请求；
- 不应存：问候、一次性格式、小说任务、失败任务、取消任务、危险请求；
- 更新：偏好重复、偏好改变、同一工程决策补充；
- 检索：相关命中、无关拒绝、Legacy 隔离、跨用户隔离；
- 指标：admission precision/recall、duplicate rate、retrieval precision@3、Legacy leakage、平均额外 token/耗时。
- 真实 Agent 层另测：当前指令覆盖长期偏好的遵循率、回答引用/行为是否与被注入记忆一致。不得用 retrieval 命中率代替该指标。
