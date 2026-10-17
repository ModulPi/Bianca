"""会话循环（阶段 C-3）：把 C-1 的实体、C-2 的解析复述串成一轮一轮的对话。

这一层自己不判对错，它只做四件事：
**读一句 → 改草稿 → 念回去 → 记账**。判据全在下游已经钉住的地方
（`extract` 归不了类就报 `unclear`，`GoalDraft.revise` 只动给了的字段，
`GoalRepository` 只让 `proposed` 写进去）。

两条规矩值得单独说：

**一、确认是一个独立动作，不从正文里认（ADR-037）。**
`say()` 里没有任何一条路径能把目标变成 `active`，只能靠 `confirm()`。
这不是洁癖：「这样就对了，但是回撤再宽一点」正是这一层要处理的标准修正句，
在正文里认「对」就会在用户正要改参数的那一刻把风控冻上（ADR-018 之后不再回头问）。
所以 `say()` 的返回类型里**没有** `activated` 这条路，`confirm()` 才是唯一入口。

**二、拒绝也要记账。**
用户在一个已经生效的目标上说了「回撤改到 8%」——那句话**照记**，回绝的话也照记。
不记的话，事后翻记录看到的是"用户从没提过这个要求"，而真相是我们没照做。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from agent.config import Settings
from agent.dialogue.extract import Extract
from agent.dialogue.extract import extract as _default_extract
from agent.dialogue.goal import GoalDraft, GoalStatus
from agent.dialogue.retell import retell
from agent.storage.models import GoalLog, PlanLog

if TYPE_CHECKING:  # pragma: no cover
    from agent.manager.loop import Manager
    from agent.storage.repository import GoalRepository, MessageRepository, PlanRepository

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"


class GoalNotFound(ValueError):
    """目标 id 不存在。

    单独一个类型是为了让 HTTP 层映射成 **404** 而不是 422 —— 「你给的 id 不对」
    和「你的请求内容不对」对调用方是两件事。
    """


class OpenGoalExists(ValueError):
    """已经有一个没结束的目标。阶段 C 一次只跑一个（§8 第 3 条），映射成 409。"""


@dataclass
class Reply:
    """一轮对话的结果。

    `text` 是**回给用户看的话**，`draft` 是这一轮之后的草稿（被回绝时就是原样）。
    `changed` 是这一轮真正改到的字段 —— C-3 的验收要的就是"只改了这一个"，
    所以它得是返回值的一部分，而不是让调用方去比对前后两份草稿。
    """

    goal_id: str
    status: GoalStatus
    text: str
    draft: GoalDraft
    changed: list[str] = field(default_factory=list)
    unclear: list[str] = field(default_factory=list)
    activated: bool = False
    #: 这一次尝试对应的 `plans` 行（成功失败都有）。**它不等于"目标已经有计划"** ——
    #: 只有 `goal.plan_id` 有值才是。分两个字段就是为了让这两件事别混。
    plan_id: str | None = None


def goal_brief(row: GoalLog) -> str:
    """把**已确认**的目标渲染成给经理的一段话（C-4 的输入）。

    喂的是生效值，不是用户口述值：

      · 回撤线取 `risk_snapshot` 里冻住的那个（ADR-036 / ADR-021）；
      · 收益率基准取 `baseline_equity`（ADR-033）。

    这两条都是为了让计划建立在**执行时真的会成立**的数上。拿用户说的 5%
    去拆计划、而实际会在 3% 停，那份计划从第一行起就是错的。

    顺带把只读的风控边界也给它 —— 一个不知道单笔上限的经理，会做出一个
    第一条就执行不了的计划。
    """
    symbols = json.loads(row.symbols) if row.symbols else []
    frozen = json.loads(row.risk_snapshot or "{}")

    lines = ["已确认的目标："]
    if symbols:
        lines.append(f"- 标的：{'、'.join(symbols)}")
    if row.target_return_pct is not None:
        lines.append(f"- 收益目标：+{row.target_return_pct * 100:g}%")
    if "max_drawdown_pct" in frozen:
        stated = row.max_drawdown_pct
        note = ""
        # 只在**配置赢**的时候解释差异。用户说得更严（2% vs 配置 5%）时，
        # 生效值就是他要的那个数，多一句「取更紧的」反而会让他以为被改了。
        if stated is not None and float(stated) > float(frozen["max_drawdown_pct"]) + 1e-12:
            note = f"（用户说的 {float(stated) * 100:g}% 更宽，取更紧的那个）"
        lines.append(f"- 回撤上限（生效）：{float(frozen['max_drawdown_pct']) * 100:g}%{note}")
    if row.horizon_hours is not None:
        lines.append(f"- 时间窗：{row.horizon_hours:g} 小时")
    if row.baseline_equity is not None:
        lines.append(f"- 基准权益：{row.baseline_equity:g} USDT（收益率按它算，ADR-033）")

    # 量纲在这里显式写出来，不靠字段名去猜：`max_trade_amount` 是 USDT，
    # `max_position_pct` 是比例。猜错一次，经理就会拿到一个差 100 倍的边界。
    bounds = [
        ("单笔上限", "max_trade_amount", "usdt"),
        ("每日亏损上限", "daily_loss_limit", "usdt"),
        ("最大仓位", "max_position_pct", "pct"),
        ("单笔止损", "stop_loss_pct", "pct"),
    ]
    shown_bounds = [
        (label, float(frozen[key]), unit) for label, key, unit in bounds if key in frozen
    ]
    if shown_bounds:
        lines.append("- 风控边界（只读，ADR-021）：")
        for label, value, unit in shown_bounds:
            if unit == "pct":
                lines.append(f"    · {label}：{value * 100:g}%")
            else:
                lines.append(f"    · {label}：{value:g} USDT")

    lines.append("- 授权：全自动（ADR-018）")
    lines.append("")
    lines.append(f"用户原话：「{row.raw_text}」")
    return "\n".join(lines)


def _as_record(parsed: Extract) -> dict[str, Any]:
    """把第一次解析的结果原样留一份（ADR-020）。

    与目标列分开存：用户改过之后两者会不一样，而「到底是机器读错了还是用户改了」
    在归因时是两件事。这里只留**第一句**的解析 —— 后来的每一句都在 `messages` 里
    有原文，不必在这里再存一份不同形状的东西。
    """
    return {
        "values": parsed.values,
        "evidence": parsed.evidence,
        "unclear": parsed.unclear,
        "derived": parsed.derived,
    }


class Dialogue:
    """一次一个目标的对话循环（§8 第 3 条）。

    `extractor` 是留给 C-2b 的插口：语义层（认标的的兜底、判定要不要授权）要真模型，
    换进来时**换的只是这一个函数**，循环本身不动。
    """

    def __init__(
        self,
        *,
        settings: Settings,
        goals: GoalRepository | None = None,
        messages: MessageRepository | None = None,
        plans: PlanRepository | None = None,
        manager: Manager | None = None,
        extractor: Callable[[str], Extract] | None = None,
    ) -> None:
        # 仓库在这里**才**导入，不在模块顶层。`agent.storage.repository` 自己要
        # `from agent.dialogue.goal import ...`，那会先把 `agent.dialogue` 这个包
        # 跑一遍；包再拉 `session`，而 session 顶层又要 repository —— 那时它只导入
        # 到一半，`GoalRepository` 还没定义。放在这里，两个方向就错开了。
        #
        # 这两个类本来就只是**默认值**（构造函数收的就是注入），所以推迟导入
        # 没有改变任何设计。
        from agent.storage.repository import GoalRepository, MessageRepository, PlanRepository

        self._settings = settings
        self._goals = goals if goals is not None else GoalRepository()
        self._messages = messages if messages is not None else MessageRepository()
        self._plans = plans if plans is not None else PlanRepository()
        # 经理也推迟到第一次规划时才建：构造 `Manager` 会去建工具注册表、
        # 解析端点，而**起草和确认根本用不上它**。让 `Dialogue` 在没有模型的
        # 环境里也能完整跑完 C-1~C-3，是这一层能离线测的前提。
        self._manager = manager
        self._read_text = extractor if extractor is not None else _default_extract

    # ---- 用户开口 ----------------------------------------------------------

    async def start(self, text: str) -> Reply:
        """开一个目标。**用户开口那一刻建行**，状态恒为 `proposed`。"""
        open_goal = await self._goals.open_goal()
        if open_goal is not None:
            # 阶段 C 一次只跑一个（§8 第 3 条）。开着两个而又"只能有一个当前目标"，
            # 那个歧义迟早会以"改了 A 结果动的是 B"的形式冒出来。
            raise OpenGoalExists(
                f"已经有一个没结束的目标（{open_goal.id}，状态 {open_goal.status}）。"
                "阶段 C 一次只跑一个 —— 先把它结束掉（abort）再开新的。"
            )
        parsed = self._read_text(text)
        draft = GoalDraft(raw_text=text)
        draft.revise(parsed.values)
        row = await self._goals.create(raw_text=text, draft=draft, parsed=_as_record(parsed))
        await self._messages.append(goal_id=row.id, role=ROLE_USER, content=text)
        return await self._reply(row, draft, parsed, changed=sorted(parsed.values))

    async def say(self, goal_id: str, text: str) -> Reply:
        """用户又说了一句。**这条路永远不会激活目标**（ADR-037）。"""
        row = await self._require(goal_id)
        # 先记用户那句话：无论我们照不照做，他说了就是说了。
        await self._messages.append(goal_id=goal_id, role=ROLE_USER, content=text)
        parsed = self._read_text(text)

        if row.status != GoalStatus.PROPOSED.value:
            refusal = self._refuse(row.status)
            await self._messages.append(
                goal_id=goal_id, role=ROLE_ASSISTANT, content=refusal
            )
            return Reply(
                goal_id=goal_id,
                status=GoalStatus(row.status),
                text=refusal,
                draft=GoalDraft.from_row(row),
                unclear=list(parsed.unclear),
            )

        draft = GoalDraft.from_row(row)
        changed = draft.revise(parsed.values)
        row = await self._goals.save_draft(goal_id, draft)
        return await self._reply(row, draft, parsed, changed=changed)

    async def resume(self, goal_id: str) -> Reply:
        """把当前目标重念一遍，**不读任何新话**。中断之后接上下文用这个。"""
        row = await self._require(goal_id)
        draft = GoalDraft.from_row(row)
        return await self._reply(row, draft, None, changed=[])

    # ---- 确认（独立动作）----------------------------------------------------

    async def confirm(
        self, goal_id: str, *, equity: float, equity_captured_at: str | None = None
    ) -> Reply:
        """确认 → `active`。**这是唯一能让目标生效的入口**（ADR-037）。

        `equity` 必填且没有默认值：它是收益率的分母（ADR-033），
        给个默认值就等于让"拿什么当基准"这件事从代码里看不见了。

        `equity_captured_at` 是那个数的来源时刻（HTTP 载体用的是交易所快照的
        `captured_at`）。**有就念出来** —— 用户有权知道这个基准有多旧，
        而"激活这一刻读的"这种话只在调用方真的刚刚读过时才成立。
        """
        row = await self._require(goal_id)
        draft = GoalDraft.from_row(row)

        missing = draft.missing()
        if missing:
            # 这里自己先拦一道，是为了**回一句人话**（而不是把 `activate()` 的异常
            # 抛给用户看）。真正的那道闸门仍在 `activate()` 里，这里只是它的前端。
            text = (
                f"现在还不能确认 —— 还有没定下来的维度（{'、'.join(missing)}）。"
                "缺着就确认等于替你拍板，而那个数会烂在库里去掉不掉。"
            )
            await self._messages.append(goal_id=goal_id, role=ROLE_ASSISTANT, content=text)
            return Reply(
                goal_id=goal_id,
                status=GoalStatus(row.status),
                text=text,
                draft=draft,
            )

        row = await self._goals.activate(goal_id, equity=equity, settings=self._settings)
        text = self._locked_text(row, equity, captured_at=equity_captured_at)
        await self._messages.append(goal_id=goal_id, role=ROLE_ASSISTANT, content=text)
        return Reply(
            goal_id=goal_id,
            status=GoalStatus(row.status),
            text=text,
            draft=GoalDraft.from_row(row),
            activated=True,
        )

    # ---- 规划（独立动作，C-4）------------------------------------------------

    async def plan(self, goal_id: str) -> Reply:
        """让经理给这个目标做一份计划，并落库（C-4）。

        **和 `confirm()` 分开是有意的。** `confirm()` 是确定的闸门，必须瞬时、
        可离线；让它顺带调模型，模型一挂就会留下「目标已生效、计划没有」的半状态，
        而那条半状态既没法测、也没法解释。分开之后，确认照旧确定，
        规划失败只是一个**可以重试的动作**。

        喂给经理的是 `goal_brief()`：已确认的**生效值**，不是用户口述值。
        """
        row = await self._require(goal_id)

        if row.status != GoalStatus.ACTIVE.value:
            text = (
                f"这个目标现在是 {row.status}，还不能规划。"
                "经理拿到的必须是**确认之后冻住的生效值**（ADR-021 / ADR-036），"
                "目标没生效就没有那些值 —— 先走确认，把它变成 active。"
            )
            return await self._refuse_reply(row, text)

        if row.plan_id:
            text = (
                f"这个目标已经有计划了（plans.id = {row.plan_id}），不重规划。"
                "计划一旦挂上就是执行依据，悄悄换一份等于把「当时为什么这么做」"
                "的答案改掉（ADR-019 R1）。真要重来，先中止这个目标、重新提一个。"
            )
            return await self._refuse_reply(row, text)

        attempt_errors: list[str] = []
        try:
            run = await self._manager_or_build().plan(goal_brief(row))
        except Exception as exc:  # noqa: BLE001 — 规划失败也得留一行，见下
            # `Manager.plan()` 自己会把传输失败吞进 `run.errors` 返回；走到这里的
            # 是那之外坏掉的（端点没配、工具注册表起不来、我们自己的 bug）。
            # **照样落一行** —— "这次尝试发生过"本身就是要能查到的事实。
            run = None
            attempt_errors.append(f"规划过程抛了异常（{type(exc).__name__}）：{exc}")

        if run is None:
            attempt = await self._plans.create(
                goal_id=goal_id,
                plan=None,
                plan_shape="none",
                final_text="",
                transcript=[],
                invocations=[],
                shapes=[],
                usage=None,
                errors=attempt_errors,
                rounds=0,
            )
            attached = False
        else:
            attempt = await self._plans.create(
                goal_id=goal_id,
                # `Plan` 是 pydantic 模型，直接序列化 —— 不在这里手工拼 dict，
                # 手工拼就会和 `parse_plan()` 认得的那份形状各走各的。
                plan=run.plan.model_dump_json() if run.plan is not None else None,
                plan_shape=run.plan_shape,
                final_text=run.final_text,
                transcript=run.transcript,
                invocations=[asdict(invocation) for invocation in run.invocations],
                shapes=run.shapes,
                usage=run.usage,
                errors=run.errors,
                rounds=run.rounds,
            )
            attached = run.plan is not None
            if attached:
                # **只有解出来了才挂。** 拿最后一段散文顶上去，会让「有 plan_id」
                # 看起来像「有计划」，而阶段 D 会照着它去执行。
                row = await self._goals.attach_plan(goal_id, attempt.id)

        text = self._plan_text(attempt, attached=attached)
        await self._messages.append(goal_id=goal_id, role=ROLE_ASSISTANT, content=text)
        return Reply(
            goal_id=goal_id,
            status=GoalStatus(row.status),
            text=text,
            draft=GoalDraft.from_row(row),
            # 这一次尝试的行 —— **成功失败都一样给**。调用方要看"目标有没有计划"
            # 得看 `status` 之外的 `goal.plan_id`，别拿这个字段当那个用。
            plan_id=attempt.id,
        )

    # ---- 内部 --------------------------------------------------------------

    def _manager_or_build(self) -> Manager:
        if self._manager is None:
            from agent.manager.loop import Manager as _Manager

            self._manager = _Manager(settings=self._settings)
        return self._manager

    async def _refuse_reply(self, row: GoalLog, text: str) -> Reply:
        """拒绝一个动作，但**话照记**（照 C-3 的规矩）。"""
        await self._messages.append(goal_id=row.id, role=ROLE_ASSISTANT, content=text)
        return Reply(
            goal_id=row.id,
            status=GoalStatus(row.status),
            text=text,
            draft=GoalDraft.from_row(row),
        )

    @staticmethod
    def _plan_text(attempt: PlanLog, *, attached: bool) -> str:
        """念计划。**念的是库里那一行**，不是内存里的 `run` —— 这样用户看到的
        就是事后翻记录时看到的同一份东西。"""
        errors = json.loads(attempt.errors or "[]")

        if not attached:
            lines = ["经理没有给出能用的计划。"]
            if errors:
                lines.append("")
                lines.extend(f"  · {err}" for err in errors)
            lines += [
                "",
                f"这次尝试记下来了（plans.id = {attempt.id}，形态 {attempt.plan_shape}），"
                "但**没有挂到目标上** —— 目标仍然没有计划。"
                "可以再试一次：没解出计划不占用「已有计划」那个位置。",
            ]
            return "\n".join(lines)

        plan = json.loads(attempt.plan or "{}")
        lines = [f"计划出来了，已挂到目标上（plans.id = {attempt.id}）。", ""]
        lines.append(f"  目标  {plan.get('objective', '')}")
        lines.append("")
        for number, step in enumerate(plan.get("steps") or [], 1):
            lines.append(f"  {number}. {step.get('what', '')}")
            if step.get("why"):
                lines.append(f"     依据：{step['why']}")
            if step.get("tool"):
                lines.append(f"     工具：{step['tool']}")
        for key, title in (
            ("stop_conditions", "什么时候停"),
            ("assumptions", "它假设了什么"),
            ("risks", "它自己说的风险"),
        ):
            items = plan.get(key) or []
            if items:
                lines.append("")
                lines.append(f"  {title}：")
                lines.extend(f"    · {item}" for item in items)
        if errors:
            # 成功也可能带着前几轮的错误（比如第一轮没调工具被催了一次）。
            # **别把它们藏起来**：计划是从那几轮之后长出来的。
            lines.append("")
            lines.append("  过程中有几处值得看一眼：")
            lines.extend(f"    · {err}" for err in errors)
        lines += [
            "",
            "这只是做法，不下单 —— 阶段 C 不执行，链尾还在阶段 D。",
        ]
        return "\n".join(lines)

    async def _require(self, goal_id: str) -> GoalLog:
        row = await self._goals.get(goal_id)
        if row is None:
            raise GoalNotFound(f"没有这个目标：{goal_id}")
        return row

    async def _reply(
        self,
        row: GoalLog,
        draft: GoalDraft,
        parsed: Extract | None,
        *,
        changed: list[str],
    ) -> Reply:
        text = retell(draft, settings=self._settings, extract=parsed)
        if parsed is not None and not changed:
            # 这一句什么都没改到。**得说出来**，否则用户要么以为我们瞎了，
            # 要么（更要紧）以为刚才那句「对」已经算确认了 ——
            # 那正是 ADR-037 点名要防的代价。
            #
            # 故意**不去猜他这句是不是确认**：判"像确认"就是回到自由文本认关键词，
            # 而这条路是被否掉的。这里只陈述事实（一句都没改到），把动作指出来。
            text = (
                "（这一句我没有读出任何可以改的维度 —— 如果你想确认，"
                "请走确认那个动作：正文里说「对」我不当作确认，ADR-037。）\n\n" + text
            )
        await self._messages.append(goal_id=row.id, role=ROLE_ASSISTANT, content=text)
        return Reply(
            goal_id=row.id,
            status=GoalStatus(row.status),
            text=text,
            draft=draft,
            changed=changed,
            unclear=list(parsed.unclear) if parsed is not None else [],
        )

    @staticmethod
    def _refuse(status: str) -> str:
        if status == GoalStatus.ACTIVE.value:
            return (
                "这个目标已经确认并生效了，改不了 —— 风控参数在确认那一刻就冻住了"
                "（ADR-021）。要改得先中止它（abort），再重新提一个。"
            )
        return f"这个目标已经结束了（{status}），改不了。要接着做就重新提一个。"

    def _locked_text(
        self, row: GoalLog, equity: float, *, captured_at: str | None = None
    ) -> str:
        """锁上之后念什么。**念的是冻结下来的那几个数，不是配置里现在是什么。**

        这里读 `row.risk_snapshot` 而不是 `self._settings` —— 两者在激活之后就可能
        不一样了（有人改了 `.env`），而用户要确认的是**冻住的那一份**。
        """
        frozen = json.loads(row.risk_snapshot or "{}")
        source = f"交易所快照 {captured_at}，ADR-033" if captured_at else "ADR-033"
        lines = [
            "好，目标锁成 active 了。",
            "",
            f"  基准权益  {equity:g} USDT（{source}）",
            f"  截止时间  {row.deadline_at}",
        ]
        if "max_drawdown_pct" in frozen:
            lines.append(f"  生效的回撤线  {frozen['max_drawdown_pct'] * 100:g}%")
        if "daily_loss_limit" in frozen:
            lines.append(f"  每日亏损上限  {frozen['daily_loss_limit']:g} USDT")
        if "max_trade_amount" in frozen:
            lines.append(f"  单笔上限  {frozen['max_trade_amount']:g} USDT")
        lines += [
            "",
            "执行期间这些参数只读（ADR-021）。要改，得先中止这个目标重新提。",
        ]
        return "\n".join(lines)
