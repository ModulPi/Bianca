"""数据面的 Windows 计划任务描述符（ADR-017 缺口 2：进程守护）。

数据面独立成进程后，谁来在崩溃后拉起它、谁来在重启后启动它？答案是操作系统的
进程守护。这里只负责**生成**任务描述符 —— 注册/卸载是 `scripts/install_market_task.py`
的事。分开的理由：XML 生成是纯函数，可以在任何平台上单测；注册要碰系统、要权限，
不该混进来。

几个设置项不是随手写的，每一个都对应一个会让"无人值守"悄悄失效的默认值：

- **恢复机制是「重复唤醒 + IgnoreNew」，不是 `RestartOnFailure`。** 任务每
  `supervise_interval_min` 分钟醒一次：采集器活着时，这次唤醒被 `IgnoreNew` 吞掉；
  采集器死了，这次唤醒就把它拉起来。它顺带覆盖了 `RestartOnFailure` 修不了的洞：
  任务被系统正常结束不算"失败"，不会触发重启，而重复唤醒照样能救回来。
- **重复必须挂在 `TimeTrigger` 上，挂在 `LogonTrigger` 上不生效（实测）。** 这是本文件
  里代价最大的一条教训：把 `<Repetition>` 放进 `<LogonTrigger>`，schtasks **照单全收、
  导出后也还在**，但调度器根本不安排下一次运行 —— `下次运行时间` 恒为 `N/A`，观察
  3 分钟零触发；补 `<Duration>` 也一样。没有报错，没有日志（计划任务操作日志默认关闭），
  只有一个静默失效的守护。改成 `TimeTrigger` + `StartBoundary` + `<Repetition>` 才真的
  反复触发（`下次运行时间` 有真实值，ticks 稳定递增）。所以现在两个触发器分工：
  `TimeTrigger` 负责心跳，`LogonTrigger` 只负责登录后立刻启动、不带重复。
- `RestartOnFailure` 实测在本机**也不生效**：注册后该项保留在任务定义里，但无论手动
  启动还是真触发器启动，动作以退出码 1 失败后都没有任何重试（间隔设 1 分钟，分别观察
  3.5 分钟和 6 分钟）。因此不使用它 —— 把"无人值守"押在一个验证不了的机制上不可接受。
- `ExecutionTimeLimit=PT0S` —— 计划任务**默认 72 小时强制结束**。一个要跑十年的
  采集器如果漏了这项，会在第三天无声无息地被系统杀掉。这是本文件里最要紧的一项。
- `StopIfGoingOnBatteries=false` + `DisallowStartIfOnBatteries=false` —— 笔记本
  默认"改用电池时停止"且"不启动"。不关掉，拔掉电源采集就停。
- `MultipleInstancesPolicy=IgnoreNew` —— 既是防重复，也是上面那个恢复机制的一半。
  它让"重复唤醒"这件事变得安全：醒着的那个实例不会被新唤醒替换掉。
- `Repetition/StopAtDurationEnd=false` —— 不写这项，重复结束时会顺手把任务停掉，
  也就是把正在跑的采集器杀掉。
- `LogonType=InteractiveToken` —— 只在该用户登录时运行（无需存密码）。代价：不登录
  就不采集。要真做到"开机即采集、无需登录"，需改用存储凭据，见安装脚本的说明。
- `StartWhenAvailable=true` —— 错过触发时间（比如关机期间）后，开机补跑。
- **默认不带 BootTrigger** —— 实测：带 `<BootTrigger>` 的任务在**未提权**的会话里注册
  会直接"拒绝访问"，去掉就能注册成功。而且它对 `InteractiveToken` 本来也无用：登录
  之前任务是跑不起来的，加了只是让注册失败。真需要开机即采集时得同时换用存储凭据，
  那时再由 `boot_trigger=True` 打开（安装脚本的 `--boot-trigger`）。

**已知局限（未解）:** 重复唤醒靠"任务还在不在"判断死活。如果采集器进程活着但**卡住**
（WS 静默停滞、不再落库），任务仍算在跑，唤醒会被 `IgnoreNew` 吞掉，没人救它。
要覆盖这种情形需要另一个独立心跳（比如拿库内 `max(time)` 的年龄做判据），不在本文件
范围内。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from xml.sax.saxutils import escape

# 任务描述符版本。用 1.2 是因为 RestartOnFailure / MultipleInstancesPolicy 等
# 需要它；1.0 的 schema 没有这些元素。
_TASK_VERSION = "1.2"
_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"


@dataclass(frozen=True)
class MarketTaskSpec:
    """一个数据面采集任务的全部参数。"""

    python_exe: str
    repo_root: str
    user_id: str
    log_file: str
    status_interval_s: int = 300
    name: str = "BiancaMarketDataPlane"
    description: str = "Bianca 行情数据面（独立于 API 进程；ADR-017）"
    supervise_interval_min: int = 5
    # 心跳触发器的锚点。必须是安装时刻的具体时间；之后调度器自行推算后续每次。
    start_boundary: str = field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    )
    boot_trigger: bool = False
    boot_delay_min: int = 1
    extra_args: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.name or any(c in self.name for c in "\\/"):
            # 任务名带路径分隔符会被解释成文件夹层级，静默创建到别处去
            raise ValueError(f"非法的任务名: {self.name!r}")
        if self.status_interval_s < 0:
            raise ValueError("status_interval_s 不能为负")
        if self.supervise_interval_min < 1:
            # 0 会让重复间隔非法 → 任务注册失败或退化成"不重复"，两种都等于没有守护
            raise ValueError("supervise_interval_min 必须 >= 1")

    @property
    def arguments(self) -> str:
        parts = [
            "-u",
            "-m",
            "agent.market",
            "--status-interval",
            str(self.status_interval_s),
            "--log-file",
            f'"{self.log_file}"',
            *self.extra_args,
        ]
        return " ".join(parts)

    @property
    def boot_trigger_xml(self) -> str:
        """BootTrigger 段。未提权时存在它就注册失败，所以默认不生成。"""
        if not self.boot_trigger:
            return ""
        return f"""
    <BootTrigger>
      <Enabled>true</Enabled>
      <Delay>PT{self.boot_delay_min}M</Delay>
    </BootTrigger>"""

    def to_xml(self) -> str:
        # 转义所有插值：路径里出现 & 会让整个 XML 非法，而计划任务的报错信息
        # 只说"XML 格式错误"，不会指出是哪个字符
        e = escape
        return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="{_TASK_VERSION}" xmlns="{_NS}">
  <RegistrationInfo>
    <Description>{e(self.description)}</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>{e(self.start_boundary)}</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT{self.supervise_interval_min}M</Interval>
      </Repetition>
    </TimeTrigger>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{e(self.user_id)}</UserId>
    </LogonTrigger>{self.boot_trigger_xml}
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{e(self.user_id)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <!-- PT0S = 不限时。默认值是 PT72H，会把长跑采集器在第三天静默杀掉 -->
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{e(self.python_exe)}</Command>
      <Arguments>{e(self.arguments)}</Arguments>
      <WorkingDirectory>{e(self.repo_root)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""
