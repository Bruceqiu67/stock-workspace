# Feishu PDF Report Delivery

When the user asks to send a market analysis/portfolio report as a PDF to their Feishu DM, use this workflow. The `hermes send` command only handles text — binary files (PDFs) require direct Feishu API calls.

## Workflow

### Step 1: Generate PDF

Use reportlab + WQY CJK font (see `pdf-generation` skill for full details):

```bash
# Set up venv (if not already present)
uv venv /tmp/pdfgen-env --clear
source /tmp/pdfgen-env/bin/activate
uv pip install reportlab pymupdf
```

Register font:
```python
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
pdfmetrics.registerFont(TTFont('WQY', '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc', subfontIndex=0))
```

Always verify after generation:
```python
import pymupdf, os
doc = pymupdf.open(OUTPUT)
all_text = "".join(page.get_text() for page in doc)
for ch in ['□', '■', '▯', '\ufffd']:
    assert ch not in all_text, f"Tofu: {ch}"
cjk_count = sum(1 for c in all_text if '\u4e00' <= c <= '\u9fff')
assert cjk_count > 0, "No CJK text rendered"
print(f"PDF OK: {len(doc)} pages, {cjk_count} CJK chars, {os.path.getsize(OUTPUT)/1024:.0f}KB")
doc.close()
```

### Step 2: Get Feishu credentials

Read from `~/.hermes/.env`:
```python
with open(os.path.expanduser("~/.hermes/.env")) as f:
    for line in f:
        if line.startswith("FEISHU_APP_ID="):
            app_id = line.split("=",1)[1].strip().strip('"').strip("'")
        elif line.startswith("FEISHU_APP_SECRET=***            app_secret = line.split("=",1)[1].strip().strip('"').strip("'")
```

Also determine the domain:
```python
domain = "open.larksuite.com" if os.environ.get("FEISHU_DOMAIN") == "larksuite" else "open.feishu.cn"
```

### Step 3: Get tenant access token

```bash
curl -s -X POST "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal" \
  -H "Content-Type: application/json" \
  -d '{"app_id":"<APP_ID>","app_secret":"<APP_SECRET>"}'
```

Response: `{"code":0,"msg":"success","tenant_access_token":"<TOKEN>","expire":7200}`

### Step 4: Upload PDF file

```bash
curl -s -X POST "https://open.feishu.cn/open-apis/im/v1/files" \
  -H "Authorization: Bearer <TOKEN>" \
  -F "file_type=pdf" \
  -F "file_name=report.pdf" \
  -F "file=@/path/to/report.pdf"
```

Response: `{"code":0,"data":{"file_key":"file_v3_..."}}`

### Step 5: Send file message to chat

First find the chat ID via `hermes send -l | grep feishu`, then:

```bash
curl -s -X POST "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"receive_id":"oc_...","msg_type":"file","receive_id_type":"chat_id","content":"{\"file_key\":\"file_v3_...\"}"}'
```

### Step 6: Send a text summary alongside (optional but recommended)

```bash
echo "📊 text summary..." | hermes send -t feishu:oc_<YOUR_FEISHU_CHAT_ID> -q
```

## Pitfalls

- **hermes send -f cannot send binaries**: Do NOT use `hermes send -f` for PDFs — it will crash with `'utf-8' codec can't decode byte`.
- **Tenant token expires in 7200s**: Get a fresh token per delivery run.
- **File upload URL domain**: Use `open.feishu.cn` for Feishu users, `open.larksuite.com` for Lark/ByteDance international.
- **Credentials in .env are masked in terminal display only**: Python reading the file gets the actual values. The script runs fine.
