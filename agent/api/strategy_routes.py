from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from agent.api.schemas import (
    MessageResponse,
    StrategyCreateRequest,
    StrategyItem,
    StrategyListResponse,
    StrategyTickResponse,
    StrategyUpdateRequest,
)
from agent.storage.json_utils import parse_json_field
from agent.storage.repository import StrategyRepository
from agent.strategy.engine import default_params, run_strategy_tick
from agent.strategy.runner import get_strategy_runner

router = APIRouter(prefix="/strategies")


def _to_item(row) -> StrategyItem:
    return StrategyItem(
        id=row.id,
        name=row.name,
        type=row.type,
        market=row.market,
        execution_mode=row.execution_mode,
        params=parse_json_field(row.params_json),
        state=parse_json_field(row.state_json),
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
        started_at=row.started_at,
        stopped_at=row.stopped_at,
    )


@router.get("", response_model=StrategyListResponse)
async def list_strategies(limit: int = 50) -> StrategyListResponse:
    repo = StrategyRepository()
    rows = await repo.list_all(limit=limit)
    return StrategyListResponse(items=[_to_item(r) for r in rows], total=len(rows))


@router.post("", response_model=StrategyItem)
async def create_strategy(body: StrategyCreateRequest) -> StrategyItem:
    if body.type not in {"grid", "dca", "trend"}:
        raise HTTPException(status_code=422, detail="type must be grid, dca, or trend")
    params = body.params or default_params(body.type)
    repo = StrategyRepository()
    row = await repo.create(
        name=body.name,
        strategy_type=body.type,
        execution_mode=body.execution_mode,
        params=params,
        market=body.market,
    )
    return _to_item(row)


@router.get("/{strategy_id}", response_model=StrategyItem)
async def get_strategy(strategy_id: str) -> StrategyItem:
    repo = StrategyRepository()
    row = await repo.get_by_id(strategy_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return _to_item(row)


@router.patch("/{strategy_id}", response_model=StrategyItem)
async def update_strategy(strategy_id: str, body: StrategyUpdateRequest) -> StrategyItem:
    repo = StrategyRepository()
    row = await repo.update(
        strategy_id,
        name=body.name,
        params=body.params,
        execution_mode=body.execution_mode,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return _to_item(row)


@router.post("/{strategy_id}/start", response_model=StrategyItem)
async def start_strategy(strategy_id: str) -> StrategyItem:
    from datetime import UTC, datetime

    repo = StrategyRepository()
    row = await repo.update(
        strategy_id,
        status="running",
        started_at=datetime.now(UTC).isoformat(),
        stopped_at=None,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    await get_strategy_runner().start()
    return _to_item(row)


@router.post("/{strategy_id}/stop", response_model=StrategyItem)
async def stop_strategy(strategy_id: str) -> StrategyItem:
    from datetime import UTC, datetime

    repo = StrategyRepository()
    row = await repo.update(
        strategy_id,
        status="stopped",
        stopped_at=datetime.now(UTC).isoformat(),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    running = await repo.list_running()
    if not running:
        await get_strategy_runner().stop()
    return _to_item(row)


@router.post("/{strategy_id}/tick", response_model=StrategyTickResponse)
async def tick_strategy(strategy_id: str) -> StrategyTickResponse:
    try:
        result = await run_strategy_tick(strategy_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return StrategyTickResponse(**result)
