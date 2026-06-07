"""
NCR SaaS API Server
===================
FastAPI + Supabase
Copyright (C) 2024 — M.A. — All Rights Reserved
Proprietary Software — Unauthorized use prohibited

يستقبل طلبات إعدادات NCR من أودو
يتحقق من صلاحية الـ API Key
يعالج البيانات ويرجع النص المُنسَّق
"""

from __future__ import annotations

import hashlib
import time
import os
from datetime import datetime
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
import uvicorn

# ================================================================
# الإعداد
# ================================================================
SUPABASE_URL         = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
API_RATE_LIMIT       = int(os.environ.get("RATE_LIMIT_PER_MIN", "60"))

SUPABASE_HEADERS = {
    "apikey":        SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}

BIND_LABELS = {
    "glue":   "Glue Binding",
    "staple": "Staple Binding",
    "spiral": "Spiral Binding",
}
SIDE_LABELS = {
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
}

# ================================================================
# FastAPI App
# ================================================================
app = FastAPI(
    title="NCR SaaS API",
    version="1.0.0",
    docs_url=None,      # نخفي الـ docs في production
    redoc_url=None,
    openapi_url=None,   # نخفي الـ schema
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # أودو يرسل من أي domain
    allow_methods=["POST", "GET"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)

security = HTTPBearer(auto_error=False)

# ================================================================
# Pydantic Models
# ================================================================
class PaperColor(BaseModel):
    layer: str
    color: str

class NcrConfigRequest(BaseModel):
    binding_option: Optional[str] = ""
    binding_side:   Optional[str] = ""
    serial_start:   Optional[str] = "00001"
    paper_colors:   Optional[list[PaperColor]] = []
    odoo_order_id:  Optional[int] = None
    odoo_line_id:   Optional[int] = None
    product_name:   Optional[str] = ""

class NcrConfigResponse(BaseModel):
    status:         str
    generated_text: str
    config_id:      Optional[str] = None
    tenant:         Optional[str] = None

# ================================================================
# Auth: التحقق من API Key
# ================================================================
def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def verify_api_key(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    """
    يتحقق من الـ API Key من:
    1. Authorization: Bearer ncr_xxx
    2. Header: X-API-Key: ncr_xxx
    يبحث عن الـ hash في Supabase ويتحقق من الاشتراك
    """
    start = time.time()

    # استخراج الـ key
    raw_key = None
    if credentials and credentials.credentials:
        raw_key = credentials.credentials
    if not raw_key:
        raw_key = request.headers.get("X-API-Key", "")
    if not raw_key:
        raise HTTPException(status_code=401, detail="API key required")

    if not raw_key.startswith("ncr_"):
        raise HTTPException(status_code=401, detail="Invalid API key format")

    key_hash = hash_key(raw_key)

    # البحث في Supabase
    async with httpx.AsyncClient() as client:
        # 1. تحقق من الـ key
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/api_keys",
            headers=SUPABASE_HEADERS,
            params={
                "key_hash": f"eq.{key_hash}",
                "is_active": "eq.true",
                "select": "id,tenant_id,expires_at",
            },
        )

        if resp.status_code != 200 or not resp.json():
            raise HTTPException(status_code=401, detail="Invalid or inactive API key")

        key_data = resp.json()[0]
        key_id   = key_data["id"]
        tenant_id = key_data["tenant_id"]

        # تحقق من تاريخ انتهاء الـ key
        if key_data.get("expires_at"):
            exp = datetime.fromisoformat(key_data["expires_at"].replace("Z", "+00:00"))
            if exp < datetime.now(exp.tzinfo):
                raise HTTPException(status_code=401, detail="API key expired")

        # 2. تحقق من المشترك واشتراكه
        resp2 = await client.get(
            f"{SUPABASE_URL}/rest/v1/tenants",
            headers=SUPABASE_HEADERS,
            params={
                "id":       f"eq.{tenant_id}",
                "is_active":"eq.true",
                "select":   "id,company_name,plan,plan_expires_at,max_requests",
            },
        )

        if resp2.status_code != 200 or not resp2.json():
            raise HTTPException(status_code=403, detail="Tenant not found or inactive")

        tenant = resp2.json()[0]

        # تحقق من انتهاء الاشتراك
        if tenant.get("plan_expires_at") and tenant["plan"] != "lifetime":
            exp_t = datetime.fromisoformat(tenant["plan_expires_at"].replace("Z", "+00:00"))
            if exp_t < datetime.now(exp_t.tzinfo):
                raise HTTPException(
                    status_code=402,
                    detail="Subscription expired. Please renew at artforprinting.ae/ncr-saas"
                )

        # 3. تحقق من عدد الطلبات (rate check بسيط على اليوم)
        resp3 = await client.get(
            f"{SUPABASE_URL}/rest/v1/request_logs",
            headers=SUPABASE_HEADERS,
            params={
                "tenant_id":  f"eq.{tenant_id}",
                "created_at": f"gte.{datetime.utcnow().strftime('%Y-%m-%d')}T00:00:00",
                "select":     "id",
            },
        )
        daily_count = len(resp3.json()) if resp3.status_code == 200 else 0

        if daily_count >= tenant["max_requests"]:
            raise HTTPException(
                status_code=429,
                detail=f"Daily request limit ({tenant['max_requests']}) reached"
            )

        # 4. تحديث last_used_at
        await client.patch(
            f"{SUPABASE_URL}/rest/v1/api_keys",
            headers=SUPABASE_HEADERS,
            params={"id": f"eq.{key_id}"},
            json={"last_used_at": datetime.utcnow().isoformat()},
        )

    return {
        "tenant_id":    tenant_id,
        "tenant_name":  tenant["company_name"],
        "plan":         tenant["plan"],
        "key_id":       key_id,
        "auth_ms":      int((time.time() - start) * 1000),
    }


# ================================================================
# Helper: بناء النص المُنسَّق
# ================================================================
def build_formatted_text(config: NcrConfigRequest) -> str:
    """
    يحوّل إعدادات NCR إلى نص بفاصلات
    مثال: Binding: Glue Binding, Side: Up, Serial Start: 00001, Master: White, ...
    """
    parts = []

    if config.binding_option:
        parts.append(f"Binding: {BIND_LABELS.get(config.binding_option, config.binding_option)}")

    if config.binding_side:
        parts.append(f"Side: {SIDE_LABELS.get(config.binding_side, config.binding_side)}")

    if config.serial_start:
        parts.append(f"Serial Start: {config.serial_start}")

    for pc in (config.paper_colors or []):
        color = pc.color.capitalize()
        parts.append(f"{pc.layer}: {color}")

    return ", ".join(parts)


def build_order_note(config: NcrConfigRequest) -> str:
    """
    نص تفصيلي لـ sale.order.line في الباك اند
    """
    lines = ["[ NCR Bill Book Configuration ]"]

    if config.binding_option:
        lines.append(f"Binding      : {BIND_LABELS.get(config.binding_option, config.binding_option)}")
    if config.binding_side:
        lines.append(f"Binding Side : {SIDE_LABELS.get(config.binding_side, config.binding_side)}")
    if config.serial_start:
        lines.append(f"Serial Start : {config.serial_start}")
    if config.paper_colors:
        lines.append("Paper Colors :")
        for pc in config.paper_colors:
            lines.append(f"  {pc.layer}: {pc.color.capitalize()}")
    if config.product_name:
        lines.append(f"Product      : {config.product_name}")

    lines.append("--------------------------------")
    return "\n".join(lines)


# ================================================================
# Endpoints
# ================================================================

@app.get("/health")
async def health():
    """Health check — بدون auth"""
    return {"status": "ok", "service": "NCR SaaS API", "version": "1.0.0"}


@app.post("/api/v1/ncr/process", response_model=NcrConfigResponse)
async def process_ncr_config(
    config: NcrConfigRequest,
    request: Request,
    auth: dict = Depends(verify_api_key),
):
    """
    النقطة الرئيسية — تستقبل إعدادات NCR وترجع:
    - generated_text: نص بفاصلات للـ Product Customizations
    - order_note: نص تفصيلي للـ Sale Order
    - config_id: UUID للتخزين
    """
    start = time.time()

    generated_text = build_formatted_text(config)
    order_note     = build_order_note(config)

    config_id = None
    async with httpx.AsyncClient() as client:
        # حفظ التكوين في قاعدة البيانات
        try:
            save_resp = await client.post(
                f"{SUPABASE_URL}/rest/v1/ncr_configs",
                headers=SUPABASE_HEADERS,
                json={
                    "tenant_id":      auth["tenant_id"],
                    "odoo_order_id":  config.odoo_order_id,
                    "odoo_line_id":   config.odoo_line_id,
                    "product_name":   config.product_name,
                    "binding_option": config.binding_option,
                    "binding_side":   config.binding_side,
                    "serial_start":   config.serial_start,
                    "paper_colors":   [pc.dict() for pc in (config.paper_colors or [])],
                    "raw_config":     config.dict(),
                    "generated_text": generated_text,
                    "status":         "processed",
                },
            )
            if save_resp.status_code in (200, 201) and save_resp.json():
                config_id = save_resp.json()[0].get("id")
        except Exception:
            pass  # لا نوقف الطلب إذا فشل الحفظ

        # تسجيل الطلب في الـ log
        elapsed = int((time.time() - start) * 1000)
        try:
            await client.post(
                f"{SUPABASE_URL}/rest/v1/request_logs",
                headers=SUPABASE_HEADERS,
                json={
                    "tenant_id":   auth["tenant_id"],
                    "api_key_id":  auth["key_id"],
                    "endpoint":    "/api/v1/ncr/process",
                    "method":      "POST",
                    "status_code": 200,
                    "response_ms": elapsed,
                    "ip_address":  request.client.host if request.client else None,
                },
            )
        except Exception:
            pass

    return NcrConfigResponse(
        status="ok",
        generated_text=generated_text,
        config_id=config_id,
        tenant=auth["tenant_name"],
    )


@app.post("/api/v1/ncr/validate-key")
async def validate_key(auth: dict = Depends(verify_api_key)):
    """يتحقق فقط من صلاحية الـ key — يستخدمه موديل أودو عند الإعداد"""
    return {
        "valid":   True,
        "tenant":  auth["tenant_name"],
        "plan":    auth["plan"],
    }


@app.get("/api/v1/ncr/configs")
async def get_configs(
    limit: int = 20,
    auth: dict = Depends(verify_api_key),
):
    """يجلب آخر تكوينات NCR للمشترك"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/ncr_configs",
            headers=SUPABASE_HEADERS,
            params={
                "tenant_id": f"eq.{auth['tenant_id']}",
                "select":    "id,created_at,product_name,generated_text,status",
                "order":     "created_at.desc",
                "limit":     str(limit),
            },
        )
    return {"configs": resp.json() if resp.status_code == 200 else []}


# ================================================================
# Entry point
# ================================================================
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        reload=False,
    )
