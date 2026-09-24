# 多 Agent 诊断

在“智能诊断”的“诊断模式”中选择多 Agent 协同诊断（默认）或单 Agent 诊断（对照）。上传与导航保持原样。

## 执行流程

1. 日志分析 Agent 使用搜索、上下文、统计三个工具调查日志。
2. 有日志证据后，源码定位 Agent 和故障知识 Agent 在两个工作线程中并行执行。
3. 源码角色只有源码工具；知识角色只有知识库检索工具。无源码或无知识库时跳过对应分支。
4. 汇总节点只调用一次模型，结合各阶段状态和工具证据生成报告，不再调用工具。

这是基于 LangChain 专业 Agent 和 Python ThreadPoolExecutor 的显式工作流，未引入 LangGraph。模型实例、回调和调查结果按请求隔离；工作线程不操作 Streamlit 会话。

## 证据与失败处理

- StageResult 是结构化交接对象，包含角色、状态、摘要、工具证据、原因、耗时和调用量。
- 证据由代码从工具响应提取，不从模型文字推断。错误响应、空检索和仅有文件列表不能作为诊断证据。
- 交接保留完整记录并控制字符预算，省略时标记截断；完整工具响应仍可在调查过程中查看。
- 历史问答仅帮助理解追问；每次运行重新调查当前资料。
- 日志无证据时停止后续推断。源码/知识分支失败时，仍尝试汇总日志证据，报告标记为未完整完成。
- 汇总失败时返回阶段状态，保留调查过程；失败轮不加入前端追问历史。
- 每个专业 Agent 最多 5 次执行迭代，执行器时间预算 120 秒；每次模型请求超时 60 秒、最多一次重试。执行器只在迭代间检查预算，因此这些不是整个请求的硬超时。
- 模型总 Token 来自接口返回，不含 Embedding，缺失用量时显示 0。耗时包含本地处理与 API 等待。
- 引用正确性和修复建议仍需要人工核对。工具证据交接与提示词约束不等于自动证明模型结论正确。

## 文件职责

- agent/diagnosis_agent.py：模式分发和原有单 Agent 基线。
- agent/workflow.py：日志优先、并行分支、汇总及失败降级。
- agent/specialists.py：专业 Agent 执行器和真实工具证据提取。
- agent/state.py：阶段结果结构。
- prompts/specialist_rules.txt：共享约束。
- prompts/log_specialist.txt、source_specialist.txt、knowledge_specialist.txt：角色职责。
- prompts/synthesis_prompt.txt：汇总核对规则。
- evaluation/compare_agents.py：真实接口对照运行。

## 检查

不调用模型的测试：

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

实际对照（会调用模型并消耗额度；需配置 .env）：

```powershell
.\.venv\Scripts\python.exe -m evaluation.compare_agents --mode both
```

可加 `--knowledge` 使用已有知识库，也可用 `--log`、`--sources` 和 `--question` 指定相同输入。两种模式均从空历史开始，报告和耗时/调用量记录保存至 evaluation/results/，不会纳入 Git。

对每份报告人工检查：根因是否正确；日志 ID/行号是否真实；源码定位是否匹配；无依据时是否说明限制；修复建议是否可执行。多 Agent 更复杂也可能更慢、更贵，不预设它一定优于单 Agent。原“项目评估”仍只验证本地解析与检索，不代表多 Agent 准确率。
