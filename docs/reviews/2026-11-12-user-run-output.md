# 审查摘要：普通用户输出目录与执行一致性

- 比较基准：`e62ca36421200bd7353a60d0fe9fea233004d91b`（dev29），任务开始时工作区干净。
- 审查目标：固定标签 `v0.1.0.dev30`，本摘要与代码同提交；不引用移动的 main。
- 范围：公开通用内核、合成测试、安装验收脚本及文档。私有用户项目、论文、凭据模板与验收日志不进入本仓库。
- 审查方式：实现者自查；未参与dev30实现的独立代理只读审阅源码、测试、CI和文档，未发现阻断问题，独立运行新测试33通过。
- 版本与发布：`0.1.0.dev30`；本摘要记录发布前检查，实际提交、推送和托管CI状态另行报告。

## 1. 变更概览

| 文件 | 职责与原因 |
| --- | --- |
| `src/attestextract/run_output.py` | Python与CLI共用目录保护和结果保存，复用既有报告及诊断，避免两个入口各自实现生命周期。 |
| `src/attestextract/pipeline.py` | `output_dir`、可选进度、自动收尾；普通模型默认启用执行管理，旧后端注入保留原默认行为。 |
| `src/attestextract/cli.py` | `--output-dir`布局、参数互斥、新目录恢复命令；现有高级输出参数保持兼容。 |
| `tests/test_python_output_dir.py`、`tests/test_cli_output_dir.py` | 输入保护、默认预算一致、成功/失败/中断/部分结果、凭据遮蔽及恢复不改历史。 |
| `scripts/user_entry_smoke.py`、`tests/test_user_entry_smoke.py` | 仓库外安装包的Python/CLI合成HTTP验收；检查真实加载路径、恢复和未知超时不重发。 |
| `.github/workflows/ci.yml`、`MANIFEST.in` | 源码包收录新验收脚本，CI在独立wheel环境执行，核查版本与模块路径。 |
| `pyproject.toml`、`src/attestextract/__init__.py` | 同步开发版本。 |
| `README.md`、`README.zh-CN.md`、`CHANGELOG.md`、`docs/SIMPLE_TASKS.md` | 用户安装/运行、输出目录与兼容边界。 |
| 本摘要、`docs/reviews/README.md` | 审查来源、证据与索引。 |

改动规模：业务与验收脚本新增252行、删除21行；测试新增498行；文档新增99行、删除6行；配置新增19行、删除8行。行数不代表质量。

## 2. 核心行为

| 入口 | 修改前 | 修改后 |
| --- | --- | --- |
| Python `extract` | 只返回对象；用户手动建立trace并保存报告、结束运行。 | 指定`output_dir`后自动保存完整运行目录并仍返回对象；省略时仍可只取内存结果。 |
| 普通Python模型预设 | 纯正文未显式给execution时没有执行账本；与CLI默认分块/预算不同。 | 所有`model=`入口默认使用ExecutionOptions，已有预算、细分、恢复策略统一生效。 |
| CLI输出 | 自定义目录需三个路径，单独`--output`不会自动产生报告和日志。 | `--output-dir`一次指定三者；与三个高级参数混用明确拒绝。 |
| 目录保护 | 旧高级文件参数可显式指定文件。 | 新入口必须新目录或空目录；恢复不能写入历史目录，结果文件独占创建，拒绝运行期间新增文件冲突。 |
| 成败标识 | CLI封装生命周期，Python调用者自行管理。 | 新Python输出入口覆盖异常和KeyboardInterrupt；两个文件保存成功后才finish，表格部分格式状态不伪装成成功。 |
| 恢复展示 | CLI恢复命令回到默认输出布局。 | 使用新目录入口时，恢复命令指向新的同级目录；Python提示原检查点与新目录要求。 |

## 3. 风险与边界

- 输出目录是一次运行的确切位置；四篇通过调用者顺序循环、四个独立目录运行，不新增批次级共享预算或调度服务。
- 输入配置错误可能在建立运行目录前就报错；运行阶段失败保留已有诊断与检查点。输出磁盘故障可能留下部分文件，但不会将其标记为成功。
- `output_dir`不能与自管`trace`混用；自管trace仍由调用者完成。自定义后端持久化仍需明确后端身份。
- 旧检查点兼容修订不变：本轮不改提示词、解析、表格或分块算法。只对普通Python入口默认启用现有执行策略；旧无检查点运行无法恢复。
- 结果schema及原值/证据契约不变；未连接Jev，空块或未报警不等于事实正确。
- 安装在终端完成；Git标签发布不等于PyPI版本可用。公共样例均合成，私有项目从wheel正常安装，不使用源码路径注入。

## 4. 验证证据

| 实际检查 | 结果 | 边界 |
| --- | --- | --- |
| 新输出入口及安装runner相关测试 | 33 passed；后补文件碰撞专项1 passed | 合成数据及MockTransport，无真实模型请求。 |
| CLI旧入口/进度/简便任务/表格回归 | 73 passed | 高级输出参数、partial状态与旧接口兼容。 |
| Python模型选择/执行/核心/自动执行回归 | 113 passed，1 skipped | Windows符号链接权限限制；非线上验收。 |
| 安装后的`user_entry_smoke.py` | 通过，3次模拟POST、0真实网络 | Python/CLI候选相同；完成恢复0新增调用；超时只发送一次，失败检查点保留。 |
| 独立用户项目 | Python离线准备与四篇公开CLI预检全部通过，14目标与4示例保留；模块确在新venv的site-packages，无LangExtract，wheel的52个Python文件与源码逐字节相同 | 只使用假凭据，5个进程网络阻断均加载且0尝试；不把预检当科学抽取质量验证。 |
| 完整回归 | 2002 passed，1 skipped，409.48秒 | 项目Python执行`python -X utf8 -m pytest -q --basetemp <唯一普通本机TEMP> --tb=short --junitxml <私有记录>`，源码冻结，允许回环TLS；跳过仅因Windows账号不能创建符号链接。 |
| 独立只读审阅及发布文件检查 | 无阻断；33 passed；17个公开文件隐私扫描通过 | 核查路径、生命周期、密钥和兼容性；长Windows临时路径首轮失败，缩短目录后不改代码通过。 |

真实付费调用：0。私有论文线上命令交用户执行，不报告整篇准确率或召回率。GitHub CI以实际提交关联运行记录为准。

## 5. 人工重点

1. [Python入口](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev30/src/attestextract/pipeline.py#L622)：默认执行管理的范围、trace所有权、失败和finally清理。
2. [共享目录保护](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev30/src/attestextract/run_output.py#L14)：先检查历史路径，再创建空目录，不能覆盖输入或旧结果。
3. [共享保存](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev30/src/attestextract/run_output.py#L29)：保存失败不能finish；密钥须在JSON和报告转义前遮蔽。
4. [CLI恢复命令](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev30/src/attestextract/cli.py#L207)：使用新目录且保留原运行配置，不自动扩大预算。
5. [安装包验收](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev30/scripts/user_entry_smoke.py#L37)：真实加载wheel、阻断网络、与线上验证清楚区分。

## 6. 结论

- 必须修复：无未解决的阻断项；完整回归、独立安装及审阅均已通过。
- 可选优化：批次级管理和更简化的文档文件读取可单独设计，不扩大本轮内核范围。
- 当前可接受：一次运行自动保存、两个入口执行默认一致；保留内存调用、老任务及高级配置，不改变科学判断边界。
