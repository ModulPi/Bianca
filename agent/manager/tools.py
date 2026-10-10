"""经理层的工具注册表（阶段 B：只有只读工具）。

**为什么「只有只读」要写死在注册口，而不是靠调用方自觉。**
阶段 B 的验收是「经理产出计划，**不产生任何订单**」。如果这条只靠一句
「我们只注册只读的」撑着，那么某天有人顺手把一个会下单的工具注册进来，
验收条件会**静默失效** —— 计划照常产出，订单也悄悄产生了，没有任何一处报错。
所以拦截点放在 `register()`：非只读的工具根本进不来。

这与 ADR-027 的拦截原则同源：**越权的那一步要在它可能发生的地方就断掉，
不留给人去记得。**
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Any

ToolHandler = Callable[[dict[str, Any]], Awaitable[Any]]


class ToolError(Exception):
    """工具层基类，方便调用方一把兜住。"""


class ToolRegistrationError(ToolError):
    """注册被拒。"""


class UnknownToolError(ToolError):
    """点名了一个不存在的工具。

    **必须报出来，不能静默跳过** —— 静默跳过等于把「它以为发生过的动作」
    当成发生过了。§5.1 实测里那个编造 `get_balance` 并报出 `0.00 BTC` 的模型，
    正是这么骗过下游的。
    """


class ToolArgumentError(ToolError):
    """参数不满足 schema 里的必填项 —— 不替模型猜。"""


@dataclass(frozen=True)
class ToolSpec:
    """一个工具的全部定义。`parameters` 是 JSON Schema（`type: object`）。"""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    read_only: bool = True

    def schema(self) -> dict[str, Any]:
        """OpenAI 风格的函数签名 —— 渲染进 prompt 给模型看的就是这一份。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    @property
    def required(self) -> tuple[str, ...]:
        raw = self.parameters.get("required") or ()
        return tuple(str(k) for k in raw)


class ToolRegistry:
    """工具名 → `ToolSpec`。阶段 B 默认**拒绝**会改状态的工具。"""

    def __init__(self, *, allow_writes: bool = False) -> None:
        # 留这个开关是为了阶段 D：那时经理才被授权动钱，且必须留下痕迹（ADR-027）。
        # 现在默认 False，且**没有任何调用方**把它设成 True。
        self._allow_writes = allow_writes
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if not spec.read_only and not self._allow_writes:
            raise ToolRegistrationError(
                f"拒绝注册会改状态的工具 {spec.name!r}："
                "阶段 B 的经理只有只读工具，验收条件是「不产生任何订单」。"
                "要开放写操作，得先改这里的 allow_writes 并补上留痕（ADR-027）。"
            )
        if spec.name in self._tools:
            raise ToolRegistrationError(f"工具名重复：{spec.name!r}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def require(self, name: str) -> ToolSpec:
        spec = self._tools.get(name)
        if spec is None:
            known = ", ".join(sorted(self._tools)) or "（一个都没有）"
            raise UnknownToolError(f"没有名为 {name!r} 的工具。已注册的是：{known}")
        return spec

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(self._tools[n] for n in sorted(self._tools))

    def schemas(self) -> list[dict[str, Any]]:
        return [spec.schema() for spec in self.specs()]

    async def call(self, name: str, arguments: dict[str, Any]) -> Any:
        """调一个工具。**先把参数校验做完再进 handler** ——
        让 handler 自己面对缺参数，等于让它替模型猜默认值。"""
        spec = self.require(name)
        missing = [k for k in spec.required if k not in arguments]
        if missing:
            raise ToolArgumentError(
                f"工具 {name!r} 缺少必填参数：{', '.join(missing)}"
                f"（需要：{', '.join(spec.required)}）"
            )
        return await spec.handler(arguments)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self) -> Iterator[ToolSpec]:
        return iter(self.specs())
