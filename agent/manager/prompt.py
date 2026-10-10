"""经理的 system prompt —— 工具清单**渲染进正文**，不走 `tools` 字段。

这是方案 B 与「正常用法」唯一的外观差异，而它是被实测逼出来的：
§5.1 里给 Ollama 一个 `tools` 数组，换回来的是 0 条调用或者 HTTP 500；
把同一份清单写进 system message，模型立刻给出正确调用。

所以这里**刻意**不拼 `tools` 字段。谁要「顺手把它加上去」，请先回看 §5.1。
"""

from __future__ import annotations

import json

from agent.manager.parser import (
    DEEPSEEK_CALLS_BEGIN,
    DEEPSEEK_CALLS_END,
    DEEPSEEK_CALL_BEGIN,
    DEEPSEEK_CALL_END,
    DEEPSEEK_SEP,
)
from agent.manager.tools import ToolRegistry

_HEAD = """你是 Bianca 的经理 Agent，负责把用户给出的交易目标拆成可执行的计划。

你可以调用下面这些工具去了解**真实**情况。它们全部是**只读**的 ——
不会下单，也不会改动任何状态。"""

_CALL_FORMAT = f"""## 怎么调工具

需要调用时，**只输出**下面这个结构，前后不要有任何别的文字：

{DEEPSEEK_CALLS_BEGIN}{DEEPSEEK_CALL_BEGIN}function{DEEPSEEK_SEP}工具名
```json
{{"参数名": 参数值}}
```
{DEEPSEEK_CALL_END}{DEEPSEEK_CALLS_END}

也可以用 ```json 围栏直接给出 {{"name": "工具名", "arguments": {{…}}}} —— 这两种都认。
除此之外的写法一律不会被当作调用。"""

_DISCIPLINE = """## 纪律

- 不知道的事情用工具去查。**不要凭记忆编数字，也不要假装调用过工具。**
- 一次可以调多个工具。信息够了就直接给计划，不要再调。
- 计划要说清三件事：每一步做什么、依据是什么、**在什么条件下应当终止**。
- 你**没有**下单权限。计划是给人看的，不是给你自己执行的。"""


def render_tool_catalog(registry: ToolRegistry) -> str:
    """工具清单的 JSON 形态 —— 喂给模型看的正是这一份（不是 `tools` 字段）。"""
    return json.dumps(registry.schemas(), ensure_ascii=False, indent=2)


def build_system_prompt(registry: ToolRegistry) -> str:
    catalog = render_tool_catalog(registry)
    return "\n\n".join(
        [_HEAD, f"## 可用工具\n\n```json\n{catalog}\n```", _CALL_FORMAT, _DISCIPLINE]
    )
