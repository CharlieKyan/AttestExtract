# 简便任务输入

从 dev29 起，普通用户提供任务说明、完整字段清单和可选标注示例即可调用 `extract()`。任务清单只需要“字段名 → 定义”；编译和检查在框架内完成，不调用模型，不需要单独确认或保存内部 TaskSpec。

此入口使用通用的观测模板，保存对象、原值、单位、条件和证据。需要自定义关系、事件或其他结构时，继续使用原有 TaskSpec。简便入口不自动理解任务说明并发现漏写的字段，也不自动生成完整数据库表。

## Python：一次调用

下面是使用合成原文的完整调用。先在环境中提供 API 地址、模型名称和密钥；密钥也可以通过已有的安全凭据来源传入。示例可省略，不必覆盖清单内的每个目标。

```python
import os
from attestextract import extract

result = extract(
    text="Sample A has mass 17 g at 25 C and length 4 cm.",
    prompt_description="Extract reported sample properties. Preserve objects, values, units and conditions.",
    field_list={
        "mass": "Reported sample mass, including original ranges and stated means.",
        "length": "Reported sample length, retaining its direction or conditions.",
    },
    examples=[{
        "text": "Sample B has mass 2 g at 20 C.",
        "extractions": [{
            "field": "mass", "entity": "Sample B", "value": "2", "unit": "g",
            "conditions": "20 C", "quote": "Sample B has mass 2 g at 20 C.",
        }],
    }],
    model=os.environ["ATTESTEXTRACT_MODEL"],
    base_url=os.environ["ATTESTEXTRACT_BASE_URL"],
    api_key=os.environ["ATTESTEXTRACT_API_KEY"],
)
for candidate in result.candidates:
    print(candidate.record_type, {name: field.value for name, field in candidate.fields.items()})
for table in result.table_results:
    print(table.table_id, table.candidates)
```

运行这段代码会调用所选服务。程序返回候选，不会自动将它们标为专家确认；Python 返回对象，CLI 默认另外保存报告和运行诊断。

已有模型预设、后端注入、配置文件继续有效。任务材料独立于模型连接；原有 `extract(text, task, ...)` 不变。不能同时提供 `task` 和新任务关键词。

## CLI：一个任务文件

将下面内容存为 `task.json`。`examples` 完全可省略。

```json
{
  "prompt_description": "Extract reported sample properties. Keep objects and conditions separate.",
  "field_list": {
    "mass": "Reported sample mass, including ranges and stated means.",
    "length": "Reported sample length and measurement direction."
  },
  "examples": [
    {
      "text": "Sample B has mass 2 g at 20 C.",
      "extractions": [
        {"field": "mass", "entity": "Sample B", "value": "2", "unit": "g", "conditions": "20 C", "quote": "Sample B has mass 2 g at 20 C."}
      ]
    }
  ]
}
```

准备原文 `paper.md` 和 DeepSeek 凭据后，直接运行：

```powershell
attestextract extract --input paper.md --task task.json --model-id deepseek:deepseek-flash
```

DeepSeek 预设读取 `DEEPSEEK_API_KEY`；显式使用已有 `.env` 时增加 `--env-file .env`。Qwen 可改为 `--model-id qwen:qwen3.8-27b` 并提供对应凭据。自定义兼容服务使用 `--base-url` 和原始 `--model`，密钥通过 `ATTESTEXTRACT_API_KEY` 或终端隐藏输入提供；无需设置 TLS、Schema 或预先创建 profile。连接与有限兜底边界见[最少模型输入](MINIMAL_API.md)。

原有 `--task` TaskSpec 文件仍可读取。文件不能混合 `record_types` 与简便输入键。

终端会显示目标数与示例数；默认输出到原文旁的 `outputs/<run>/`，原文位于 `inputs/` 时使用同级 `outputs/`。打开 `report.md` 查看每个目标、候选原值、条件、原文位置和复核项。完整值、表格及诊断保存在 `result.json` 和 `diagnostics/`。

需要提前了解分块和预算时，可选择运行同一命令加 `--check`；它不联网、不是必经步骤，也不验证服务可用性或科学抽取质量。

## 示例和缺失信息

- `field` 必须是完整清单中的一个名字；`value` 为非空字符串。数值也写成字符串，保留范围、OCR和定性措辞。
- `entity`、`unit`、`conditions` 可省略或为 `null`；它们仍是模型需要根据原文保存的内容。可选不等于忽略。
- `quote` 是连续原文片段，足以支持该观测。不同字段的证据在不同句子时，可用 `quotes` 按 `entity/value/unit/conditions` 覆盖对应引文，不能拼接证据。
- 存在多处匹配、重复示例或只能通过保守空格/标点规范化定位时给 warning，原输入仍保留。找不到引文、类型错误、未知目标或重复 JSON 键会在模型准备前报错，并给出输入路径。
- 示例校验检查结构和引文位置，不能证明标注含义正确。用户仍须核对自己的例子。
- 没报告某个目标时，不生成空记录充数；模型返回空 `value` 时保留原记录并提示缺失。报告的“有值候选”仅表示值非空，不代表证据、归属或科学事实通过核验。
- 对“10%～20%，平均15%”，保存范围字符串与单独的原文平均值；不计算缺失平均值，不支持在这个入口将 `value` 写为 `{min, max, avg}`。

完整清单中的目标始终进入任务约束，即使示例未覆盖它。零条候选不代表原文确实没有该信息，应结合原文抽查漏抽。

## 表格、快照与恢复

正文与表格共享说明和示例，但输出协议不同。表格将用户例子视作语义指导，继续保存实际表格的单元格引用、锚点和作用范围；框架不会把纯文本例子伪装成表格。未执行的原值或语义检查不会写成通过，图像内容识别不在此入口范围内。

原始任务说明、清单、示例和编译修订号保存到结果任务及 `diagnostics/task.json`，并参与检查点身份。修改这些材料需要新运行，不能复用旧响应。只修改允许的超时等连接设置，仍可按原恢复规则复用已完成块。失败后的命令由终端给出，调用预算保持累计。

旧 TaskSpec 的序列化与正文提示词保持兼容；本轮修改了表格必填空白值检查，表格路由修订为 `dev29-table-a-2`。旧结果可读，但包含旧路由表格的检查点不能直接用于新版本恢复。含表格的结果仍不能送入当前正文专用的 `prepare-decisions` 或接受档案，避免漏掉表格。

公开样例均为合成数据。当前交付通过离线编译、回放、模拟 HTTP 和安装检查；真实服务效果与科研任务质量应另行验收，不由这些检查推导准确率。
