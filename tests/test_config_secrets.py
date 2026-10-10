"""密钥不许从 `Settings` 的 `repr` / `str` 里漏出来（2026-10-11）。

**这不是假想的问题，是已经发生过一次。** 一条 pytest 断言失败时，
`assert ... == Settings(...)` 把 `.env` 里的真 Binance key/secret 和 LLM key
原样打进了输出 —— 因为那三个字段当时是裸 `str`，而 `Settings` 是会被传来传去的
对象：任何一条 `logger.info("%s", settings)`、任何一个断言消息、任何一次
调试打印，都会把它整份吐出来。

现在的做法是 `SecretStr`：要原文必须显式 `.get_secret_value()`，
于是"哪里读了密钥"在代码里看得见，顺手塞进日志也就没那么容易。
下面这几条钉的就是这件事 —— 谁哪天把它改回 `str`，这里会红。
"""

from __future__ import annotations

from agent.config import Settings

REAL_KEY = "sk-thisIsNotARealKey_butItLooksLikeOne_0123456789"
REAL_SECRET = "definitelyNotARealBinanceSecret_0123456789abcdef"


def _settings() -> Settings:
    """显式给值，不读 `.env` —— 这条测的是脱敏，不该依赖本机有没有配置文件。"""
    return Settings(
        binance_api_key=REAL_KEY,
        binance_api_secret=REAL_SECRET,
        llm_api_key=REAL_KEY,
    )


def test_repr_does_not_leak_any_of_the_three_secrets():
    """**这条是这次修复的核心。** `repr` 是泄漏的真实路径 ——
    它就是那个把密钥打进 pytest 输出的东西。"""
    text = repr(_settings())
    assert REAL_KEY not in text
    assert REAL_SECRET not in text
    assert "sk-thisIsNotARealKey" not in text


def test_stringifying_a_single_secret_masks_it():
    """顺手 `str(settings.binance_api_key)`（拼日志最常见的手法）也得是掩码。"""
    settings = _settings()
    assert str(settings.binance_api_key) == "**********"
    assert str(settings.llm_api_key) == "**********"
    assert REAL_SECRET not in str(settings.binance_api_secret)


def test_dumping_the_model_does_not_leak_either():
    """`model_dump()` 是另一个能整份带走配置的出口，一并钉住。"""
    settings = _settings()
    assert REAL_KEY not in str(settings.model_dump())
    assert REAL_SECRET not in str(settings.model_dump(mode="json"))


def test_the_real_value_is_still_reachable_on_purpose():
    """脱敏不能脱成"取不出来了" —— 显式要一次仍然拿得到。
    这条同时在钉**取原文必须写成 `.get_secret_value()`** 这件事本身。"""
    settings = _settings()
    assert settings.binance_api_key.get_secret_value() == REAL_KEY
    assert settings.binance_api_secret.get_secret_value() == REAL_SECRET


def test_configured_flags_still_see_through_the_wrapper():
    """`binance_configured` / `llm_configured` 读的是值不是包装 ——
    换成 SecretStr 时最容易在这里悄悄改成"永远为真"。"""
    assert _settings().binance_configured is True
    assert _settings().llm_configured is True

    empty = Settings(binance_api_key="", binance_api_secret="", llm_api_key="")
    assert empty.binance_configured is False, "空字符串不算配好了"
    assert empty.llm_configured is False


def test_a_whitespace_only_secret_does_not_count_as_configured():
    """`.strip()` 那一层不能丢：`.env` 里留个空格是很常见的手滑，
    而"配了个空格"与"没配"对下游是同一件事。"""
    settings = Settings(binance_api_key="   ", binance_api_secret="   ", llm_api_key="   ")
    assert settings.binance_configured is False
    assert settings.llm_configured is False
