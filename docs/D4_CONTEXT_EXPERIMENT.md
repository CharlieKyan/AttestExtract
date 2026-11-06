# D4 上下文对照实验

这个实验分别检查标题／表头继承和相邻段落带来的收益与风险。离线探针只回答“模型能看到哪些原文”“定位是否增加歧义”，不能证明模型抽取更准确。真实小样本仅形成描述性比较，不据此推断跨领域总体表现。

## 四组固定对照

| 组 | 代码 | 上下文 |
| --- | --- | --- |
| dev18 | 冻结提交 `a95bf81` 的源码 | 原始目标正文 |
| none | 当前代码 | 同一目标正文 |
| structure | 当前代码 | 同一正文及可追溯的标题、表头、表注 |
| neighbors | 当前代码 | structure 加本节相邻完整段落，最多 500 字符 |

各组目标正文的字符区间完全一致，任务、模型、温度及输出限额一致。原有自然分块另外统计覆盖、块数及总输入字数，不把不同正文范围当作上下文收益。准备结果同时比较 dev18 与 none 的完整请求；若请求不同，必须解释差异，不能宣称只改变上下文。

基线通过只读 `git show` 冻结到私有实验目录，保留提交和源码哈希。没有移动标签或创建工作树。公开脚本内含七个手工设计的合成场景：标题对象、邻段条件、表头单位、邻段另一对象、同值重复、跨章节、仅上下文出现的数值。前后两类风险不能用召回增加抵销。

## 离线准备

在已安装项目的环境中运行，下面路径是使用者自行指定的新目录：

```powershell
python scripts/d4_context_experiment.py freeze-baseline --output PRIVATE/baseline
python scripts/d4_context_experiment.py offline --baseline-source PRIVATE/baseline/src --output PRIVATE/offline
python scripts/d4_context_experiment.py prepare --bundle PRIVATE/source-cases.json --baseline-source PRIVATE/baseline/src --env-file .env --output PRIVATE/pilot
```

`source-cases.json` 是案例数组，每例包含 `id`、原样 `source`、`target.start/end`、任务 `task`、独立预期 tuple 列表 `gold`，可选 `mineru_content` 和 `hints`。人工预期在模型调用前固定，必须记录来源、原文件哈希、原文区间、核对者和审核范围。研究者／代理的原文核对不得标为专家金标。表格无明确语义标记时，用独立核对的 `header_rows`、`row_header_columns` 提示，不能只凭 `<td>` 猜表头。

准备命令不会读取凭据或发起网络请求。输出的 `cases.json`、`backend.json`、`planned-requests.jsonl` 由 `manifest.json` 记录哈希。每个请求还保存正文范围、实际 chunk、探针状态、自然分块覆盖及输入字数，便于批准前逐项检查。真实论文、原始回答和人工预期只留私有目录；公开仓库仅包含合成场景和代码。

## 有边界的真实 pilot

首轮固定三份私有片段，每份四组各一次，共 **12 次目标块请求**。一份正文内容已自足，作为“增加上下文不应破坏正确答案”的对照；两份为简单表和合并单元格表。它不是整篇抽取验收。

固定 DeepSeek Responses `deepseek-flash`、`json_schema`、temperature 0、关闭 thinking。每次最多 4,096 输出 tokens，总上限 49,152；timeout 60 秒、HTTP attempts 1、format retries 0，不细分、不改模型、不自动重试。调用前需要单独授权；发布授权不包含付费调用。

```powershell
python scripts/d4_context_experiment.py live --bundle PRIVATE/pilot --output PRIVATE/live-results
```

live 只执行已冻结的请求；材料改变即拒绝。`live-started.json` 和逐请求 reservation 记录本轮额度，同一 bundle 不能再次运行，即便改输出目录或发生中断。恢复／补跑须新建可审核计划；脚本不会自行重发。鉴权、余额、权限、模型或请求配置错误停止本轮；普通格式失败保留失败样本。API 凭据只在 live 阶段从明确 env 文件及进程环境解析，文件路径不等于凭据。

每次请求使用独立诊断目录，四臂相同案例不会覆盖彼此的响应。适配器直接抛出截断／拒答等异常时，也保存其已返回的 usage、elapsed、finish_reason 和响应元数据；截断正文留在对应 trace，不能将失败响应的已知 token 用量当作零。

## 评估顺序与决策边界

1. **先看对象或条件错归属**，逐条核对原文；新增错归属是阻止默认启用的信号。可定位的错误事实依然错误。
2. 看完整正确 tuple、错值／错单位、漏抽、重复、额外记录及格式失败。精确 tuple 分数对替代表达保持保守，存在争议的表述另列人工核对，不能临时修改 gold 迁就结果。
3. 看字段引文未解决项、仅上下文出现的 value、跨章节泄漏与新增歧义。共同的事后定位检查不冒充 dev18 当时已有的保护，也不是语义归属判定。
4. 报告每组输入字数、块数、服务返回 token usage 和耗时。无服务计费记录时不编造货币成本；失败但未返回 usage 的调用也保留已预留次数。

只有原文覆盖、真实切片、章节边界和责任正文检查通过，且逐例人工核对未发现新增错误归属，才讨论扩大试验。七个合成场景及三篇片段不足以支持全局默认启用邻段；默认 `none` 保持，结构上下文和邻段分别显式选择。

2026-11-06 的三片段描述性 pilot 中，首次 12 次请求因传输失败未取得模型响应；在用户另行授权的联网环境中重跑同一冻结请求，12 次均获得可解析响应，无自动重试。局部上下文补全与孤立合并表行的错列绑定同时存在，不能据此宣称总体优越。后者是固定正文压力输入；自然生产分块保留跨行单元格覆盖的行组，不能将该压力结果直接称为生产回归。真实费用未查询，不能将传输失败推断为零费用。真实正文、数值、原始回答、标注及逐例评分仅保存在私有实验目录。
