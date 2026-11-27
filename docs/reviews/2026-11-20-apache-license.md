# Apache-2.0 许可与分发包验收

2026-11-20。用户指定的基准为 `8eda38bf79dbaa1b4df865b9a732b4fe1ad9c6e6`，发布分支初始工作区干净。许可提交为 [`e034c6a`](https://github.com/CharlieKyan/AttestExtract/commit/e034c6aa23bc6c0a8ebbe42dedbe8a99860fd98e)。本摘要及索引在后续文档提交中单独归档。包版本为 `0.1.0.dev41`，本步骤未创建版本标签。

## 文件与归属

- `LICENSE` 收录 [SPDX 分发的 Apache-2.0 英文全文](https://spdx.org/licenses/Apache-2.0.html)，含附录。`NOTICE` 记录项目名及用户指定的版权人 Yuzhi Kuang。
- `pyproject.toml` 声明 SPDX 许可表达式和随包文件，将构建依赖 setuptools 最低版本从 69 调整为 77.0.3 以支持 PEP 639。安装后的运行依赖与抽取接口沿用基准版本。
- `README.md`、`README.zh-CN.md` 将先前的未授权说明更新为许可和署名链接，并保留 PyPI 状态。`docs/reviews/README.md` 收录本摘要。
- 许可提交含许可与署名 75 行、打包配置 +3/−1、中英文文档 +6/−2；业务代码与测试文件改动均为 0。人工审阅来源为实现者自查；另有独立只读核查发布基准上的合成样例与打包清单。

## 行为及依据

此前根目录和软件包元数据缺少许可证。源码、wheel 和安装后的分发元数据现均可用于确认授权和署名。此次没有修改抽取函数、结果格式或对用户输入和科研数据的处理。已跟踪的 D1 样例有独立合成原文、任务和手工预期，模拟审阅标为 `synthetic_fixture`。本次暂存的许可提交仅含上述五个文件，常见凭据模式扫描未命中。

## 验证

- 在固定基准导出树应用许可文件和配置，用项目 Python 3.13.11、setuptools 84.2.1 执行 `python -m build --no-isolation`，生成 sdist 并从该 sdist 构建 wheel。第一次 wheel 构建失败，在项目临时目录重新构建成功。
- 在独立虚拟环境使用本地 wheelhouse 执行 `pip install --no-index`，`pip check` 通过。逐项比较源树、sdist、wheel 和安装环境的 `LICENSE`、`NOTICE` 字节，并在三处包元数据核对 `License-Expression: Apache-2.0` 与两个 `License-File` 字段。
- 在独立环境运行 `attestextract demo --example ccus --output sample.json`，读回两条合成候选，运行模式为 `replay`。提交前执行 `git diff --cached --check`。本次无运行时代码改动，未重跑完整测试套件。模拟 HTTP 和真实端点调用均为 0。
- 这些检查覆盖许可提交。随后归档的两个审查文档仅做内容和差异检查。GitHub Actions 状态在交付时单独报告。

## 人工重点与结论

1. [NOTICE 第 2 行](https://github.com/CharlieKyan/AttestExtract/blob/e034c6aa23bc6c0a8ebbe42dedbe8a99860fd98e/NOTICE#L2) 核对公开署名。
2. [pyproject.toml 第 12 行](https://github.com/CharlieKyan/AttestExtract/blob/e034c6aa23bc6c0a8ebbe42dedbe8a99860fd98e/pyproject.toml#L12) 核对许可标识及分发文件。
3. [中文 README 第 341 行](https://github.com/CharlieKyan/AttestExtract/blob/e034c6aa23bc6c0a8ebbe42dedbe8a99860fd98e/README.zh-CN.md#L341) 核对读者可直接访问的许可和署名。

在本次许可与打包范围内未见遗留的必修问题，当前可接受。首版入门说明、最终版本号和正式发布另行处理。
