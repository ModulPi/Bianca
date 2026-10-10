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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agent.config import Settings
from agent.dialogue.extract import Extract
from agent.dialogue.extract import extract as _default_extract
from agent.dialogue.goal import GoalDraft, GoalStatus
from agent.dialogue.retell import retell
from agent.storage.models import GoalLog

if TYPE_CHECKING:  # pragma: no cover
    from agent.storage.repository import GoalRepository, MessageRepository

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"


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
        extractor: Callable[[str], Extract] | None = None,
    ) -> None:
        # 仓库在这里**才**导入，不在模块顶层。`agent.storage.repository` 自己要
        # `from agent.dialogue.goal import ...`，那会先把 `agent.dialogue` 这个包
        # 跑一遍；包再拉 `session`，而 session 顶层又要 repository —— 那时它只导入
        # 到一半，`GoalRepository` 还没定义。放在这里，两个方向就错开了。
        #
        # 这两个类本来就只是**默认值**（构造函数收的就是注入），所以推迟导入
        # 没有改变任何设计。
        from agent.storage.repository import GoalRepository, MessageRepository

        self._settings = settings
        self._goals = goals if goals is not None else GoalRepository()
        self._messages = messages if messages is not None else MessageRepository()
        self._read_text = extractor if extractor is not None else _default_extract

    # ---- 用户开口 ----------------------------------------------------------

    async def start(self, text: str) -> Reply:
        """开一个目标。**用户开口那一刻建行**，状态恒为 `proposed`。"""
        open_goal = await self._goals.open_goal()
        if open_goal is not None:
            # 阶段 C 一次只跑一个（§8 第 3 条）。开着两个而又"只能有一个当前目标"，
            # 那个歧义迟早会以"改了 A 结果动的是 B"的形式冒出来。
            raise ValueError(
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

    async def confirm(self, goal_id: str, *, equity: float) -> Reply:
        """确认 → `active`。**这是唯一能让目标生效的入口**（ADR-037）。

        `equity` 必填且没有默认值：它是收益率的分母（ADR-033），
        给个默认值就等于让"拿什么当基准"这件事从代码里看不见了。
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
        text = self._locked_text(row, equity)
        await self._messages.append(goal_id=goal_id, role=ROLE_ASSISTANT, content=text)
        return Reply(
            goal_id=goal_id,
            status=GoalStatus(row.status),
            text=text,
            draft=GoalDraft.from_row(row),
            activated=True,
        )

    # ---- 内部 --------------------------------------------------------------

    async def _require(self, goal_id: str) -> GoalLog:
        row = await self._goals.get(goal_id)
        if row is None:
            raise ValueError(f"没有这个目标：{goal_id}")
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

    def _locked_text(self, row: GoalLog, equity: float) -> str:
        """锁上之后念什么。**念的是冻结下来的那几个数，不是配置里现在是什么。**

        这里读 `row.risk_snapshot` 而不是 `self._settings` —— 两者在激活之后就可能
        不一样了（有人改了 `.env`），而用户要确认的是**冻住的那一份**。
        """
        frozen = json.loads(row.risk_snapshot or "{}")
        lines = [
            "好，目标锁成 active 了。",
            "",
            f"  基准权益  {equity:g} USDT（激活这一刻读的，ADR-033）",
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
