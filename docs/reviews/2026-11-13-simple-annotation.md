# 审查摘要：普通用户的结果与证据集中展示

- 日期：2026-11-13。
- 比较基准：`d0f70cbd77663016f4007dfde2aecebd034dc928`，任务开始时 HEAD；工作区干净。
- 目标：固定标签 `v0.1.0.dev32`，本摘要与代码同提交。
- 范围：新增通用展示模块、保存集成、合成测试和文档；私有论文、旧 annotation、真实结果、助手审计附件与桌面用户脚本不进入公开提交。
- 审查方式：实现者自查；`review_annotation` 独立只读审阅并执行相关测试与额外 JSON Pointer 转义验证，未编辑被审阅代码。
- 结论：当前未发现阻断，属于展示改进，不声称字段准确率已验证。
- 版本：0.1.0.dev32；本地测试、安装与远端发布分别记录，推送和 CI 状态在交付时报告。

## 1. 文件职责与原因

| 文件 | 修改目的 |
| --- | --- |
| `src/attestextract/annotation_view.py`（新增） | 把普通用户关心的原值、单位、条件、出处和完整引文放在一起；技术追踪独立保存。复用现有 review 构建，不实现另一套抽取或定位。 |
| `src/attestextract/expert_review.py` | 既有托管输出与离线导出统一附加 annotation 三文件，不影响原 result/review。 |
| `src/attestextract/cli.py` | 离线导出完成后提示简洁阅读入口。 |
| `tests/test_annotation_view.py`（新增） | 对象、条件、多值、表格出处、类型异常、警告、证据、脱敏、指针及非简便任务兼容。 |
| `tests/test_expert_review_cli.py`、`test_cli_output_dir.py` | 离线 CLI 与默认输出目录实际包含新文件，旧结果不变。 |
| `README.md`、`README.zh-CN.md`、`CHANGELOG.md`、`docs/EXPERT_REVIEW.md` | 说明阅读入口、显示分组与事实确认的区别。 |
| `docs/reviews/README.md`、本文件 | 本轮审查记录。 |
| `pyproject.toml`、`src/attestextract/__init__.py` | 同步版本号。 |

改动规模：业务代码 +192/-1 行；测试 +187/-0 行；文档 +105/-3 行；包配置 +1/-1 行。仅作审阅提示，不作为质量指标。

## 2. 核心行为变化

| 入口 | 原来 | 现在 |
| --- | --- | --- |
| `_build` / `build_annotation` | 详细 review 按对象与条件分组，值与引文分区，技术字段很多 | 简便任务按对象原标签集中展示；每条的条件、范围和出处仍独立，数组保留全部观测；无对象与非简便任务逐候选。 |
| `render_annotation` | 普通用户需在索引和逐候选明细间跳转 | 同一表格行内显示字段、原值、单位、条件、出处、证据和检查；不将程序 checked 说成准确。 |
| `save_annotation` | 无独立普通阅读格式 | 写 annotation.json / annotation.md 和 annotation.sources.json；原回答与完整结果仍是底层档案。 |
| `save_expert_review` / `export-review` | 保存两份详细审阅文件 | 追加三份简洁文件；旧档案离线导出，不读凭据、不发请求、不覆盖原目录。 |

没有改变提示词、任务编译、协议、调用预算、抽取结果 schema 或恢复身份。
不同表格的相同对象可能对应不同情景，所以出处保留在普通视图，不能全部当作技术字段移走。

## 3. 问题与边界

- 用户需要“抽到什么及依据”，详细 review 的来源偏移、模型和检查对象不适合作第一阅读入口；这些转移到独立映射，而非删除证据。
- 同对象的范围、均值或不同条件属于不同观测，只共同展示，不挑选、合并、转换范围或生成领域 ID。
- 原 annotation 格式只能作为版式参考，历史预测不是独立 gold。本轮不实现自动准确性判断；每份通用文件明确未人工核实。
- 有实质警告、失败检查或关联线索时继续显示。格式隔离声明不进入正常记录，错误类型不能使导出崩溃。
- 同义名称不自动合并；条件缺失不能视为条件相同；证据片段定位不等于科学归属正确。
- 可选体验：部分旧诊断仍显示原英文消息，不影响判断保留，后续可另行完善翻译。

## 4. 验证证据

| 本轮实际检查 | 结果与适用范围 |
| --- | --- |
| 相关 annotation、expert review、CLI 输出及导出测试 | **55 passed**；独立审阅重复核对，并额外验证包含 `/`、`~` 的字段名指针。 |
| 全套 `python -m pytest -q --basetemp <新的本机临时目录> --tb=short` | **2075 passed, 1 skipped**，295.39 秒；本机 TLS 测试仅回环，不访问供应商。 |
| 构建 wheel/sdist，全新环境无索引安装 | 已通过；安装前无 AttestExtract/LangExtract，安装后从 site-packages 导入，未安装 LangExtract。 |
| 已安装包的普通 Python extract 与 CLI export-review | 合成多条件同对象集中展示通过；禁用网络 socket；私有已存结果导出与源码版本一致，全部观测保留、源文件不变。 |
| 证据保护及提交检查 | 私有输入与输出做哈希核对，公开测试仅合成；提交前检查暂存文件和差异。 |

本轮真实模型调用：**0**。用户已完成的真实运行仅作只读审阅，不冒充本轮在线测试。
助手逐条核对的私有附件不进入公共代码，不自动应用到结果，也不是专家 gold。
CI：推送后另查，不以本地通过代替。

## 5. 人工审阅入口

| 固定标签代码位置 | 重点 |
| --- | --- |
| [annotation_view.py:89](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev32/src/attestextract/annotation_view.py#L89) | 显示分组不丢多值、条件或不同表的出处，不推断身份相同。 |
| [annotation_view.py:146](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev32/src/attestextract/annotation_view.py#L146) | 值、证据和检查同行；未人工核实、隔离声明与转义正确。 |
| [annotation_view.py:175](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev32/src/attestextract/annotation_view.py#L175) | 独占保存与脱敏；来源映射逐条对应。 |
| [expert_review.py:360](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev32/src/attestextract/expert_review.py#L360) | 附加文件集成，不更改或覆盖原结果。 |

## 6. 最终建议

- 必须修复：未发现剩余阻断，完整回归与安装验证均已通过。
- 可选优化：诊断的中文表述；不为缩短文件而自动选择正确候选。
- 当前可接受：作为通用结果阅读入口。准确性、遗漏、对象和情景归属仍需原文核对；没有新增 Jev、在线审核或领域专属规则。
