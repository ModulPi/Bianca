"""计划任务描述符测试（ADR-017 缺口 2：进程守护）。

这里测的是"守护会不会悄悄失效"。几个默认值单独拎出来钉住，因为它们错了不会报错，
只会让采集在某个时刻无声停止 —— 那是无人值守最危险的失败模式。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from agent.market.task import MarketTaskSpec

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def _spec(**over) -> MarketTaskSpec:
    base = dict(
        python_exe=r"D:\py\python.exe",
        repo_root=r"D:\repo\Bianca",
        user_id=r"LI\dev",
        log_file=r"D:\repo\Bianca\logs\market-collector.log",
    )
    base.update(over)
    return MarketTaskSpec(**base)  # type: ignore[arg-type]


def _text(xml: str, path: str) -> str:
    root = ET.fromstring(xml)
    node = root.find(path, NS)
    assert node is not None, f"XML 缺少 {path}"
    return (node.text or "").strip()


# ------------------------------------------------------------------ 会静默失效的默认值


def test_execution_time_limit_is_unlimited():
    """计划任务默认 72 小时强制结束 —— 不关掉，采集器第三天会被系统静默杀掉。

    而且被杀算"正常结束"不算失败，所以 RestartOnFailure 也不会救它：从此再不复返。
    """
    assert _text(_spec().to_xml(), "t:Settings/t:ExecutionTimeLimit") == "PT0S"


def test_battery_settings_do_not_stop_collection():
    """笔记本默认「用电池时不启动、改用电池时停止」—— 不关掉，拔电源就断流。"""
    xml = _spec().to_xml()
    assert _text(xml, "t:Settings/t:DisallowStartIfOnBatteries") == "false"
    assert _text(xml, "t:Settings/t:StopIfGoingOnBatteries") == "false"


def test_second_instance_is_ignored():
    """计划任务层面的防重，与采集器自己的单实例锁是两道独立的闸。"""
    assert _text(_spec().to_xml(), "t:Settings/t:MultipleInstancesPolicy") == "IgnoreNew"


def test_supervision_uses_repetition_not_restart_on_failure():
    """恢复机制是「重复唤醒 + IgnoreNew」，不是 RestartOnFailure。

    RestartOnFailure 实测在本机不生效：注册后该项保留在任务定义里，但手动启动和
    真触发器启动都以退出码 1 失败后无任何重试（间隔设 1 分钟，观察 3.5 / 6 分钟）。
    所以这里断言它**不在**描述符里 —— 免得有人以为它在兜底。
    """
    xml = _spec().to_xml()
    assert ET.fromstring(xml).find("t:Settings/t:RestartOnFailure", NS) is None
    assert _text(xml, "t:Triggers/t:TimeTrigger/t:Repetition/t:Interval") == "PT5M"


def test_repetition_hangs_off_the_time_trigger_not_the_logon_trigger():
    """实测教训：`<Repetition>` 挂在 LogonTrigger 上不生效。

    schtasks 照单全收、导出后也还在，但调度器根本不安排下一次运行（`下次运行时间`
    恒为 N/A，观察 3 分钟零触发，补 Duration 也一样），且没有任何报错 —— 一个静默
    失效的守护。挂到 TimeTrigger 上才真的反复触发。这条断言钉住这个分工。
    """
    root = ET.fromstring(_spec().to_xml())
    logon = root.find("t:Triggers/t:LogonTrigger", NS)
    assert logon is not None
    assert logon.find("t:Repetition", NS) is None, "LogonTrigger 上挂 Repetition 是无效的"

    time_trigger = root.find("t:Triggers/t:TimeTrigger", NS)
    assert time_trigger is not None and time_trigger.find("t:Repetition", NS) is not None


def test_time_trigger_has_a_concrete_start_boundary():
    """心跳锚点必须是具体时刻，缺了它 TimeTrigger 不合法。"""
    xml = _spec(start_boundary="2026-09-24T00:30:00").to_xml()
    assert _text(xml, "t:Triggers/t:TimeTrigger/t:StartBoundary") == "2026-09-24T00:30:00"


def test_supervise_interval_is_configurable():
    assert _text(_spec(supervise_interval_min=1).to_xml(), "t:Triggers/t:TimeTrigger/t:Repetition/t:Interval") == "PT1M"


def test_zero_supervise_interval_is_rejected():
    """0 会让重复间隔非法 —— 要么注册失败，要么退化成不重复，两种都等于没有守护。"""
    with pytest.raises(ValueError):
        _spec(supervise_interval_min=0)


def test_missed_trigger_runs_later():
    """关机期间错过的触发，开机后要补跑。"""
    assert _text(_spec().to_xml(), "t:Settings/t:StartWhenAvailable") == "true"


# ------------------------------------------------------------------ 启动动作


def test_action_runs_the_module_in_the_repo_root():
    """工作目录必须是仓库根：settings 用相对路径读 .env，读不到就没代理、连不上币安。"""
    xml = _spec().to_xml()
    assert _text(xml, "t:Actions/t:Exec/t:Command") == r"D:\py\python.exe"
    assert _text(xml, "t:Actions/t:Exec/t:WorkingDirectory") == r"D:\repo\Bianca"

    args = _text(xml, "t:Actions/t:Exec/t:Arguments")
    assert "-m agent.market" in args
    assert "-u" in args  # 无缓冲，守护进程日志才能实时看到
    assert "market-collector.log" in args


def test_log_file_path_is_quoted():
    """日志路径带空格时必须带引号，否则被切成两个参数、日志静默丢失。"""
    spec = _spec(log_file=r"D:\my data\market.log")
    assert '"D:\\my data\\market.log"' in spec.arguments


def test_status_interval_is_passed_through():
    assert "--status-interval 60" in _spec(status_interval_s=60).arguments
    assert "--status-interval 0" in _spec(status_interval_s=0).arguments


def test_logon_trigger_is_always_present():
    assert _text(_spec().to_xml(), "t:Triggers/t:LogonTrigger/t:Enabled") == "true"


def test_boot_trigger_is_off_by_default():
    """实测：带 BootTrigger 的任务在未提权会话里注册直接被拒（"拒绝访问"）。

    而它对交互式令牌本来就无用 —— 登录前跑不起来，加了只会让注册失败。
    所以默认不生成；真需要时配合存储凭据一起打开。
    """
    assert ET.fromstring(_spec().to_xml()).find("t:Triggers/t:BootTrigger", NS) is None


def test_boot_trigger_can_be_enabled():
    xml = _spec(boot_trigger=True, boot_delay_min=3).to_xml()
    assert _text(xml, "t:Triggers/t:BootTrigger/t:Enabled") == "true"
    assert _text(xml, "t:Triggers/t:BootTrigger/t:Delay") == "PT3M"


# ------------------------------------------------------------------ 输入校验


@pytest.mark.parametrize("bad", ["", "a\\b", "a/b"])
def test_task_name_with_path_separator_is_rejected(bad):
    """带分隔符的名字会被当成文件夹层级，任务被静默建到别处去。"""
    with pytest.raises(ValueError):
        _spec(name=bad)


@pytest.mark.parametrize("kwargs", [{"status_interval_s": -1}, {"supervise_interval_min": -1}])
def test_negative_numbers_are_rejected(kwargs):
    with pytest.raises(ValueError):
        _spec(**kwargs)


def test_ampersand_in_path_does_not_break_the_xml():
    """路径里有 & 会让整个 XML 非法，而 schtasks 只会说"XML 格式错误"，不说是哪个字符。"""
    spec = _spec(repo_root=r"D:\a & b\Bianca")
    root = ET.fromstring(spec.to_xml())  # 能解析就说明转义对了
    node = root.find("t:Actions/t:Exec/t:WorkingDirectory", NS)
    assert node is not None
    assert node.text == r"D:\a & b\Bianca"


def test_generated_xml_is_well_formed():
    ET.fromstring(_spec().to_xml())
