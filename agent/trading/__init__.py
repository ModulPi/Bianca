"""交易账本：订单状态机、成交对账、持仓重建。

与 `agent/graph/` 的分工：graph 负责"要不要交易"，这里负责"交易到底成了没"。
本包不 import graph，也不 import llm —— 它可以在没有任何 LLM 的情况下被单测。
"""
