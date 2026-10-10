"""经理层（阶段 B —— 经理只说不做）。

与 `agent/graph/` 下那几个子 Agent 的区别：子 Agent 是**确定性**的节点，
经理层是**不确定**的那一层 —— 它读目标、调只读工具、吐计划。
不确定性只留在这里，交易执行路径不受它影响（设计文档 §4）。
"""

from agent.manager.parser import ParsedReply, ToolCall, parse_reply
from agent.manager.tools import (
    ToolArgumentError,
    ToolError,
    ToolRegistrationError,
    ToolRegistry,
    ToolSpec,
    UnknownToolError,
)

__all__ = [
    "ParsedReply",
    "ToolArgumentError",
    "ToolCall",
    "ToolError",
    "ToolRegistrationError",
    "ToolRegistry",
    "ToolSpec",
    "UnknownToolError",
    "parse_reply",
]
