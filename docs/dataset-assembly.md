# 从冻结观测生成科研数据表

## 字段类型怎样跨论文复用

任务文件可以继续只写字段说明，也可以通过 `field_types` 明确部分或全部字段的含义。例如下面的任务允许材料牌号为 `316`，浓度可用单值、范围或单侧界限表述，“性能评价”交给模型解释。

```json
{
  "prompt_description": "记录各批次的材料牌号、浓度和性能评价。",
  "field_list": {"grade": "材料牌号", "concentration": "样品浓度", "performance": "报告的性能评价"},
  "field_types": {"grade": {"kind": "text"}, "concentration": {"kind": "numeric"}}
}
```

类型可以是 `text`、`numeric`、`mixed` 或 `unknown`。数值型与混合型默认接受 `scalar`、`range`、`bound`。例如只收精确点值的列可以写 `{"kind":"numeric","shapes":["scalar"]}`。均值、最大值、实测值、公式设定值的用途和统计口径单独记录。

普通在线字段任务会在正文抽取前，把任务说明和全部尚未声明的字段定义一起送给模型。用户声明标为 `user`，模型建议标为 `model`。准备请求只包含任务信息。固定服务通常消耗一次准备调用；自动选择接口时，如果服务明确拒绝一种输出格式，可以换兼容格式继续。每次尝试都计入 `--max-model-calls`，成功后在正文和续跑中复用准备结果。低层自行传入 `backend=` 默认维持原有调用次序，需要准备时传 `prepare_field_types=True`。

批量研究可先用下面的命令生成共享任务。

```powershell
python -m niceextract prepare-task --task task.json --model-id deepseek:deepseek-flash --output-dir prepared
```

随后给每篇 `extract` 都传 `--task prepared/task.json`。模型只解释这份任务一次，各篇使用相同的类型建议。全部字段已声明时，准备命令无需提供模型；四篇单独执行抽取命令、每次仍传原始未声明任务，则每篇都会单独准备。准备目录保存最终字段、来源及调用记录。用 `--check` 可离线查看本篇还需准备几次。续跑继承原检查点配套的任务；已有历史任务沿用原契约。

## 首轮怎样读、失败后怎样接着跑

普通的字段任务可以直接给出“轴承 B7 的温度为 60 °C”。任务没有规定行主体时，B7 就是这一行的主体。若用户按电机查部件温度，模型要说明 B7 与哪台电机有关，以及这个字段能否收录轴承读数。关系说明、作者原句和原文位置分别保存。原文只写范围、模型把端点解释为上下界时，可在主表的依据列与引文状态列分别查看这两个判断。

新任务默认给模型每侧至多 500 字符的相邻段落。表格可引用介绍段、表头、单位及脚注。使用 `--neighbor-chars 300` 可调整长度，`--context none` 可关闭邻段。正文或表格窗口只负责自己的读数。程序先检查引文。有多个可能出处、归属会影响查找或字段范围仍有冲突时，才使用查证预算。

对于旧版正文因关系格式被拒两次的运行，使用 `extract --recover-format-from 旧运行目录`，配上原来的输入、任务、模型和一个新的输出目录。程序核对两次请求实际见过的材料、两份原始回答和旧账本。

使用单个模型运行 source-v2 字段任务时，若同段正文的首次回答和格式重生成都正常结束、都出现 JSON 语法错误，程序会自动把该段拆短，按原文顺序接着处理。此前在这一步停止的运行，用 `--resume 旧运行目录` 和新输出目录即可继续。检查点保留两份原答的指纹、拆分范围和既有调用；拆段与后续请求仍受累计调用预算、最小块长和 `--max-split-depth` 限制。传输超时、结构正确但字段不合规、表格及语法错误适用各自的处理规则。

## 查询一个对象及它的下属对象

新任务会分别保留直接测量的对象和主表中供读者查找的对象。论文若写“电机 M4 的轴承 B7 温度为 60 °C”，按电机查询能看到 B7 的测量。结果标着“下属”，直接对象仍是 B7。如果字段要求整台电机的平均温度，模型明确判定轴承读数不适用，这一格就留空，明细中保留原值和理由。

```powershell
niceextract query-dataset --dataset dataset --subject "电机 M4"
niceextract query-dataset --dataset dataset --subject "电机 M4" --self-only
```

第一条命令显示本级读数、有原文支持的下属对象读数、直接关联和待定意见。第二条只看直接测到电机本身的读数。加上 `--output 电机读数.csv` 即可另存。Python 对应 `query_dataset(data, "电机 M4")` 和 `query_dataset(data, "电机 M4", self_only=True)`。同一读数只有一个观测编号。多级查询沿带证据的包含关系逐层查找，结果的 `query_path` 保留沿途理由和出处。

## dev57 阅读主表

要把筛选结果交给同事，可用 Python 从已保存的 dataset.json 导出一份 Excel 能打开的 CSV。下面只导出模型推断、且引文尚未核实的观测。每条观测占一行，保留编号、直接对象、归档对象、原值、单位和用途。需要只看实测时，可再加 `measurement_use="measured"`。输出使用新文件名，文件已存在会提示另选名称。

```python
import json
from pathlib import Path
from niceextract import export_observations

data = json.loads(Path("dataset/dataset.json").read_bytes())
export_observations(data, "待核推断.csv", statement_basis="inferred", citation_status="unverified")
```

普通字段任务会在第一次阅读时记录原文明示或模型推断、直接对象和用途。指定一行代表什么对象后，正文和表格都可以报告所属关系。原值保留在原字段列，旁边的“_依据”和“_引文状态”列可在 Excel 中筛选。来源记录缺少依据类型时显示“尚未判明”。“已定位”说明引文可回到原文，定性改写和字段解释仍是模型的判断。范围、均值和单位可以分别追溯到各自观测。

例如样品的含水率可以归入采样站的行。按采样站或样品查询，结果指向同一观测。两站均有同名样品时，独立的观测编号及范围继续保留。原值可查但目标仍有争议时，记录状态展示分歧。引文有误时会尝试在原文重新定位，多个可能位置保存在明细；引文未核实的提议也能在主表中查看。

在 Python 中，`query_dataset(data, measurement_use="measured", citation_status="located")` 可选出已定位的实测值。`statement_basis="reported"` 和 `"inferred"` 分别选择明示和推断，`"unknown"` 选择依据缺失。`subject_match="contains"` 可以按标签中的一部分文字查找。`include_pending=False` 可选取原交付分类为确认的观测。每次查询按观测编号返回，明细中的引文位置使用原文 Unicode 字符偏移。

历史完整运行目录使用 `assemble-dataset` 写到新目录时可回放已保存的关系提议。回放利用同次请求展示的段落、原始回答和校验和。缺少该次请求时保留原待核意见。需要补读时可用 `--reading-review review.json` 传入独立结果。文件记录原文 SHA-256、补读者以及逐条判断；每项指定原观测编号、字段、直接对象、原值、单位、原文区间与原句、填值依据和理由。新增观测省略原观测编号。可用 JSON 数组按顺序传入多份补读，主表保留来源并分开记录意见。历史冻结数据的内存接口可用 `assemble_dataset(..., reader_view=True)` 显式重组；读取已有数据文件不会改写旧结果。

一项观测包含原值、直接对象、条件和证据位置。例如原文报告“样品 A 的质量为 3.70 g”，正文或表头还可能指明它取自设备 R。要检索 R 的相关观测，可以沿有证据的来源关系找到 A。设备质量一列只能接收直接描述设备的质量值。跨论文统一身份和含多个角色的对照任务超出本版范围。

## 抽取时分配属性和名称

dev50 在普通 `extract --table-subject` 的第一次阅读中保留每条观测的原始主体。
明确的本行观测使用 `as_reported`，通过原抽取检查后可直接交付。篇内名称需要比较、
观测属于关联对象、或字段限定仍有争议时，复用已有查证预算。用户继续提供原来的
任务说明、字段列表和一行代表的对象。一个实体缺少名册 ID 也可以交付数据。

例如报告先介绍东厂的两条线，随后用短名分别报告产量。程序可保存这次局部指代，
或在有原文依据的章节、表格区域内复用短名。源观测与名称判断分别保存。遇到另一个
可能的对象或更换实验场景时，明细保留原称、数值及争议依据。归属分数或字面相似性
不能替代出处。历史 dev49 完整结果可离线重建；进行中的 dev49 查证用原版本续跑。

若一次抽取已完成并保存了首轮结果，后续查证中断，可通过 `assemble-dataset --raw-only`
和 `--result` 指定该结果。原任务中已明确归属且字段适配的观测仍可组表。尚需查证的简称、
冲突字段和未确认的范围可在明细查阅。改变主表主体定义时，旧任务的入表判断只适用于原定义。

## 历史运行

### 直接对象与关联归档

例如用户按设备建表，原文写“二号炉的内衬温度为 80 °C”。直接对象保留“内衬”，
有据的目标主体为“二号炉”。任务允许收录部件温度时，主表可按二号炉归档，并显示直接对象。
若任务只收整炉平均温度，这条观测及其原句仍在明细中。字段适用范围待定的行在
`main.csv` 的“记录状态”列标出，默认确认统计排除这些行。

`observations.csv` 保留 `entity` 原值。`direct_subject_status` 区分已连回来源的对象
（`source_linked`）、模型给出但仍待核的名称（`model_proposed`）和没有直接对象
（`unreported`）。`value_located` 描述数值位置。`direct`、`target`、`fit` 各自带判断依据。
原有警告见 `original_checks`。同一条观测从两个名称检索时共用 `observation_id`。

```python
from niceextract import query_dataset

direct_readings = query_dataset(dataset, "内衬", exploratory=True)
associated_readings = query_dataset(dataset, "二号炉", association_only=True)
confirmed = query_dataset(dataset)
```

在新的普通 `extract --table-subject` 命令中加入 `--row-relation-evidence`，
可试验在原抽取请求中一并报告直接对象与目标的有向关系、原文依据和字段适用范围。
Python 对应参数为 `row_relation_evidence=True`。明确的备选目标可以共用一条待定记录。
这种语义判断来自模型。程序核对引文位置，以及相应正文提及或表格区域是否适用于这条观测。
缺少局部连接的跨节关系可沿用现有、有出处的查证结果。历史回答在明细中标为
`row_relation_status=not_asked`；试验回答返回空关系时为 `not_proposed`。
`located` 表示关系引文已定位，最终归档结果请查看 `target`、`fit` 和 `delivery`。

对照两种提示时，分别使用新的输出目录，固定任务、输入、模型、参数和预算。
默认命令使用原提示，实验开关仅适用于观测优先的简便任务。旧运行用下面的命令重新组表。

### 离线重建

保留输入时的 UTF-8 原文和换行。将下列路径替换成自己的文件。命令只读取本地文件，新建一个输出目录。

```sh
niceextract assemble-dataset --input source.md --run-dir run --output-dir rebuilt --dataset-config dataset-options.json
```

默认读取 `run/result.json` 和 `run/analysis/analysis.json`。自定义路径可用 `--result` 和 `--analysis` 指定。文件缺失、文档校验失败或配置无效会返回错误。重建时选另一个输出目录，以便保留可比较的旧版本。

dev48 更新了简单任务的抽取提示与表格补读策略。早期版本尚未完成的简单任务可用原安装版本续跑，也可用 dev48 在新目录重新抽取。已完成的观测优先运行可直接用上述离线命令重新组表。

`--dataset-config` 可省略。框架从冻结任务读取完整字段列表和定义，从已有分析读取主表行的主体描述。以下为一个合成任务的选项文件。

```json
{
  "row_subject": "individual specimens",
  "identity_fields": ["specimen"],
  "subject_label_field": "specimen",
  "field_policies": {
    "mass": {"allowed_units": ["g"]},
    "material_description": {"allowed_value_forms": ["description"]}
  }
}
```

材料名称列可以保留“红色 4 号颜料”和“含 12% 染料的涂层”。只写“12.5”、单位为“%”的测量会随所测组分、对象及引文留在明细。所需字段要报告含量时，在任务中明确要求保留成分和含量，并让该字段允许数量。可选的 `allowed_value_forms` 支持描述或带数字的名称 `description`、无单位的数字或范围 `number`、带单位的数字或范围 `quantity`。允许多个形式时可在列表中并列填写；省略此项时不会按形式筛除观测。数字编码本身可作类别的任务也可允许 `number`。

`identity_fields` 中的字段必须已存在于任务。只有显式指定的 `subject_label_field` 可以接收有出处的目标名。序列号、站点编码等身份字段需要各自有证据的原值。省略 `identity_fields` 时，`subject_label_field` 可单独作为身份字段。配置多个身份维度时须包含显示名字段（如有）。各列的证据和冲突分别判断。完整明细始终可用于审阅。新指定的行主体会重新约束已有关联。

可用字段约束包括 `allowed_units`、`allowed_purposes`、`allowed_statistics`、`required_conditions`。每项都是允许的字符串列表，按原值精确匹配。可从已有观测明细查看可用值。不同条件下的测量在组表时分别保留。

## 新抽取

对使用 `prompt_description` 和 `field_list` 的任务，有两种入口。

- 传入 `table_subject` 时，原有文档归属分析按单独的 `max_analysis_calls` 预算执行。随后默认生成数据表。Python 可额外传入 `dataset_options`，CLI 对应 `--dataset-config`。
- 只希望基于已有候选离线组表时，在 `extract(..., dataset_options={"row_subject": "individual specimens", "subject_label_field": "specimen"})` 中提供配置。此方式仅使用原抽取的模型预算。Python 从 `result.dataset` 读取数据，使用 `output_dir` 的运行同时写入 `dataset/`。

对没有保存归属分析的原始运行，可显式传入 CLI 的 `--raw-only`，或调用 `assemble_dataset(source, result_dict, analysis=None, **options)`。常规回放默认要求归属分析文件，以便识别错误路径或意外缺失的产物。

## 确认值与待定值

主表末列「记录状态」支持筛选。「已确认」表示局部归属、目标身份和字段适配均有依据。有来源的待定观测也可进表。例如原文明确某一读数属于甲、乙设备中的一台，就只显示一个候选行；同名设备的厂区仍有歧义时，在状态列列明范围待定。原文只给出毛料的长度，任务明确排除毛料长度时，仅保留观测明细。若原文给出长度、但未说明是否属于任务所指的可用长度，可按字段口径待定展示。状态区分原文直接报告的属性和用户请求的属性。未找到任何有依据的候选目标时，在明细中保留原值和引文。

原文名称与规范名可以指向同一篇内主体。明确的定义或分组以及同范围的局部属性陈述支持名称对应；样品属于设备、部件属于整体等关系各有方向。名称对应和属性记录可以分别查证。

普通抽取在首轮完成后检查可识别的表头及原始值格。有明确目标表头、其下有数值、相应格没有返回该字段时，可在同一抽取预算内定向补读一次。保留共享表头与已有值格的出处。图像、无法定位的网格或缺乏可识别任务字段的表头仍可能漏抽。离线 `assemble-dataset` 只使用已存输入与结果。

## 读取结果

| 文件 | 读取方式 |
| --- | --- |
| `main.csv` | 用户定义的列按原顺序输出，末尾附「直接对象」「观测口径」「记录状态」。如有重名，实际列名见 `dataset.json` 的 `context_columns` 和 `status_column`。使用身份字段可直接读取行主体，或按行序对照 `lineage.json`。 |
| `observations.csv` | 每条观测保留原始 `entity`、直接与目标主体名、原值、单位、条件、用途和候选指针。复合证据及各判断以 JSON 保存在单元格内。 |
| `lineage.json` | 查看每个主表行和每个值关联的观测 ID，再沿明细内的候选指针和 Unicode 字符偏移回读原文。 |
| `dataset.json` | 完整的可重用 JSON，含两张表、证据、独立的归属和字段适配状态、覆盖缺口与预算。可用它区分空值、字符串和数字。 |

省略主体显示名配置时，CSV 在「记录状态」中附上主体名。CSV 第一条数据行的主体在 `lineage.json` 的 `rows[0].subject_label`，第二条对应 `rows[1]`，依此类推。如需用单独的列筛选主体，请在 schema 中声明主体显示名列，并用 `subject_label_field` 指向它。

严格查询、关联检索和探索查询共用观测 ID。严格查询只返回已确认的入表观测。关联查询要求指定主体存在有依据的连接，可以返回部件、样品或组合对象的观测。探索查询还返回具有候选名称的待定记录。

```python
from pathlib import Path
from niceextract import assemble_from_files, query_dataset

dataset = assemble_from_files(Path("source.md"), Path("run"), subject_label_field="specimen")
measures = query_dataset(dataset, "A", field="mass")
related = query_dataset(dataset, "R", association_only=True)
leads = query_dataset(dataset, "R", exploratory=True)
```

## 可选的语义补查

### 本地 Jev 复核

dev49 提供独立的封闭选择复核入口。先用普通用户命令抽取并保留 dev48 的数据集。在支持权重自带 `inference.py` 的 Python、PyTorch 与 Transformers 环境中安装 dev49，并显式指定可信的本地 Intern-Decision-4B 目录。每个问题使用研究任务、原观测、原文和候选项；模型可选择一个主体、保留待核或排除。主表中的确认行作为基线保留。完整性按源观测 ID 核对，涵盖原有待定行和仅存明细的观测。漏抽线索和没有形成观测的隔离响应仍在原始文件中。

选中主体时，再让同一模型单独阅读字段定义、候选原值、单位和原文，判断原值能否回答该字段；第二次问题不显示第一次选择。只有两项均支持才送入组表，实际入表还要核对同源重复、身份与条件。两次选择及分数可分别查看。原本仅缺字段限定的待定行继续显示原值和待定原因，拒绝将其确认为目标字段会另加说明。

```sh
niceextract review-dataset --input source.md --dataset run/dataset --checkpoint /path/to/Intern-Decision-4B --output-dir final
```

打开 `final/main.csv` 查看最终表，在 `final/observations.csv` 或 `final/dataset.json` 中按观测 ID 对照原文、模型选择和交付状态。完整答复、候选集和分数保存在 `dataset.json` 的 `jev_review.items` 中。分数表示模型在本次候选集中的偏好；科研准确率需要与原文、标注独立核对。运行默认使用 CUDA，可传 `--device cpu`。开发所用模型的权重、推理脚本和依赖信息可查阅[模型发布页](https://huggingface.co/internlm/Intern-Decision-4B)。

中断后重复命令，默认从输出目录旁的同名 `.sqlite` 文件复用已完成的选择。发生逐项验证错误时，程序将可用结果保存到独立的 `.partial-*` 目录，返回状态码 2。要对照两个完整结果，另选输出目录并通过 `--ledger` 指向同一账本。需要回退时直接打开先前的 `run/dataset/main.csv`；代码基线保留在固定标签 `v0.1.0.dev48`。

### 语言模型语义补查

先用零预算查看尚待处理的工作。此命令可在无凭据环境中运行，结果以 JSON 写到标准输出。每篇文档选一个单独的 SQLite 文件。

```sh
niceextract supplement-dataset --input source.md --run-dir run --dataset-config dataset-options.json --ledger review/work.sqlite --max-calls 0
```

启用模型判别时，设置 `--model-id` 和正整数 `--max-calls`，并配置该预设的凭据。重复使用同一账本，已派发的尝试继续占用累计额度，输入片段会发往所选服务。`niceextract models` 可列出预设。Python 也可用 `niceextract.dataset_supplement.run_supplement(dataset, source_text, ledger_path=..., max_calls=..., backend=...)` 显式传入单次尝试的后端。

返回值分别给出模型判别、失败、待核项、尝试数和后端报告的 token 用量。有失败且尚待处理的补查任务时，CLI 输出报告并返回状态码 2。模型判别带有证据和来源标记，独立保存在补查报告中。原文中缺少匹配候选的数字只构成查漏线索。例如页码、编号也可能进入队列。漏抽率和科学准确率需要与人工标注对照计算。

对于跨句或多处引证的肯定判断，模型须在 `applicability` 中说明适用对象与条件，并引用含有该项观测的局部原文，程序验证其坐标。只有单条局部引文时，可以直接以这处引文标出范围，`scope_basis` 会注明采用了这一方式。新发现的观测也要满足任务对单位、条件、统计量和用途的显式约束。
## dev52 定量主表

普通 `extract` 和 `assemble-dataset` 会在 `main.csv` 中为实际交付的定量字段追加 `_min`、`_max`、`_avg`、`_unit`。原有任务列继续保存读数或带口径的组合文本；直接对象、观测口径和记录状态列保留在表尾。用户已使用的字段名优先，发生重名时查看 `dataset.json` 中的 `statistic_columns` 对照。

范围只提供两个端点。原文另外报告、并能归到同一对象、指标、样本、情景和单位的均值可以放进同一行。单值留在原字段。单侧界限、置信区间和条件限定保留原口径。每个展开单元格都在 `lineage.json` 的相应行 `statistics` 中记录观测编号。多处出处支持同一模型设定时，可从 `corroborating_observations` 和 `corroborates` 回看另一条证据。

通过 `query_dataset(dataset, subject, field=..., statistic_kind="avg")` 可按原文报告的均值筛确认观测。已确认并通过字段适用检查的关联观测可以按直接对象或目标行查询，二者返回同一观测编号。待定项和有证据的相关项可用 `association_only=True` 按主体查阅。
### 按名称或代号查数，区分限值和用途（dev53）

当对象名与代号在同一篇内有经查证的对应关系，用其中任何一个查询，都可以找到同一批观测。两个厂都用同一代号时，仍须结合厂区区分。正文逐项给出同一试样的长度和宽度时，两项可以并排放在一行。每个单元格在 `lineage.json` 中保留自己的观测 ID 与出处。

如果任务接受工艺设定，正文规定 A1 和 A2 共同使用方案 P，且引用的参数表给出了 P 的温度，那么这项设定在主表占一行。按任一成员查询会返回同一观测 ID。成员信息与依据可在 `target.members` 检查。

例如“一般不低于 10 °C”在主表可按字段分别导出 `_lower_bound=10`、`_bound_operator=>=` 和 `_qualifier=一般`。上限使用 `_upper_bound`。实测范围和实际报告的极值仍使用 `_min`、`_max`。每个有依据的定量用途可按 `_use` 筛选。

在 Python 中使用 `query_dataset(data, field="temperature", measurement_use="model_input")` 可取已确认的模型设定。要筛选有实测依据的单值，同时指定 `measurement_use="measured"` 和 `statistic_kind="value"`。默认查询只返回确认交付的记录；需要查看保存在明细中的分类界限时，加 `exploratory=True` 和 `measurement_use="classification_threshold"`。
