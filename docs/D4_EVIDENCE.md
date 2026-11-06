# D4：结构上下文、原文定位与验收边界

本页说明 dev19 的 D4 接口与证据约束。程序接收的仍是未经改写的 UTF-8
TXT/Markdown；MinerU 解析在框架外完成。结构索引、检索视图与模型输入均保留到
这份原文的 Unicode 字符位置，不能把这些位置解释为 PDF 字节位置或 PDF 单元格框。

## 上下文策略与 CLI

`extract` 增加以下参数：

| 参数 | 行为 |
| --- | --- |
| `--context none` | 默认值。不增加章节或邻段上下文；未提供结构索引时沿用原有分块范围。 |
| `--context structure` | 保留当前章节的标题层级，以及明确属于当前表格的表头、行标题、caption 和 footnote。 |
| `--context neighbors` | 包含 `structure`，另携带同章节内前后邻段的完整文本或完整句子；不跨越中间表格搜索正文邻段。 |
| `--neighbor-chars 500` | 每侧邻段的字符上限，默认 500，范围 0–5000；0 关闭邻段添加。仅影响 `neighbors`。 |
| `--mineru-content-list FILE` | 读取 MinerU `content_list` 对象数组，只有唯一精确对齐到输入的元数据才接入。 |
| `--structure-hints FILE` | 用显式 JSON 指定表头行及行标题列，不让程序猜测全 `td` 表格的表头。 |

例如，先对已配置的后端做本地检查，不发送模型请求：

```console
attestextract extract --input synthetic.md --task task.json --config backends.toml --profile review --context structure --mineru-content-list synthetic_content_list.json --structure-hints hints.json --check
```

使用 `--context neighbors --neighbor-chars 500` 可检查邻段方案。移除 `--check`
才会按后端配置执行抽取；日志和报告可能包含原文，应保存在私有运行目录。

默认仍为 `none`，因为增加可见原文既可能补足条件，也可能引入重复数值和其他对象的
信息。离线定位通过不能证明真实模型抽取质量改善；完成固定材料、固定任务与模型配置
的对照验收后，才能决定是否调整默认值。显式提供 MinerU 或 hints 会建立结构索引，
因此即使选 `none`，结构化表格的分块也可能与旧版不同；`none` 表示不增加辅助上下文，
并不表示所有新增证据功能均关闭。

## 原文、结构与主体归属

多层表头中的单位若互相冲突，同列关系检查仍不能判断哪个单位语义上适用；程序不自动
解决该冲突。原文位置与网格关系可检查，科学含义仍需人工复核。

`Document.structure` 是可选的 `DocumentStructure`，包含原文哈希、标题、表格和诊断。
加入索引不改变 `Document.text` 或文档身份。`build_structure()` 和
`validate_structure()` 位于 `attestextract.structure`；索引加载时检查哈希、范围及
表格网格与原始语法是否一致。

- Markdown 表格支持转义管道 `\|`、可选首尾管道与空单元格，并检查各行列数。
- HTML 表保留原始标签、实体和内部内容，记录 `rowspan`、`colspan`。不生成新的
  Markdown，也不把 `&nbsp;`、LaTeX 或 OCR 文本转换成另一种“证据原文”。
- `Table` 保存全表范围、整行范围、单元格内容范围、表头行、行标题列及 caption/
  footnote 引用；行列索引为零基。单元格 ID 绑定本次文档结构中的表、行、列。
- HTML 的 `thead`、明确的 `th` 及显式 hints 提供表头信息。全 `td` 且未给 hints 的
  表不猜表头，诊断为 `header_rows_unspecified`。结构损坏的表保留整体范围，但不提供
  可信网格供单位绑定。
- MinerU 的 `table_body` 必须在输入中唯一精确出现并对齐整个表格。caption、footnote
  和 `text_level` 标题同样要求可验证的唯一原文匹配；多个位置或未找到时记录诊断。
  `page_idx`、表级 `bbox` 仅保存输入已有值，不推算缺失坐标或单元格 PDF 框。

hints 示例，必须按实际原文确认行列：

```json
{
  "tables": {
    "table-0001": {
      "header_rows": [0, 1],
      "row_header_columns": [0]
    }
  }
}
```

当前 MinerU 入口支持 `content_list` 对象数组，不自动猜测其他版本的嵌套 JSON
结构。`structure.source_sha256` 是传入 MinerU 数据的规范 JSON 哈希，不是 PDF 哈希，
也不是原始 JSON 文件字节哈希。

每个块分别记录 `body_segments` 和带角色的 `context_segments`。模型提示明确哪些
片段是抽取主体、哪些仅用于确认对象、条件或单位。辅助上下文不会自动成为新的抽取目标；
只有上下文中出现的候选保留 `context_only_candidate` 待复核提示。当同一候选随后在
实际主体中出现时，可合并来源并解除该提示，其他证据问题仍保留。

标题、不可拆分的公式和表格 rowspan 行组采用软长度上限；超限会报告，不通过截断原文
凑字符预算。递归切分要求子块主体严格缩小并保持覆盖。`coverage.json` 分别报告主体
覆盖、重复主体字符、附加上下文字符及超限块；字符覆盖完整仍不证明对象和条件关联正确。

## 结果 schema 0.4 与定位状态

结果 schema `0.4` 为证据增加规范化匹配与表格引用。模型仍提交引文，框架保存原文位置，
不要求模型编造行列坐标。主要状态为：

| 状态 | 含义与处理 |
| --- | --- |
| `matched` | 引文在允许的来源范围内唯一精确出现，`text[start:end] == quote`。 |
| `normalized` | 精确检索失败后，通过受限检索视图唯一找回原文；仍须复核。 |
| `ambiguous` | 存在多个可用位置，保留位置清单，不猜选其中一个。 |
| `unmatched` | 未找到可用来源；保留模型引文与待复核状态。 |
| `missing` | 模型未提供非空引文。 |

规范化仅折叠空白及映射明确列出的中英文标点，检索结果保存到 `source_matches`：
`source_quote` 是原始切片，`span` 是原文位置，`rules` 记录采用的规则。
此时模型的 `quote` 可能不等于原文切片，应使用 `source_quote` 复核。
重复位置会保留歧义，规范化结果不会直接成为接受记录。

检索不支持 OCR 纠错、拼写纠错、数值改写、单位换算、大小写或任意 Unicode 等价变换。
识别出的 LaTeX 区域保留原字符和空白；整条公式必须按原文引用，不支持公式等价匹配，
例如改写指数、上下标、命令、空格或连接符来让另一种公式写法通过定位。

`table_refs` 保存表格和单元格 ID、行列、跨度与角色。表头单位关联使用相同结构网格，
检查同表、明确表头与覆盖的列；来源匹配和列关系成立仍不是对象、条件或科学结论已经
核验。规范化定位、未明结构和关联不确定项均不能用“位置已找到”替代人工判断。

## 兼容与验收

旧 schema `0.1`、`0.2`、`0.3` 档案继续按原版本规则和身份语义读取；缺少
`Document.structure` 的旧文档仍可加载。新结果为 `0.4`，新增证据字段可能改变新跑结果
的候选身份，不应只靠新旧候选 ID 相等判断业务回归。

改变包版本、上下文策略或结构索引会改变执行指纹。旧运行不迁移为新策略检查点；需要
新建运行，以免复用与当前模型请求范围不一致的回答。

验收分开记录：合成与离线回放验证原文映射、网格、覆盖、恢复及归档约束；真实材料的
人工核查验证对象、条件和单位是否足够；真实模型对照比较正确抽取、漏抽、错归属、重复、
待复核与调用成本。付费真实调用需要独立授权，不由发布授权推定。公开测试及文档只使用
合成素材，真实论文、专家标签和原始模型输出留在私有目录。
