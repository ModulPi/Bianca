from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agent.api.schemas import ApiKeyCreateRequest, ApiKeyItem, ApiKeyListResponse, MessageResponse, SecretsReloadResponse
from agent.security.crypto import encrypt_secret
from agent.security.secrets_loader import refresh_effective_settings
from agent.storage.repository import ApiKeyRepository

router = APIRouter(prefix="/secrets", tags=["secrets"])

_ALLOWED_TYPES = {"binance", "llm", "telegram", "custom"}

_FORMAT_HINTS = {
    "binance": "API_KEY:API_SECRET",
    "llm": "sk-...",
    "telegram": "BOT_TOKEN:CHAT_ID",
}


def _mask(value: str) -> str:
    if len(value) <= 4:
        return "****"
    return f"{value[:2]}…{value[-2:]}"


@router.get("/keys", response_model=ApiKeyListResponse)
async def list_api_keys() -> ApiKeyListResponse:
    rows = await ApiKeyRepository().list_all()
    items = [
        ApiKeyItem(
            id=row.id,
            name=row.name,
            key_type=row.key_type,
            masked_value="****",
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]
    return ApiKeyListResponse(items=items, total=len(items))


@router.post("/keys", response_model=ApiKeyItem)
async def create_api_key(body: ApiKeyCreateRequest) -> ApiKeyItem:
    if body.key_type not in _ALLOWED_TYPES:
        raise HTTPException(status_code=422, detail=f"key_type must be one of {sorted(_ALLOWED_TYPES)}")
    try:
        encrypted = encrypt_secret(body.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = await ApiKeyRepository().create(
        name=body.name,
        key_type=body.key_type,
        encrypted_value=encrypted,
    )
    await refresh_effective_settings()
    return ApiKeyItem(
        id=row.id,
        name=row.name,
        key_type=row.key_type,
        masked_value=_mask(body.value),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.delete("/keys/{key_id}", response_model=MessageResponse)
async def delete_api_key(key_id: str) -> MessageResponse:
    ok = await ApiKeyRepository().delete(key_id)
    if not ok:
        raise HTTPException(status_code=404, detail="API key not found")
    await refresh_effective_settings()
    return MessageResponse(message=f"Deleted key {key_id}")


@router.post("/reload", response_model=SecretsReloadResponse)
async def reload_secrets() -> SecretsReloadResponse:
    effective = await refresh_effective_settings()
    return SecretsReloadResponse(
        message="Runtime secrets reloaded",
        binance_configured=effective.binance_configured,
        llm_configured=effective.llm_configured,
        telegram_configured=effective.telegram_configured,
    )


@router.get("/formats")
async def secret_formats() -> dict[str, str]:
    return _FORMAT_HINTS
