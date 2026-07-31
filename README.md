# Bianca

面向 C 端用户的加密货币自动交易 Agent 平台，基于币安（Binance）交易所，覆盖现货、U 本位合约、币本位合约全品种。

## 产品定位

Bianca（FnAgent）帮助用户通过 **策略模板 + AI 分析建议 + 信号跟单** 的混合决策模式实现数字资产管理，支持全自动或半自动执行，降低普通用户的交易门槛。

## 核心特性

- **零门槛自动化** — 无需编程，配置策略参数即可运行
- **AI 辅助决策** — 默认 Ollama 本地 LLM，也可切换云端模型
- **多层风控** — 策略 → 风控 → 执行三层分离，支持模拟交易先行验证
- **全品种覆盖** — 现货 + U 本位合约 + 币本位合约统一管理
- **透明可审计** — 每笔交易的决策过程可回溯
- **开源自部署** — Docker Compose 一键部署，数据完全自控

## 技术栈

| 层级 | 技术 |
|------|------|
| Agent 编排 | LangGraph（Supervisor 模式） |
| 后端 | Python |
| 前端 | TypeScript / React |
| 数据库 | PostgreSQL |
| LLM | Ollama（本地优先） |
| 交易所 | 币安 REST + WebSocket |

## 文档目录

```
docs/
├── PRD-FnAgent.md                          # 产品需求文档
├── 用户故事-FnAgent.md                      # 用户故事集
├── system-design/                          # 系统设计
│   ├── 系统设计文档-FnAgent.md
│   ├── 容量规划报告-FnAgent.md
│   └── 技术选型建议-FnAgent.md
├── outline-design/                         # 概要设计
│   ├── 架构设计/架构设计文档-FnAgent.md
│   └── 数据库设计/
│       ├── 数据库设计文档-FnAgent.md
│       ├── 数据字典.md
│       └── sql/001_init.sql
└── module-scheduling/                      # 开发排期
    ├── 开发排期计划-FnAgent.md
    ├── 里程碑清单-FnAgent.md
    ├── 甘特图描述-FnAgent.md
    └── 资源分配建议-FnAgent.md
```

## 开发阶段

当前处于 **概念验证（PoC）** 阶段，需求与设计文档已完成，代码开发待启动。

## 许可证

MIT
