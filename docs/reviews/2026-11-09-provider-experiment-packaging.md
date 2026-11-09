# 审查摘要：实验源码包清单修正

- 基准：`aa672771754116fd40fdc95c4335ed7410d93f15`，dev21 发布后文档提交。
- 目标：`v0.1.0.dev22`；保留 dev21 已发布提交与标签。
- 范围：打包清单、版本元数据与说明；没有抽取、观测、实验调度或评分行为变化。
- 原因：最后的源码包内容检查发现实验脚本在仓库存在，但 MANIFEST 未列入，源码发行包缺少入口与辅助模块。
- 本轮真实模型调用：0；用户自行执行在线实验。

改动规模：业务逻辑与测试均0；CI +20/−6；打包与版本元数据 +7/−2；文档 +44/−1。行数只提示审阅范围。

## 文件与行为

`MANIFEST.in` 显式包含5个 provider_experiment 脚本；`pyproject.toml`、`src/attestextract/__init__.py`、CI版本断言和中文README升级dev22。
英文README及CHANGELOG说明打包修正。本摘要与索引记录修复。旧的dev21标签保持不变，不将补丁伪装成原标签内容。

核心验收增加直接检查源码包成员，并从源码包按文件白名单读取这5个文件进行独立wheel安装验收。
CI 同步增加同一断言，不再从 checkout 复制实验脚本；逐字节对照后只写出指定文件，不解压任意归档路径。
不重新运行未改变行为的整套测试；dev21本轮已1283 passed、3 skipped，完整108试次测试已加强后单独通过。

## 证据与审阅

- `tests/test_version_metadata.py`：2 passed。
- `python -m build --no-isolation`：wheel 和 sdist 构建成功。
- 新环境以本地依赖缓存离线安装 wheel，`pip check`、导入路径及全部 Python 模块字节核验通过，未安装 LangExtract。
- 直接执行 CI 的 Python 验收段（仅替换为离线 pip、指定 dev22 包和工作区临时目录）：demo、D1 和10个独立脚本全部通过。
- 新实验5个脚本来自 sdist，逐字节等于源码；108模拟试次、三次全新16块抽取通过，重复阶段新增请求0。
- wheel SHA256：`a553c28dce9dfc13f67dd70bbc1faeb73e46f270107a892e8884a5a5181656ad`。
- 私有 dev22 材料已离线冻结和检查，原文/任务/请求/配置等10个文件与旧包逐字节一致；旧目录未修改，生成请求0。
- 独立只读审阅：两位代理分别核对打包/元数据与 CI sdist 读取，均无必须修复项；内嵌 Python 在3.11/3.13语法模式解析通过。
- 源码与凭据检查：沿用本轮公开文件审查，私有实验包继续留在research目录。
- 发布与CI：本摘要随实现提交，交付时记录实际状态。

## 人工重点与结论

重点核对 [MANIFEST.in:15](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev22/MANIFEST.in#L15) 的5个入口/辅助模块是否一起进入sdist，以及 [CI:52](https://github.com/CharlieKyan/AttestExtract/blob/v0.1.0.dev22/.github/workflows/ci.yml#L52) 是否从正确版本运行安装包。
修改仅修正源码包可复现性；不更改实验请求或调用上限。完整实现审查见[dev21审查](2026-11-09-provider-experiments.md)。
