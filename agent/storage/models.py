from sqlalchemy import Float, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TradeLog(Base):
    __tablename__ = "trade_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    side: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    order_type: Mapped[str | None] = mapped_column(String, nullable=True)
    llm_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision_reason: Mapped[str] = mapped_column(Text, nullable=False)
    risk_decision: Mapped[str | None] = mapped_column(String, nullable=True)
    risk_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    decision_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    # ---- 可归因性（ADR-019 / ADR-031）----
    # 决策时刻的**完整输入**。LLM 驱动的交易是行情上下文，强制平仓是风控输入
    # （持仓 + 高点 + 回撤）—— 后者没有 LLM，但同样得答得出"当时依据了什么"。
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    # R3：决策时刻手里有什么。**刻意留一份副本而不是只存 captured_at 去查快照表** ——
    # 快照是每 tick 一张的流水，可能被轮转清理；"这一笔依据的是哪个状态"必须钉在这一笔上。
    equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    positions: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 留位：目标与计划是阶段 C 的实体，现在没有。可空，届时回填，不阻塞现在。
    goal_id: Mapped[str | None] = mapped_column(String, nullable=True)
    plan_id: Mapped[str | None] = mapped_column(String, nullable=True)


Index("idx_trade_logs_created", TradeLog.created_at.desc())
Index("idx_trade_logs_side_status", TradeLog.side, TradeLog.status)


class RiskEvent(Base):
    __tablename__ = "risk_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    related_trade_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


Index("idx_risk_events_time", RiskEvent.created_at.desc())


class DecisionLog(Base):
    __tablename__ = "decision_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    model_used: Mapped[str] = mapped_column(String, nullable=False)
    # 人类看一眼的一行摘要。**不是**归因依据 —— 依据是下面那两列。
    prompt_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_output: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_signal: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    # ---- 可归因性（ADR-019 / ADR-031）----
    # **实际发出去**的完整 messages。只存 raw_output 的话，"模型是看着什么说的"
    # 就永远查不到 —— 而那是全自动模式下唯一的查错处。
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 用的是哪一份 prompt（normal / brevity）。重试那次是被催着简短作答的，
    # 回答质量与首次不同 —— 光看 raw_output 判断不出来，得单独记。
    prompt_variant: Mapped[str | None] = mapped_column(String, nullable=True)
    market_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    goal_id: Mapped[str | None] = mapped_column(String, nullable=True)
    plan_id: Mapped[str | None] = mapped_column(String, nullable=True)


class AgentConfigRow(Base):
    __tablename__ = "agent_config"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class OrderLog(Base):
    """订单账本：交易所实际收到了什么、回了什么。

    与 `trade_logs` 分开的理由：trade_logs 记的是**决策**，可能根本没有订单
    （HOLD、被风控拦下）。订单回执才是对账与事后归因（ADR-019 R1/R3）的唯一依据。

    `client_order_id` 是幂等键 —— 由决策 id 派生而非随机，交易所据此去重。
    唯一约束是硬保证：同一决策不可能在库里留下两笔单。
    """

    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    client_order_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    trade_id: Mapped[str | None] = mapped_column(String, nullable=True)
    decision_id: Mapped[str | None] = mapped_column(String, nullable=True)

    symbol: Mapped[str] = mapped_column(String, nullable=False)
    side: Mapped[str] = mapped_column(String, nullable=False)
    order_type: Mapped[str] = mapped_column(String, nullable=False)

    # amount 的口径必须与数值一起落库：BUY 是计价币名义（USDT），SELL 是基础币数量。
    # 现状里这个口径只活在 prompt 的一句话里，落库后无从分辨 —— 归因时
    # "amount=25 到底是 25 USDT 还是 25 BTC" 是个致命的歧义。
    amount_kind: Mapped[str] = mapped_column(String, nullable=False)
    requested_amount: Mapped[float] = mapped_column(Float, nullable=False)

    status: Mapped[str] = mapped_column(String, nullable=False)
    exchange_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    filled_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_quote: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee: Mapped[str | None] = mapped_column(Text, nullable=True)

    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


Index("idx_orders_created", OrderLog.created_at.desc())
Index("idx_orders_trade", OrderLog.trade_id)


class EquitySnapshotLog(Base):
    """某一时刻的账户权益（阶段 A-2）。

    为什么要落库而不是每次现算：日亏熔断需要**当天开始时的权益**做基准，
    而"开始时"是个已经过去的时刻 —— 不落库就只能拿现金流近似（旧实现正是如此，
    它把买入当成亏损）。有了这张表，日盈亏才是"现在的权益减今天的第一个快照"。

    持仓与账本的差异（账外持仓）一并存 JSON：事后归因时要知道，
    当时的那个权益数字背后有没有"不属于我们的钱"。
    """

    __tablename__ = "equity_snapshots"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    captured_at: Mapped[str] = mapped_column(String, nullable=False)

    cash_quote: Mapped[float] = mapped_column(Float, nullable=False)
    holdings_value: Mapped[float] = mapped_column(Float, nullable=False)
    equity: Mapped[float] = mapped_column(Float, nullable=False)

    # 取不到价的持仓 symbol。非空意味着 equity 只是下界，不是完整值。
    unpriced: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    positions: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    gaps: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="[]")


Index("idx_equity_captured", EquitySnapshotLog.captured_at.desc())


class GoalLog(Base):
    """一个目标的完整生命周期（阶段 C-1，ADR-020 / 021 / 032 / 033）。

    **这张表同时也是「会话」。** §2.3 给会话的定义就是"一个目标的完整生命周期的
    载体"，所以没有单独的 `sessions` 表 —— 会话句柄就是 goal id。省掉的那张表
    不会带来任何表达力损失，多出来的只会是两张表之间的一次同步。

    最要紧的一条：**每个维度都可空，且空 = 用户没说。** ADR-020 要的是"自然语言
    为主"，那就得能如实记下"这句话里没提回撤上限"。补一个 5% 进去，后面所有
    "离目标还差多少"都建立在一个用户没同意过的数上，而事后看不出来是谁定的。
    解析层（C-2）之所以能"点名问缺哪个"，靠的也正是这里区分得出空与不空。
    """

    __tablename__ = "goals"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String, nullable=False)

    # 用户原话。**不许只存解析结果** —— ADR-019 R1 的链头是"目标"，而目标就是
    # 这句话。只留结构化的那份，事后没人能判断当时是解析错了还是用户说错了。
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    # 解析器给出的结构化结果。与下面那几列**分开存**：那几列是用户改过之后的定稿，
    # 这一份是机器第一次读出来的。两者不一致时，能看出是"解析错了"还是"用户改了"。
    parsed: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 经理的复述（§2.1 步骤②）。确认的是这句话，不是 `raw_text` —— 把关点在这。
    objective: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- §2.2 的维度。空 = 用户没说，**不是"用默认值"** ----
    symbols: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 用户直接说的**绝对**目标权益（「做到 1100」），与上面那个百分比口径并列留着。
    target_equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    horizon_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    autonomy: Mapped[str | None] = mapped_column(String, nullable=True)

    # ---- ADR-033：本金有两个口径，**两个都得看得见** ----
    # 口述的是"用户以为自己在拿多少钱试水"，实际权益是"账户里真有那么多"。
    # 收益率一律按后者算；两者长期不一致时，这个不一致本身是复盘时要看的东西。
    stated_capital: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_equity: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ---- ADR-021：风控参数在 `active` 那一刻固化，之后只读 ----
    risk_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 计划（C-4 填），指向 `plans.id`。**注意别把这句话说过头**：填上之后只有
    # 「目标 → 计划」这一段通了；`trade_logs` / `decision_logs` 的 goal_id/plan_id
    # 仍要等阶段 D 的执行循环去写 —— C 不下单，那两列在本阶段没有生产者。
    plan_id: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[str] = mapped_column(String, nullable=False)
    confirmed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    # 时间窗到期是**硬性终止条件**（§2.3，由代码判定），所以要有个钉死的时刻可比，
    # 而不是每次拿浮点小时数重算一遍。
    deadline_at: Mapped[str | None] = mapped_column(String, nullable=True)
    ended_at: Mapped[str | None] = mapped_column(String, nullable=True)
    end_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


Index("idx_goals_status", GoalLog.status)
Index("idx_goals_created", GoalLog.created_at.desc())


class MessageLog(Base):
    """会话消息。**"会话重启后上下文不丢"靠的就是它**（§6.5 验收第 4 条）。

    只存"说了什么"。这一轮该做什么、目标现在是什么状态，都是 `goals` 那一行的事 ——
    两边各存一份意图，迟早会不一致。
    """

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    goal_id: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


Index("idx_messages_goal", MessageLog.goal_id, MessageLog.created_at)


class PlanLog(Base):
    """一次规划尝试（C-4）。**成功失败都留一行。**

    为什么失败也留：R1 要的是"当时为什么这么做"，而"计划没出来"本身就是一个
    需要能解释的结果（模型没给、传输挂了、解析器不认）。只留成功的那些，
    事后看到的是"每次都有计划"，那是不真的。

    `plan` 为 `None` 就**真的是没解出来** —— 不拿最后一段散文顶上。
    一个挂上去的假计划比没有计划更糟：它会让"有 plan_id"看起来像"有计划"。
    """

    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    goal_id: Mapped[str] = mapped_column(String, nullable=False)

    # 解出来的计划（JSON）。None = 没解出来。
    plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 模型这一轮是怎么回的（json / text / none）。B 阶段靠这一栏才发现
    # deepseek 根本不填 `tools`（§6.4.3），规划阶段同样要看这个。
    plan_shape: Mapped[str] = mapped_column(String, nullable=False)
    final_text: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # R1 的「完整 prompt」：实际发出去的全部 messages（含 system 与工具结果）。
    transcript: Mapped[str] = mapped_column(Text, nullable=False)
    # 规划时调了哪些工具、拿到了什么 —— 计划也是"基于什么做出来的"。
    invocations: Mapped[str] = mapped_column(Text, nullable=False)
    shapes: Mapped[str] = mapped_column(Text, nullable=False)
    usage: Mapped[str | None] = mapped_column(Text, nullable=True)
    errors: Mapped[str] = mapped_column(Text, nullable=False)

    rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


Index("idx_plans_goal", PlanLog.goal_id, PlanLog.created_at.desc())

