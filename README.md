# AstrBot 语义阻断自动切换

这个插件适用于主模型的内容阻断但缺以普通文本返回（例如：我是一个人工智能，并不能参与相关话题的话题），而不是返回 API 错误。AstrBot会正常返回内容，并把拒答文本直接发给用户，原有的备用模型切换不会触发。

插件会在 AstrBot 的 Agent runner 中检测当前主模型的完整响应。识别到拒答后，插件将响应转换成 AstrBot 原生 fallback 流程可以识别的 `role="err"`，切换为 AstrBot 配置目录里选择的备用模型，触发原生 fallback 流程。

## 设计原则

- 不在插件中重新选择主模型或备用模型。
- 主模型、备用模型、prompt、人格、上下文和工具调用全部沿用 AstrBot 当前会话配置。
- 备用模型响应不会再次被主模型拒答规则拦截。
- 不监听日志，也不额外调用 LLM 判断，不增加模型请求次数。
- 当前主要面向非流式模式；流式保护分支用于防止未来误开启流式时泄露拒答片段。

## 安装

将插件目录放到：

```text
AstrBot/data/plugins/astrbot_plugin_semantic_fallback/
```

目录中应包含：

```text
metadata.yaml
main.py
_conf_schema.json
README.md
```

插件只使用 Python 标准库和 AstrBot 自身接口，不需要额外安装依赖。安装后重启 AstrBot，或在 WebUI 中重载插件。

## 配置

插件配置可以在 AstrBot WebUI 的插件配置面板中修改：

- `启用语义阻断检测和备用模型切换`：总开关。
- `触发语义阻断所需的最少规则命中数`：默认是 `2`。建议先保持为 `2`，减少误判。
- `用于识别主模型拒答文本的正则表达式列表`：每一项都是一条独立规则。
- `在检测流式主模型响应前暂存内容`：当前不使用流式时保持默认即可。
- `检测到语义阻断时写入日志`：建议保持启用，方便确认是否触发。

规则检测的是主模型的回复文本，不是用户输入，也不是由另一个 LLM 判断。列表中的每一项按正则表达式进行包含匹配。例如：

```text
无法继续
角色扮演
身体接触
作为人工智能
```

当一条主模型回复至少命中两条规则时，插件会将它交给 AstrBot 原生 fallback 流程。

## 日志确认

成功触发时，日志中应出现：

```text
[semantic_fallback] classified primary response as refusal
Chat Model ... returns error response, trying fallback to next provider.
Switched from ... to fallback chat provider: ...
```

## 兼容性说明

插件目标版本为 AstrBot `4.27.4` 及后续 `4.x` 版本。插件运行时包装了 AstrBot 的内部 Agent runner 方法，因此升级 AstrBot 后应重新测试；如果内部方法签名变化，插件会记录不兼容错误并停用。

## 发布信息

本仓库根目录就是插件目录，包含 `metadata.yaml`、`main.py` 和配置文件。市场记录中的 `author/name`、`version`、`repo` 应与 `metadata.yaml` 保持一致；当前仓库已使用 `skyhuangjx/astrbot_plugin_semantic_fallback` 身份。

如果手动打包用于本地安装，请将这些文件放入 `AstrBot/data/plugins/astrbot_plugin_semantic_fallback/`，并不要把 `.git`、`__pycache__` 等开发文件放入发布压缩包。AstrBot 插件市场要求压缩包小于 16 MB。
