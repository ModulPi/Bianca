"""经理的 system prompt —— 工具清单**渲染进正文**，不走 `tools` 字段。

这是方案 B 与「正常用法」唯一的外观差异，而它是被实测逼出来的：
§5.1 里给 Ollama 一个 `tools` 数组，换回来的是 0 条调用或者 HTTP 500；
把同一份清单写进 system message，模型立刻给出正确调用。

所以这里**刻意**不拼 `tools` 字段。谁要「顺手把它加上去」，请先回看 §5.1。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from agent.manager.parser import (
    DEEPSEEK_CALLS_BEGIN,
    DEEPSEEK_CALLS_END,
    DEEPSEEK_CALL_BEGIN,
    DEEPSEEK_CALL_END,
    DEEPSEEK_SEP,
)
from agent.manager.schemas import Plan
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


# ------------------------------------------------- 执行阶段：一个信号，不是一段计划

_DECISION_HEAD = """你是 Bianca 的经理 Agent，正在执行一个**已经确认**的交易目标。

你有只读工具可以查真实情况；你**没有**下单权限。你的输出是一个信号，
风控与下单由系统确定性地完成 —— 你不能决定"跳过风控"，也不能声称"已经下单"。"""

_DECISION_FORMAT = """## 输出信号

信息够了之后，**只输出下面这一个 JSON**，不要有别的内容：

```json
{"action": "BUY | SELL | HOLD", "symbol": "BTCUSDT", "amount": 12.5,
 "reason": "为什么这么做", "plan_step": 2, "deviation": null}
```

- `amount` 的口径：**BUY 是计价币名义金额（USDT）；SELL 是基础币数量。**
  口径错了会得到一笔完全不同大小的单，所以它必须写清。
- `plan_step`：这一步对应计划里的第几步（从 1 开始）。没按计划走就填偏离后的
  实际第几步，并在 `deviation` 里写清为什么 —— **偏离允许，不说明不允许**（ADR-039）。
- 不该动手就 `{"action": "HOLD", "reason": "在等什么"}`。HOLD 不需要 symbol/amount。
- 工具拿不到数、或你看不清局面时，HOLD 比猜一个动作安全。"""

_DECISION_DISCIPLINE = """## 纪律

- 计划是**指导，不是脚本**：它可以过时、可以不适用。但偏离必须在 `deviation` 里说清。
- **不要凭记忆编数字。** 需要用数就去调工具；工具说拿不到，就把"拿不到"当作事实。
- 一次 tick 最多执行一笔。不要试图在一轮里安排多笔。"""


def build_decision_prompt(registry: ToolRegistry) -> str:
    catalog = render_tool_catalog(registry)
    return "\n\n".join(
        [
            _DECISION_HEAD,
            f"## 可用工具\n\n```json\n{catalog}\n```",
            _CALL_FORMAT,
            _DECISION_FORMAT,
            _DECISION_DISCIPLINE,
        ]
    )


def render_decision_context(
    brief: str, plan: Plan | None, state: Mapping[str, Any]
) -> str:
    """执行这一轮要看的全部上下文：目标、计划、当前状态。

    `state` 由调用方从库里/快照里取，**渲染时一个数都不补**：这里缺什么，
    模型就该看到缺什么（与只读工具的 `available: false` 同一条原则）。
    """
    lines = [f"目标（已确认）：\n{brief.strip()}", "", "计划（指导，不是脚本）："]
    if plan is None or not plan.steps:
        lines.append("（这个目标还没有计划 —— 按目标和当前状态判断）")
    else:
        for index, step in enumerate(plan.steps, 1):
            why = f"（依据：{step.why}）" if step.why else ""
            lines.append(f"{index}. {step.what}{why}")
        if plan.stop_conditions:
            lines.append("计划里写的停止条件：" + "；".join(plan.stop_conditions))

    lines += [
        "",
        "当前状态（系统给的，不是你猜的）：",
        json.dumps(state, ensure_ascii=False, indent=2, default=str),
        "",
        "现在给出这一轮的信号：只输出一个 JSON。",
    ]
    return "\n".join(lines)
