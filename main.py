"""
NCR SaaS API Server
FastAPI + Supabase
Copyright (C) 2024 M.A. — All Rights Reserved
"""

from __future__ import annotations
import hashlib, secrets, time, os, smtplib
from datetime import datetime, timezone
from typing import Optional
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import httpx
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
import uvicorn

# ── Config ──────────────────────────────────────────────────────
SUPABASE_URL         = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
SMTP_HOST            = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT            = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER            = os.environ.get("SMTP_USER", "")
SMTP_PASS            = os.environ.get("SMTP_PASS", "")
SENDER_NAME          = os.environ.get("SENDER_NAME", "NCR SaaS")

SB = {
    "apikey":        SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}

BIND_LABELS = {"glue":"Glue Binding","staple":"Staple Binding","spiral":"Spiral Binding"}
SIDE_LABELS = {"up":"Up","down":"Down","left":"Left","right":"Right"}

app = FastAPI(title="NCR SaaS API", version="1.0.0",
              docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["POST","GET"],
                   allow_headers=["Authorization","Content-Type","X-API-Key"])

security = HTTPBearer(auto_error=False)

# ── Models ──────────────────────────────────────────────────────
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

class SignupRequest(BaseModel):
    company_name: str
    owner_name:   str
    email:        str
    phone:        Optional[str] = ""
    odoo_domain:  Optional[str] = ""

# ── Helpers ─────────────────────────────────────────────────────
def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()

def generate_api_key() -> str:
    return f"ncr_{secrets.token_hex(32)}"

def build_text(config: NcrConfigRequest) -> str:
    parts = []
    if config.binding_option:
        parts.append(f"Binding: {BIND_LABELS.get(config.binding_option, config.binding_option)}")
    if config.binding_side:
        parts.append(f"Side: {SIDE_LABELS.get(config.binding_side, config.binding_side)}")
    if config.serial_start:
        parts.append(f"Serial Start: {config.serial_start}")
    for pc in (config.paper_colors or []):
        parts.append(f"{pc.layer}: {pc.color.capitalize()}")
    return ", ".join(parts)

async def send_welcome_email(to_email: str, company: str, owner: str, api_key: str):
    """يرسل إيميل ترحيب مع الـ API Key"""
    if not SMTP_USER or not SMTP_PASS:
        return  # لو ما في SMTP نتجاوز بصمت

    subject = "✅ Your NCR SaaS Activation Key — Free Trial"
    html = f"""
<!DOCTYPE html>
<html dir="ltr">
<head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;background:#f5f5f5;margin:0;padding:20px">
<div style="max-width:600px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 20px rgba(0,0,0,0.1)">

  <div style="background:#1D9E75;padding:30px 40px;text-align:center">
    <h1 style="color:#fff;margin:0;font-size:24px">NCR Bill Book Configurator</h1>
    <p style="color:rgba(255,255,255,0.85);margin:8px 0 0">Your free trial is ready!</p>
  </div>

  <div style="padding:40px">
    <p style="color:#333;font-size:16px">Hello <strong>{owner}</strong>,</p>
    <p style="color:#555">Welcome to NCR SaaS! Your 60-day free trial for <strong>{company}</strong> is now active.</p>

    <div style="background:#f0faf7;border:1px solid #a8dcc8;border-radius:8px;padding:20px;margin:24px 0">
      <p style="margin:0 0 8px;font-size:13px;color:#0F6E56;font-weight:bold;text-transform:uppercase;letter-spacing:0.05em">Your Activation Key</p>
      <p style="font-family:monospace;font-size:14px;color:#1a1a1a;word-break:break-all;background:#fff;padding:12px;border-radius:6px;border:1px solid #ddd;margin:0">{api_key}</p>
      <p style="margin:8px 0 0;font-size:12px;color:#888">Keep this key safe. Do not share it.</p>
    </div>

    <h3 style="color:#333;border-bottom:2px solid #f0f0f0;padding-bottom:10px">How to activate in 3 steps:</h3>
    <ol style="color:#555;line-height:2">
      <li>Install the <strong>NCR Bill Book Configurator</strong> module on your Odoo</li>
      <li>Go to <strong>Settings → NCR Bill Book Configurator</strong></li>
      <li>Paste your activation key above and click <strong>Test Connection</strong></li>
    </ol>

    <div style="background:#fff8e1;border-left:4px solid #F9A825;padding:16px;border-radius:0 8px 8px 0;margin:24px 0">
      <p style="margin:0;color:#7A5700;font-size:13px">
        <strong>⏰ Trial expires in 60 days.</strong><br>
        Your trial is completely free — no credit card required.
      </p>
    </div>

    <p style="color:#555">Need help? Reply to this email and we'll assist you.</p>
    <p style="color:#333">Best regards,<br><strong>NCR SaaS Team</strong></p>
  </div>

  <div style="background:#f8f9fa;padding:20px 40px;text-align:center;border-top:1px solid #eee">
    <p style="color:#aaa;font-size:12px;margin:0">NCR Bill Book Configurator SaaS</p>
  </div>

</div>
</body>
</html>
"""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"{SENDER_NAME} <{SMTP_USER}>"
        msg["To"]      = to_email
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, to_email, msg.as_string())
    except Exception as e:
        print(f"Email error: {e}")

# ── Auth ────────────────────────────────────────────────────────
async def verify_api_key(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
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
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/api_keys",
            headers=SB,
            params={"key_hash":f"eq.{key_hash}","is_active":"eq.true",
                    "select":"id,tenant_id,expires_at"},
        )
        if resp.status_code != 200 or not resp.json():
            raise HTTPException(status_code=401, detail="Invalid or inactive API key")

        key_data  = resp.json()[0]
        key_id    = key_data["id"]
        tenant_id = key_data["tenant_id"]

        if key_data.get("expires_at"):
            exp = datetime.fromisoformat(key_data["expires_at"].replace("Z","+00:00"))
            if exp < datetime.now(exp.tzinfo):
                raise HTTPException(status_code=401, detail="API key expired")

        resp2 = await client.get(
            f"{SUPABASE_URL}/rest/v1/tenants",
            headers=SB,
            params={"id":f"eq.{tenant_id}","is_active":"eq.true",
                    "select":"id,company_name,plan,plan_expires_at,max_requests"},
        )
        if resp2.status_code != 200 or not resp2.json():
            raise HTTPException(status_code=403, detail="Tenant not found or inactive")

        tenant = resp2.json()[0]

        if tenant.get("plan_expires_at") and tenant["plan"] != "lifetime":
            exp_t = datetime.fromisoformat(tenant["plan_expires_at"].replace("Z","+00:00"))
            if exp_t < datetime.now(exp_t.tzinfo):
                raise HTTPException(status_code=402, detail="Subscription expired. Please renew.")

        await client.patch(
            f"{SUPABASE_URL}/rest/v1/api_keys", headers=SB,
            params={"id":f"eq.{key_id}"},
            json={"last_used_at": datetime.utcnow().isoformat()},
        )

    return {"tenant_id":tenant_id,"tenant_name":tenant["company_name"],
            "plan":tenant["plan"],"key_id":key_id}

# ── Routes ──────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {"service":"NCR SaaS API","version":"1.0.0","status":"running"}

@app.get("/health")
async def health():
    return {"status":"ok","service":"NCR SaaS API","version":"1.0.0"}

# ════════════════════════════════════════════════════════════════
# SIGNUP — تسجيل مجاني 60 يوم
# ════════════════════════════════════════════════════════════════
@app.get("/signup", response_class=HTMLResponse)
async def signup_page():
    """صفحة التسجيل"""
    html = """
<!DOCTYPE html>
<html lang="en" dir="ltr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NCR Bill Book Configurator — Free Trial</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: linear-gradient(135deg, #0F6E56 0%, #1D9E75 50%, #0F6E56 100%);
         min-height: 100vh; display: flex; align-items: center; justify-content: center;
         padding: 20px; }
  .card { background: #fff; border-radius: 16px; width: 100%; max-width: 480px;
          box-shadow: 0 20px 60px rgba(0,0,0,0.2); overflow: hidden; }
  .header { background: #1D9E75; padding: 32px 40px; text-align: center; }
  .header h1 { color: #fff; font-size: 22px; margin-bottom: 6px; }
  .header p { color: rgba(255,255,255,0.85); font-size: 14px; }
  .badge { display: inline-block; background: #FFF176; color: #7A5700;
           padding: 4px 14px; border-radius: 20px; font-size: 13px;
           font-weight: 700; margin-top: 10px; }
  .body { padding: 36px 40px; }
  .field { margin-bottom: 18px; }
  label { display: block; font-size: 13px; font-weight: 600; color: #444;
          margin-bottom: 6px; }
  input { width: 100%; padding: 11px 14px; border: 1.5px solid #ddd;
          border-radius: 8px; font-size: 14px; color: #222;
          transition: border-color 0.2s; outline: none; }
  input:focus { border-color: #1D9E75; box-shadow: 0 0 0 3px rgba(29,158,117,0.1); }
  .hint { font-size: 12px; color: #999; margin-top: 4px; }
  button { width: 100%; padding: 14px; background: #1D9E75; color: #fff;
           border: none; border-radius: 8px; font-size: 15px; font-weight: 600;
           cursor: pointer; transition: background 0.2s; margin-top: 8px; }
  button:hover { background: #0F6E56; }
  button:disabled { background: #aaa; cursor: not-allowed; }
  .features { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 24px; }
  .feature { background: #f0faf7; color: #0F6E56; padding: 6px 12px;
             border-radius: 20px; font-size: 12px; font-weight: 500; }
  #result { display: none; text-align: center; padding: 24px; }
  #result.success .icon { font-size: 48px; margin-bottom: 12px; }
  #result.success h2 { color: #1D9E75; margin-bottom: 8px; }
  #result.success p { color: #555; font-size: 14px; margin-bottom: 16px; }
  .key-box { background: #f0faf7; border: 1px solid #a8dcc8; border-radius: 8px;
             padding: 16px; margin: 16px 0; }
  .key-box label { color: #0F6E56; font-size: 12px; text-transform: uppercase;
                   letter-spacing: 0.05em; margin-bottom: 8px; }
  .key-val { font-family: monospace; font-size: 13px; color: #1a1a1a;
             word-break: break-all; background: #fff; padding: 10px;
             border-radius: 6px; border: 1px solid #ddd; }
  .copy-btn { background: #1D9E75; color: #fff; border: none; border-radius: 6px;
              padding: 8px 16px; font-size: 13px; cursor: pointer; margin-top: 10px;
              width: 100%; }
  .error-msg { background: #fdf2f2; color: #c0392b; padding: 12px 16px;
               border-radius: 8px; font-size: 14px; margin-top: 12px;
               display: none; border-left: 4px solid #e74c3c; }
  .spinner { display: none; }
  .loading .spinner { display: inline-block; }
  .loading .btn-text { display: none; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .spinner { width: 18px; height: 18px; border: 2px solid rgba(255,255,255,0.3);
             border-top-color: #fff; border-radius: 50%;
             animation: spin 0.8s linear infinite; display: inline-block; }
</style>
</head>
<body>
<div class="card">
  <div class="header">
    <h1>NCR Bill Book Configurator</h1>
    <p>For Odoo printing companies</p>
    <div class="badge">60 Days Free Trial</div>
  </div>
  <div class="body">
    <div class="features">
      <span class="feature">✅ No credit card</span>
      <span class="feature">✅ Instant activation</span>
      <span class="feature">✅ Odoo 18 ready</span>
      <span class="feature">✅ Full features</span>
    </div>

    <div id="form-section">
      <div class="field">
        <label>Company Name *</label>
        <input type="text" id="company_name" placeholder="Your Printing Company LLC" required>
      </div>
      <div class="field">
        <label>Your Name *</label>
        <input type="text" id="owner_name" placeholder="John Smith" required>
      </div>
      <div class="field">
        <label>Email Address *</label>
        <input type="email" id="email" placeholder="you@company.com" required>
        <div class="hint">Your activation key will be sent to this email</div>
      </div>
      <div class="field">
        <label>Phone (optional)</label>
        <input type="text" id="phone" placeholder="+971 50 000 0000">
      </div>
      <div class="field">
        <label>Odoo Domain (optional)</label>
        <input type="text" id="odoo_domain" placeholder="yourcompany.odoo.com">
      </div>
      <div id="error-msg" class="error-msg"></div>
      <button id="submit-btn" onclick="submitForm()">
        <span class="btn-text">Start Free Trial →</span>
        <span class="spinner"></span>
      </button>
    </div>

    <div id="result"></div>
  </div>
</div>

<script>
async function submitForm() {
  const company = document.getElementById('company_name').value.trim();
  const owner   = document.getElementById('owner_name').value.trim();
  const email   = document.getElementById('email').value.trim();
  const phone   = document.getElementById('phone').value.trim();
  const domain  = document.getElementById('odoo_domain').value.trim();
  const errDiv  = document.getElementById('error-msg');
  const btn     = document.getElementById('submit-btn');

  errDiv.style.display = 'none';

  if (!company || !owner || !email) {
    errDiv.textContent = 'Please fill in all required fields.';
    errDiv.style.display = 'block';
    return;
  }
  if (!/^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/.test(email)) {
    errDiv.textContent = 'Please enter a valid email address.';
    errDiv.style.display = 'block';
    return;
  }

  btn.disabled = true;
  btn.classList.add('loading');

  try {
    const resp = await fetch('/api/v1/signup', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        company_name: company,
        owner_name:   owner,
        email:        email,
        phone:        phone,
        odoo_domain:  domain,
      }),
    });

    const data = await resp.json();

    if (resp.ok && data.status === 'ok') {
      document.getElementById('form-section').style.display = 'none';
      const result = document.getElementById('result');
      result.className = 'success';
      result.innerHTML = `
        <div class="icon">🎉</div>
        <h2>You're all set!</h2>
        <p>Your activation key has been sent to <strong>${email}</strong></p>
        <div class="key-box">
          <label>Your Activation Key</label>
          <div class="key-val" id="api-key-display">${data.api_key}</div>
          <button class="copy-btn" onclick="copyKey('${data.api_key}')">📋 Copy Key</button>
        </div>
        <p style="font-size:13px;color:#888;margin-top:12px">
          Install the Odoo module, go to Settings → NCR Configurator, paste this key.
        </p>
      `;
      result.style.display = 'block';
    } else {
      errDiv.textContent = data.detail || 'Something went wrong. Please try again.';
      errDiv.style.display = 'block';
      btn.disabled = false;
      btn.classList.remove('loading');
    }
  } catch (e) {
    errDiv.textContent = 'Connection error. Please try again.';
    errDiv.style.display = 'block';
    btn.disabled = false;
    btn.classList.remove('loading');
  }
}

function copyKey(key) {
  navigator.clipboard.writeText(key).then(() => {
    const btn = document.querySelector('.copy-btn');
    btn.textContent = '✅ Copied!';
    setTimeout(() => btn.textContent = '📋 Copy Key', 2000);
  });
}

document.addEventListener('keydown', e => {
  if (e.key === 'Enter') submitForm();
});
</script>
</body>
</html>
"""
    return HTMLResponse(content=html)


@app.post("/api/v1/signup")
async def signup(data: SignupRequest):
    """
    تسجيل مشترك جديد:
    1. التحقق من الإيميل (ما يسجل مرتين)
    2. إنشاء tenant في Supabase
    3. إنشاء API Key
    4. إرسال إيميل ترحيب تلقائياً
    """
    async with httpx.AsyncClient() as client:

        # 1. تحقق من الإيميل — هل مسجل مسبقاً؟
        check = await client.get(
            f"{SUPABASE_URL}/rest/v1/tenants",
            headers=SB,
            params={"owner_email":f"eq.{data.email}","select":"id,is_active"},
        )
        if check.status_code == 200 and check.json():
            raise HTTPException(
                status_code=409,
                detail="This email is already registered. Please check your inbox for your API key."
            )

        # 2. إنشاء tenant
        tenant_resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/tenants",
            headers=SB,
            json={
                "company_name":    data.company_name,
                "owner_email":     data.email,
                "owner_name":      data.owner_name,
                "phone":           data.phone or "",
                "plan":            "trial",
                "plan_expires_at": (__import__("datetime").datetime.utcnow() + __import__("datetime").timedelta(days=60)).isoformat(),"
                "is_active":       True,
                "max_requests":    1000,
                "odoo_domain":     data.odoo_domain or "",
            },
        )
        if tenant_resp.status_code not in (200, 201):
            raise HTTPException(status_code=500, detail="Failed to create account. Please try again.")

        tenant_id = tenant_resp.json()[0]["id"]

        # 3. إنشاء API Key
        raw_key    = generate_api_key()
        key_hash   = hash_key(raw_key)
        key_prefix = raw_key[:12]

        key_resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/api_keys",
            headers=SB,
            json={
                "tenant_id":  tenant_id,
                "key_hash":   key_hash,
                "key_prefix": key_prefix,
                "label":      "Trial Key — 60 days",
                "is_active":  True,
            },
        )
        if key_resp.status_code not in (200, 201):
            raise HTTPException(status_code=500, detail="Failed to generate API key. Please try again.")

    # 4. إرسال إيميل (async — لا يوقف الاستجابة)
    import asyncio
    asyncio.create_task(
        asyncio.to_thread(
            send_welcome_email,
            data.email,
            data.company_name,
            data.owner_name,
            raw_key,
        )
    )

    return {
        "status":      "ok",
        "api_key":     raw_key,
        "plan":        "trial",
        "expires_days": 60,
        "message":     f"Welcome! Your 60-day trial is active. Key sent to {data.email}",
    }


# ── NCR Process ─────────────────────────────────────────────────
@app.post("/api/v1/ncr/validate-key")
async def validate_key(auth: dict = Depends(verify_api_key)):
    return {"valid":True,"tenant":auth["tenant_name"],"plan":auth["plan"]}


@app.post("/api/v1/ncr/process", response_model=NcrConfigResponse)
async def process_ncr_config(
    config: NcrConfigRequest,
    request: Request,
    auth: dict = Depends(verify_api_key),
):
    start          = time.time()
    generated_text = build_text(config)
    config_id      = None

    async with httpx.AsyncClient() as client:
        try:
            save = await client.post(
                f"{SUPABASE_URL}/rest/v1/ncr_configs", headers=SB,
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
            if save.status_code in (200,201) and save.json():
                config_id = save.json()[0].get("id")
        except Exception:
            pass

        try:
            await client.post(
                f"{SUPABASE_URL}/rest/v1/request_logs", headers=SB,
                json={
                    "tenant_id":   auth["tenant_id"],
                    "api_key_id":  auth["key_id"],
                    "endpoint":    "/api/v1/ncr/process",
                    "method":      "POST",
                    "status_code": 200,
                    "response_ms": int((time.time()-start)*1000),
                    "ip_address":  request.client.host if request.client else None,
                },
            )
        except Exception:
            pass

    return NcrConfigResponse(
        status="ok", generated_text=generated_text,
        config_id=config_id, tenant=auth["tenant_name"],
    )


@app.get("/api/v1/ncr/configs")
async def get_configs(limit: int = 20, auth: dict = Depends(verify_api_key)):
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/ncr_configs", headers=SB,
            params={"tenant_id":f"eq.{auth['tenant_id']}",
                    "select":"id,created_at,product_name,generated_text,status",
                    "order":"created_at.desc","limit":str(limit)},
        )
    return {"configs": resp.json() if resp.status_code == 200 else []}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0",
                port=int(os.environ.get("PORT",8000)), reload=False)
