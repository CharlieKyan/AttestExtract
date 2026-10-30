# D2 验收边界与清单（0.1.0.dev9）

D2 的目标是完成统一模型通道、扩展方式和首批真实接入，并使普通用户在首次配置凭据后，只更换一个模型标识就能切换受支持模型。当前工程项已经完成。它不是所有服务商、所有模型和地域均可即插即用的声明，也不是抽取内容已全部符合人工标准的声明。

## 逐项核验

| D2 要求 | 当前实现与证据 | 结论 |
| --- | --- | --- |
| 统一请求 → 适配器 → 原生响应 → 统一响应 | `ExtractionRequest`、`BackendResponse`；兼容适配器显式转换请求与响应 | 完成 |
| 统一错误及边界处理 | 空回答、拒答、截断、协议异常、HTTP 错误有明确分类；不支持参数明确失败，不静默降级 | 完成 |
| 可配置地址、模型、凭据、超时 | `BackendConfig`、TOML profiles、环境变量、显式参数 | 完成 |
| 适配器注册与替换 | `BackendRegistry`；Python 注入和 CLI `main(..., registry=...)` 共用注册方式 | 完成 |
| 另一种响应格式的模拟验证 | `SyntheticEnvelopeBackend` 使用独立请求/响应信封；三类 D1 样例经过 CLI 的模拟 HTTP 验收 | 完成，模拟验证 |
| 更换模型只改配置或适配器 | 相同原文、任务与预期值；DeepSeek/Qwen 切换不改抽取内核 | 完成 |
| 最少配置的使用体验 | Python `model=`、CLI `--model-id`、离线 `models`；凭据首次配置后保留，仅改变模型标识 | 完成，限明确预设 |
| DeepSeek、Qwen 真实调用 | dev8 新引擎各三类合成样例，共六次成功调用；保留原始请求、响应、证据和统计于本地 | 完成，历史真实证据 |
| 新入口与已实测请求一致 | dev9 两个预设生成的六个完整请求体，与 dev8 保存的对应请求体逐项相同；路由/凭据隔离另由模拟 HTTP 验证 | 完成，离线验证 |
| 回归、独立审阅与安装包验收 | 新旧入口测试、独立只读审阅、wheel 在仓库外安装并执行 demo/D1/D2/模型切换；CI 覆盖 Windows/Ubuntu、Python 3.11/3.13 | 纳入发布检查 |

## 模型选择的范围

```python
from attestextract import extract

result = extract(text, task, model="deepseek:deepseek-flash", env_file=".env")
# 下次只改变 model：
result = extract(text, task, model="qwen:qwen3.8-27b", env_file=".env")
```

两行 `extract` 各自会请求对应真实服务；它们是使用示例，不是离线示例。一次性配置两个服务的凭据后，框架自动选择地址、参数与适配器。CLI 使用 `--model-id`，原有 `--model` 继续表示原生模型名。

首批预设只有 `deepseek:deepseek-flash`、`qwen:qwen3.8-27b`、`qwen:qwen-plus`。Qwen 快捷入口限定北京区；Plus 有配置与模拟覆盖，没有本轮真实验收记录。未知模型、其他地域和不同协议仍走高级 profile 或自定义适配器；自建兼容网关须显式绑定地址与凭据变量，或使用高级 profile。详见 [配置和扩展指南](BACKENDS.md)。

## 真实证据与内容质量分开报告

2026-10-30 保存的 dev8 官方接入记录：

| 检查 | DeepSeek Flash | Qwen 3.8-27B | 合计 |
| --- | --- | --- | --- |
| HTTP 200、正常结束及合法输出 | 3/3 | 3/3 | 6/6 |
| 机械证据定位到原文 | 16/16 | 16/16 | 32/32 |
| 与手工预期严格内容一致 | 2/3 | 2/3 | 4/6 |

两个条件观测样例的样本名称与手工预期存在类别前缀差异；数值、单位、两个不同温度条件和两条观测均保留。没有为通过验收修改预期、原始响应或抽取值。原文定位成功、`needs_review=false` 和 HTTP 成功均不能替代语义验收。该差异需要后续明确标注口径与质量评价；不得将 D2 工程完成解释为 6/6 内容完全正确。

dev9 本轮新增真实调用为 **0**。旧的余额不足失败记录也保留于本地，与后来获追加授权的成功调用分开。真实凭据、原始模型输出和运行日志不进入公开仓库。

## 可复现的离线验收

安装后在源码检出目录运行，输出目录必须是新目录：

```powershell
python scripts/model_selection_smoke.py --output-dir ../research/attestextract/runs/dev9-switch-example
python scripts/d2_acceptance.py --mode mock --output-dir ../research/attestextract/runs/dev9-envelope-example
python -m pytest -q
```

第一个脚本使用合成原文 `Lamp A uses 8 W.`，通过两个模型标识分别执行 Python 和 CLI，共四次模拟 HTTP 请求。两种入口都必须保留值 `8` 和原文范围 `[12, 13)`；凭据、服务地址、输出模式和 token 上限也被核验。第二个脚本验证另一种信封格式及三类任务。两个脚本都不访问真实模型。

安装验收会把脚本复制到仓库之外，以全新环境执行，并检查导入来自已安装 wheel。具体测试数量、安装路径、提交、推送和 CI 状态以该版本的发布核验记录为准；不把示例命令或 CI 配置本身当作已执行的结果。
