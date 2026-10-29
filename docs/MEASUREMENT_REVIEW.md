# 测量证据与复核：怎么配置，怎么验证改进

dev7 将“引文在原文中出现”和“数值、单位在指定测量片段中出现”分开记录。
程序做确定性的原文与位置检查；模型负责提出候选及其上下文。Jev 尚未接入，
程序不会因此把候选标为事实已核验。

## 一条测量如何保存

以合成句子“样品甲位于井段 3100–3117.5 m，厚度 17.5 m”为例：

- `fields.value.quote` 与 `fields.unit.quote` 都应是“厚度 17.5 m”。
- `measurements.reading.context_quote` 也使用“厚度 17.5 m”。
- `value_text` 为 `17.5`，`unit_text` 为 `m`，`unit_quote` 为空字符串。
- `conditions` 单独保存“井段 3100–3117.5 m”及对应原文引文。

结果的 `candidate.measurements.reading` 保存整条上下文的位置、数值片段的位置、
单位片段的位置，以及关联的条件字段名。条件引文仍在 `candidate.fields.conditions`
中。这样，井段里的 `m` 不会被拿来给厚度凑单位。

如果同一句出现厚度和深度，要提供两个不同的最小测量表达式。如果无法明确分开，
保留候选并复核。程序不会自动选择“最近的”或“看上去最合适的”单位。
`measurement_multiple_values` 是保守提示：上下文里还有数字需要人工看，
它不证明那里一定是另一项测量，也可能是未分开的条件或标识符。

表格没有逐行写单位时，`unit_quote` 可以引用原始表头。程序检查数值和单位是否位于
同一张简单管道表格的同一列，保留 `unit_context` 表头引文；列关联不通过时不会给单位
猜一个位置。此检查支持带首尾 `|` 的简单表格；复杂表头、合并单元格、其他表格写法
应进入复核。分块时重复携带的表头仍映射到原始 Markdown 的位置。

## 任务文件必须声明需要的检查

旧任务可以继续运行，但不会自动获得测量证据组，也不会凭字段名称推断情景要求。
下面是一个完整的合成配置，可另存为自己的 `task.dev7.json`。字段与取值由任务决定，
公共内核没有固化领域参数名。

```json
{
  "name": "参数与计算情景",
  "description": "分别保留主体、参数、数值、单位及计算前提。理论量、调整后量和总量不得混为同一情景。保留重复表述和相互矛盾的来源。",
  "record_types": {
    "observation": {
      "fields": {
        "subject": {"type": "string", "required": true},
        "parameter": {"type": "string", "required": true},
        "value": {"type": "string", "required": true, "description": "保留原文数值写法，不换算。"},
        "unit": {
          "type": "string",
          "not_applicable_when": {"parameter": ["校正系数", "比例"]}
        },
        "conditions": {
          "type": "string",
          "description": "包括反应比例、完整反应假设、理论或调整后情景；另引原文，不编造。",
          "required_when": {"parameter": ["理论容量", "调整后容量", "总容量"]}
        }
      },
      "identity_fields": ["subject", "parameter"],
      "measurement_groups": {
        "reading": {
          "value_field": "value",
          "unit_field": "unit",
          "condition_fields": ["conditions"]
        }
      },
      "comparison_fields": ["parameter", "value", "unit"]
    }
  }
}
```

`not_applicable_when` 仅分类空字段，不会删除或否定原文明示的值。例如无量纲比例仍可明确报告 `%`，此时单位保持 `reported`。

`required_when` 和 `not_applicable_when` 按模型返回的字段值精确比较；同一规则中的
多个字段必须全部满足，每个字段的列表表示允许的取值。它们不是全文关键词识别器，
也不会纠正模型给错的参数名。要在自己的任务中列出实际使用的名称，并人工检查
理论量、调整后量与总量的归属。空字符串、纯空白与 `null` 都按未提供值处理。

在 `RecordSpec.condition_fields` 中声明的字段始终必需；
`measurement_groups.*.condition_fields` 则声明关联关系，不自动让每条观测都必须有条件。
有些参数才需要条件时，使用 `required_when`。必需要求优先于不适用规则；避免给同一
情形同时配置矛盾要求。

## 怎样读新结果

| 内容 | 含义 | 不能据此得出的结论 |
| --- | --- | --- |
| `reported` | 模型提供了非空值 | 值一定正确 |
| `not_reported` | 模型没有提供可选字段，记为信息提示 | 原文一定没有报告 |
| `not_applicable` | 满足任务声明的不适用规则 | 模型可以自行把缺失都当成不适用 |
| `required_missing` | 必要字段或命中的关键情景缺失，需要复核 | 程序已经补出了正确情景 |
| `evidence.status=matched` | 引文与原始字符位置对应 | 参数、数值、单位一定属于同一观测 |
| `measurements.*.issues=[]` | 当前机械检查未发现问题 | 语义已核验；`semantic_status` 仍为 `not_verified` |

`review` 列表保留两种级别：`severity=review` 需要复核，`severity=info` 是信息提示。
可选空字段不会再同时产生“缺证据”和“缺值”两条阻断问题；必要条件缺失仍会进入复核。
`needs_review=false` 表示当前程序检查未发现需复核问题，仍不是接受决定。

`links` 保存候选之间的待确认线索，原候选 ID、数值和出处均保留：

- `possible_repeat`：配置的比较字段相同，主体与已声明情景的字段值没有差异或缺失；引文仍可能需要复核。
- `possible_conflict`：比较字段相同，但主体或其他身份字段不同。
- `insufficient_context`：存在缺失的主体或情景，暂不能归为同一事实。

这些是候选关联提示，不是自动事实合并。不同井恰好同值也可能产生“可能冲突”提示，
需要对照图号、段落和中英文图注确认；不会自动选定一个井号。
已明确不同情景的观测不会仅因数值相同而建立重复关联。只有相同原文位置的重复候选
才可能在原有去重步骤合并。

`review.md` 展示字段状态、上下文与片段位置，并限制长匹配列表的显示长度；完整位置在
`extraction.json` 中。含数学定界符的证据以代码文本显示，避免浏览器把原文再次当作
LaTeX 渲染。这个显示修复不改变匹配逻辑、原始引文或 JSON。

## 在文章或自选片段中测试

先保留旧结果，再选一个包含多测量、关键计算前提、表头单位的短片段。片段通过后再跑
全文。下面的 PowerShell 示例使用占位路径，请换成真实绝对路径，并保存上面的任务配置。
`extract` 会调用真实模型，可能产生费用；它不会退回演示数据。

```powershell
$repo = "C:\Work\AttestExtract"
$paper = "C:\Work\research\paper-or-excerpt.md"
$task = "C:\Work\research\task.dev7.json"
$runDir = "C:\Work\research\runs\$(Get-Date -Format 'yyyyMMdd-HHmmss-fff')"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
& "$repo\.venv\Scripts\python.exe" -m attestextract extract `
  --env-file "$repo\.env" --structured-output off --format-retries 0 `
  --input $paper --task $task `
  --output "$runDir\extraction.json" --report "$runDir\review.md" `
  --log-dir "$runDir\diagnostics"
```

保持原文、模型与服务商设置一致，分别保存新旧输出。开始对照时关闭格式重试，
避免把新增调用的效果混在一起；稳定后可显式使用 `--format-retries 1`。
模型存在随机性，一次前后对比不能单独证明提示词的长期效果。

人工预先挑选 10–20 条观测，写下正确主体、参数、数值、单位、条件及出处，再核查：

| 核查项 | 实质提升的判断 |
| --- | --- |
| 关键计算情景 | 理论与调整后结果分开；条件遗漏仍会被指出，而非全部变成无需复核 |
| 数值和单位的位置 | 位置切片确实恢复目标片段；没有从条件、另一测量或 LaTeX 命令中取单位 |
| 同句多个测量 | 绑定正确，或明确进入复核；错误绑定不能因引文存在就通过 |
| 空字段噪声 | 可选未报告字段降为信息；无量纲只在任务规则支持时标为不适用 |
| 重复和冲突 | 所有实际观测与原文出处仍在；未按同值删记录，也未自动决定有争议的主体 |
| 覆盖率 | 手工列出的观测仍被抽到；复核减少不能以漏掉困难观测为代价 |

同时记录“正确抽出的条数 / 手工预期条数”“错误绑定条数”“关键条件漏报条数”和
“人工查看每条候选所需时间”。复核总数下降本身不是质量提高的证据。

旧响应可通过 `ReplayBackend` 离线回放，验证原文定位、空字段分类等确定性变化，
但旧响应没有新 `measurements` 内容时，会明确报告 `measurement_missing`，
不会补造数值和单位位置。回放不能评价新提示词对真实模型的影响。

## 离线回归与格式兼容

在安装了开发依赖的仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_measurement_evidence.py tests/test_reporting.py tests/test_report_cli.py
```

测试使用独立合成输入，覆盖中文与 emoji 位置、条件中的单位、多测量、数值子串、
LaTeX 命令、表头列映射、关键空条件、重复和冲突线索，不调用付费模型。
JSON Schema 的接口问题另见 [诊断说明](SCHEMA_DIAGNOSTICS.md)。

新结果使用 `schema_version: "0.2"`，仍能读取旧 `0.1` 结果。旧结果未存储的新检查
不会凭空出现，报告对未记录的字段状态会标注“旧结果”。新增测量证据或模型返回字段的
变化可能导致重新抽取时的候选 ID 与旧版本不同；不能按候选序号或旧 ID 强行对齐。
对比时以原文、主体、参数、情景与出处为依据，保存旧文件，不覆盖历史输出。
