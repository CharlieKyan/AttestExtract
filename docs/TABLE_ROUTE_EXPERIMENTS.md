# A 路线表格实验：运行、查看与定位错误

dev28 已将 A 路线接入普通 `extract` 入口：默认自动识别完整表格并分流，
使用方法见[正文与表格统一抽取](TABLE_EXTRACTION.md)。本页保留独立实验入口，
用于冻结任务、参考答案和历史回答，做可重复的离线比较或有明确预算的真实实验。
实验目录不是普通用户必须准备的输入。A 的流程为：给原表格子编号 → 模型判断
数值属于谁、什么条件、什么单位 → 程序回原文核对 → 原样交付模型字段及独立检查信息。
用户提供抽取任务，无须指定列号或表格布局。程序不再自动改值、改单位、改关系或覆盖引用。

新实验清单为 `table-a-0.3`，结果契约为 `table-route-v3`，处理策略为 `observe_only`，只执行 A。
B 的转写、二次抽取和映射代码已删除；旧自动改写执行模式也已删除。
历史实验文件原样保留，可直接打开原来的静态 HTML 和 JSON；不能用新程序继续旧 B 请求。
旧 `table-a-0.2`、`table-a-0.1` 与历史 A/B 包仅可只读导入保存的 A 回答，不能继续旧运行。
离线回放不产生模型费用，也不能证明新的模型表现。

## 先运行不联网的合成样例

完整检查空字段、单条格式错误隔离、三套样例及中断恢复，可在仓库根目录运行：

```powershell
.venv\Scripts\python.exe scripts\table_results_smoke.py --output-dir ..\research\attestextract\experiments\table-results-check-01
```

每次使用新目录。该命令阻断网络连接，执行12个合成槽位，逐包生成报告并输出 `summary.json`。

在仓库根目录执行。每次新实验使用尚不存在的输出目录，保留之前的证据。
下列反引号是 PowerShell 换行符。

```powershell
.venv\Scripts\python.exe scripts\table_route_experiment.py prepare `
  --cases src\attestextract\examples\table_routes\cases.json `
  --output ..\research\attestextract\experiments\table-a-demo-01

.venv\Scripts\python.exe scripts\table_route_experiment.py check `
  --bundle ..\research\attestextract\experiments\table-a-demo-01

.venv\Scripts\python.exe scripts\table_route_experiment.py replay `
  --bundle ..\research\attestextract\experiments\table-a-demo-01

.venv\Scripts\python.exe scripts\table_route_experiment.py report `
  --bundle ..\research\attestextract\experiments\table-a-demo-01
```

打开输出目录的 `REPORT.html`。回放回答来自样例预设，检查的是给定回答时的程序处理。
再次运行同一目录不会重发已预留调用。不要把回放目录切换成真实运行目录。

| 合成文件（位于 `src/attestextract/examples/table_routes/`） | 用途 |
| --- | --- |
| `cases.json` | 并排参数、反应前后、转置、父子分组 |
| `edge_cases.json` | 组总量、明确共享条件、脚注条件、同格多个事实 |
| `failure_cases.json` | 错误值与单位、错格关系、未知来源等故障 |

故障样例预期出现红色诊断；核对诊断位置及正确记录是否受到影响，不是要求报告全绿。

## 原字段与定位结果如何分开保存

原始响应、解析后的结构、交付字段和历史候选不改动。定位结果是独立检查信息，不是对模型字段的修正。

1. 先找到模型自己引用的单元格或上下文编号。不存在的编号明确报告，不能借另一格补齐。
2. 若指定位置能逐字对应引文，独立记录命中位置；即使同格有重复文字也不会移动。
3. 若没有位置或位置错误，只在这个已引用片段内逐字寻找。恰好一处才定位；
   找不到、多处或重叠出现都显示定位问题，不去其他格子找一个相同数字。原声明位置不符时保留该问题，即使唯一查找找到了出处。
4. 字段、数值定位点和范围依据采用同一规则。定位成功后仍检查数值是否完整、
   单位是否被截短、条件关系和群体归属。位置可定位不代表科学关系得到核验。

例如完整引文 `材料密度/(kg/m3)` 起点写错，程序可在同格唯一找到它，并另外检查该引文是否支持单位
`kg/m3`；交付中的单位、引文和 offset 均保持模型原样。原格为 `10–20` 时，不能只截取 `10` 作为
完整测量。`aaa` 中的 `aa` 有两处重叠匹配，也不能默认取第一处。

这不是 LaTeX 数学等价搜索，不删除 OCR 字符，不自动换算单位，不推导缺失表头。
原文位置为 Unicode 字符下标的左闭右开范围，不是 PDF 像素或字节位置。

重复事实只加注记，不合并记录。历史关联可使用独立定位后的原文范围，但不会用旧记录替换新记录，
也不会将历史记录追加成本轮抽取。入口仍向 `reconcile_results` 传入 `table_input`；省略时只使用原声明的严格关联，不能猜测位置。

例如模型返回 `value: "3"`、`unit: "m"`、引文 `试样厚度3m`，交付仍为 `3` 和 `m`。
若模型返回 `999`、原文为 `307`，交付仍保留 `999`，同时报告字面不一致；本模块不替模型选取新值。
任务未声明的字段保留并标记，任务缺失字段只报告，不补造 null。

## 报告怎样读

每张表显示原表、任务、冻结参考答案以及 A 的一次调用。点击来源编号跳到原格。
原表由格子索引重建并转义，原始全文另行展开，不执行论文或模型返回的 HTML。

| 区域 | 含义 |
| --- | --- |
| 抽取结果（附检查状态） | 默认展示 `candidates`，与合法解析记录一一对应；字段、引用、顺序和类型保持不变 |
| 分项检查 | 分别显示引文定位、数值／单位字面检查、局部位置关系与科学核验状态 |
| 范围与成员依据 | 分开显示模型范围声明、范围引文定位、成员逐项出处核验；累计行已定位不表示成员已核实 |
| 优先检查入口 | 明确问题及具体风险单列；普通“关联未自动确认”另列，不自动调用 Jev |
| 模型原始输出 | 实际返回文本、成功解析部分及被隔离的格式错误记录 |
| 交付保真 | 检查交付是否与解析后的模型字段、引用和顺序一致；不代表模型内容正确 |

每一步都有请求、响应、解析、字段检查、历史关联、错误、预留和状态文件。
`results.json` 是默认结果导出；`parse-rejections.json` 保存被隔离记录及原数组位置。
JSON 链接可直接打开。这些是可观察的输入输出和程序动作，不是模型内部思维过程。
问题按记录和字段归组，同时列出受影响记录数、字段数、诊断数。同一字段的定位失败
可能继续触发来源不明和必填项缺失，三条诊断不等于三个独立科学错误。

`success` 只表示步骤执行完成。HTTP 成功、格式合法、字段正确、来源正确和专家
审核是不同结论。`SUMMARY.json` 分开保存执行证据和质量比较。HTTP 计数只接纳
真实运行中完整模型 POST 响应证据，不把 TLS 探测或模拟传输当成真实调用成功。

“结果已交付”与“科学关系已独立核验”分别表示。跨分组行、跨列块单位和合并格共享
关系可能超出现有规则：相关记录仍交付，字段标为“关联未自动确认”。完整表、表题、
标题及脚注来自实际模型输入，记录通过共享证据包引用它们；保存完整上下文不证明每个
语义标签正确。错格、截短数值或单位、混合条件、总量误分配等诊断仍保留，不因此自动接受。
本轮没有额外模型或专家核验时明确写 `semantic_verification: not_run`。

`relation_check: locally_supported` 保留旧字段值，报告显示为“局部位置规则符合（非科学核验）”。
新增的 `relation_check_basis: local_position_rules` 明确它只核对同行、同列、跨度或上下文类型，
不能据此判断共享单位必然适用、对象归属必然正确。

每条记录的 `scope_check` 分开保存 `declared_scope`、`scope_quote_location` 和
`member_evidence_check`。群体或各成员分别适用的声明（`group` / `each`）目前没有逐成员核验器，
因此成员核验始终为 `not_run`；范围引文即使含有成员名称也不自动升级为已核验。
`individual` 的成员检查为 `not_applicable`，意思是“按模型范围声明不适用”，不是程序验证了该声明。
例如模型抽取“两个单元累计为12 L”，仅引用“累计体积/L”时，数值、单位及累计行可分别定位，
成员状态仍显示“未逐项核验成员及其出处”。旧产物缺少这个状态时显示未记录，不解释为通过。

`relation` 表示模型声明的证据与数值定位点的位置关系，例如同一行、同一列或上下文。
只有 `value: null` 且 `sources: []` 时允许关系省略或为 null；非空字段缺关系仍是协议错误。
这不会替模型补造关系，也不绕过必填字段检查。严格 `parse_route_response` 保留整包验证；
运行入口显式使用逐记录解析。一条坏记录只隔离自身，重复编号涉及的全部记录均隔离。
原数组位置、原记录和错误字段保存下来。JSON损坏、重复键、非法顶层或截断仍整体失败。
存在合法记录与隔离记录时显示 `partial_format_error`（部分格式错误），不能计为完全成功。

“参考值匹配”是事先冻结任务范围内的一对一比较，不能称为整表全部信息召回率。
没有响应显示未评估，空结果算遗漏，重复输出不能重复匹配同一目标。失败保留在计划
目标分母。名称别名须预先写入 `gold.aliases`，不能看完输出后暗改标准。
报告同时显示不使用别名的严格写法匹配，防止名称差异被误读成科学内容错误。

新增的“格式等价匹配”仅对任务 `measurement_groups` 中的数值和单位字段生效：
忽略首尾空白及一层完整成对的单 `$` 包装，保持 JSON 类型，不修改内部字符、指数、范围或倍率，
不换算单位，不支持 `$$` 或破损包装。该指标不修改模型字段、引用或冻结参考，也不等于科学核验。
没有任务定义时不启用这种字段比较。历史严格分数保留，不按新指标追改旧报告。

来源分两项：严格比较原格编号、引文和位置；仅比较单元格编号。完整表头引文和短
引文可能指向同一格，严格字符串不匹配不一定是科学内容错误。仅编号一致也不证明
格内位置和科学关系正确。来源参考存于 `gold.expected_sources`，缺少标注就显示
未评估。只有预先冻结的 `quote_aliases` 才能放宽引文比较。

## 用旧回答验证代码修复

`fork-replay` 校验旧清单、试次和响应 hash，再将已保存 A 回答导入新离线目录。
可以导入 `table-a-0.2`、`table-a-0.1` 和历史 `table-routes-0.1`，但仅导入 A，保留轮数、任务和参考，不执行 B。
旧 A 回答缺失时明确失败，不悄悄代入样例回答。旧目录不修改。
导入目录中的 `replay-provenance/<slot>/` 保存原请求、响应与来源记录，并冻结 hash。
报告将其与本轮生成但未发送的请求草案分开展示。若原回答本身来自回放，来源模式照实保留；
覆盖回答的故障测试也明确标记，不能冒充新的真实调用。

```powershell
.venv\Scripts\python.exe scripts\table_route_experiment.py fork-replay `
  --bundle C:\private\historical-bundle `
  --output C:\private\a-import-01

.venv\Scripts\python.exe scripts\table_route_experiment.py replay `
  --bundle C:\private\a-import-01
```

注入故障时可另存 `overrides.json`，以真实 A 槽位编号为键、替换回答为值，在
`fork-replay` 加 `--overrides C:\private\overrides.json`。覆盖 B 槽位会被拒绝。
导入目录只能离线运行，不能转成真实调用。完整性检查拒绝冻结材料或试次被暗改。

新 A 目录可以用 `summarize --bundle ...` 只读汇总；允许代码版本不同，但仍检查
证据完整性。代码变化后重算应新建 `fork-replay`，不可覆盖旧结果。

## v2 调用方如何迁移

`evaluate_route` 和 `reconcile_results` 的参数签名保持；返回契约升级为 v3，不再返回
`current_results`、`changes`、`all_changes`。调用方读取 `candidates`，将检查、独立定位和历史关联当作附加信息。
`fields`、`anchors`、`scope`、`scope_sources` 保留解析后的模型声明；`values` 是字段值投影。
证据位置在 `resolved_fields`、`resolved_anchors`、`resolved_scope_sources` 中另存，不覆盖原声明。
候选 `status: extracted` 表示已抽取，`check_status` 区分检查完成与发现问题；这些状态不代表专家接受。
旧报告中旧严格结果仍可阅读，但新运行没有等效的自动改写或自动接受开关。

## 真实调用的固定边界

新包接受 1–4 张表，默认一轮、最多 4 次 A 调用；显式 `--rounds 2` 最多 8 次。
轮数写入清单，不自动补位、重试或追加轮次。失败或结果未知的槽位不会自动重发。
真实实验须另建目录，使用已核对的 `--backend-config`，先 `check` 和查看请求，
在明确付费授权范围内运行：

```powershell
.venv\Scripts\python.exe scripts\table_route_experiment.py run `
  --bundle C:\private\table-a-live-bundle `
  --env-file C:\private\.env --live
```

密钥从环境或私有 `.env` 读取，不写入冻结材料。每次最多输出 4096 tokens；
不通过第二次调用修复格式。参考答案、历史回答和裁图不进入请求。

合成回放验证程序规则；历史真实回答回放验证代码修复；新的 `run --live` 才验证
一次新的模型作答。三者分别报告，不能混称真实新实验。

合并单元格仍需判断意义：样品名跨反应前后两行可被两次观测引用；组总厚度跨三行，
不能自动复制为三个成员各自的厚度。当前检查不是通用语义证明，不能保证任意复杂表
均正确。跨页重建、损坏网格、隐藏关系和复杂数学单位仍可能失败，应保留首轮证据，
先定位错误再决定后续优化。论文、参考、真实响应和日志留在私有目录；公开内容仅
含代码、合成样例及不暴露研究材料的验证结论。
