# Bianca 代码布局（前后端 + 四层）

> 重构日期：2026-08-26

## 顶层

| 目录 | 职责 |
|------|------|
| `frontend/` | React 运维看板（原 `web/`） |
| `backend/` | Python API / Agent（原 `agent/`） |
| `tests/` `scripts/` `deploy/` `docs/` `data/` | 测试、脚本、部署、文档、运行时数据 |

## backend 四层

```
backend/
├── interfaces/        # 接口层：api、security
├── application/       # 应用编排：runner、graph、confirmation、dashboard、summary、validation
├── domain/            # 领域：strategy、factors、risk、llm、markets、trading、positions
├── infrastructure/    # 基建：storage、cache、exchange、checkpoint、market、notify
├── main.py
└── config.py
```

依赖方向：`interfaces → application → domain → infrastructure`（禁止反向）。

Agent 主体：`application/runner.py` + `application/graph/`  
因子预留：`domain/factors/`
