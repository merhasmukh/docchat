# 13 — Deployment

## What This File Covers

Moving DocChat from the development server to production — provisioning a server, configuring gunicorn and nginx, switching to MySQL, enabling SSL, and managing costs.

**Prerequisites:** All previous files — the application must be fully working in development before deployment.

---

## 1. Why Not Use `runserver` in Production?

`python manage.py runserver` is for development only:

| Problem | Impact |
|---------|--------|
| Single-threaded | One user blocks everyone else; streaming responses hold the thread |
| No SSL | Passwords and tokens sent in plain text over the network |
| DEBUG=True leaks secrets | Error pages show settings, environment variables, and code |
| No process supervision | If it crashes, it stays down |

Production setup: **gunicorn** (multi-process Python server) + **nginx** (reverse proxy, SSL termination, static file serving).

---

## 2. Deployment Checklist Overview

Work through these steps in order:

1. Provision a Linux server (Ubuntu 22.04 recommended)
2. Install system dependencies (Python, Poppler, Tesseract, nginx, MySQL)
3. Clone the repository and create a virtual environment
4. Install Python dependencies
5. Create production `.env` with secure values
6. Switch to MySQL database
7. Run migrations and collect static files
8. Configure gunicorn as a systemd service
9. Configure nginx as a reverse proxy
10. Get SSL certificate with Let's Encrypt (Certbot)
11. Test everything end-to-end
12. Add model pricing in admin and upload first document

---

## 3. Server Provisioning

**Recommended:** Ubuntu 22.04 LTS — best compatibility with all dependencies.

**Minimum specs:**
- RAM: 4 GB (sentence-transformers model loads ~1 GB on first use)
- Disk: 20 GB (OS + models + uploaded document files)
- CPU: 2 cores

**Cloud providers:** DigitalOcean, Linode, Hetzner, AWS EC2, GCP Compute Engine, Azure VM.

### Install System Packages

After SSH-ing into the server:

```bash
sudo apt update && sudo apt upgrade -y

# Python and build tools
sudo apt install python3.11 python3.11-venv python3-pip build-essential -y

# Poppler (PDF to image)
sudo apt install poppler-utils -y

# Tesseract OCR with Hindi and Gujarati
sudo apt install tesseract-ocr tesseract-ocr-hin tesseract-ocr-guj tesseract-ocr-eng -y

# Nginx
sudo apt install nginx -y

# MySQL server and client
sudo apt install mysql-server libmysqlclient-dev -y

# Git
sudo apt install git -y
```

---

## 4. Clone and Set Up the Application

```bash
# Create a dedicated user for the application (optional but recommended)
sudo adduser docchat
sudo su - docchat

# Clone the repository
git clone <your-repo-url> /home/docchat/app
cd /home/docchat/app

# Create and activate virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
pip install gunicorn   # not in requirements.txt, added for production
```

---

## 5. Production `.env` File

Create `/home/docchat/app/.env` with production values:

```ini
# Generate with: python -c "import secrets; print(secrets.token_hex(50))"
SECRET_KEY=your-long-random-secret-key-here

# Use MySQL in production
DB_ENGINE=mysql
DB_NAME=docchat
DB_USER=docchat_user
DB_PASSWORD=your-strong-db-password
DB_HOST=localhost
DB_PORT=3306

# API keys
GEMINI_API_KEY=your-gemini-key
SARVAM_API_KEY=your-sarvam-key

# Gmail SMTP
EMAIL_HOST_USER=your-gmail@gmail.com
EMAIL_APP_PASSWORD=your-16-char-app-password
```

**Set file permissions** (secrets file should not be readable by other users):

```bash
chmod 600 /home/docchat/app/.env
```

---

## 6. Production Django Settings

You need to change a few settings for production. The cleanest approach is to override them via environment variables or a separate `settings_production.py`. The simplest approach — edit `dochat/settings.py`:

```python
# Change these for production:
DEBUG = False
ALLOWED_HOSTS = ["your-domain.com", "www.your-domain.com"]

# Add these security settings:
SECURE_SSL_REDIRECT = True         # redirect all HTTP to HTTPS
SESSION_COOKIE_SECURE = True       # session cookie only over HTTPS
CSRF_COOKIE_SECURE = True          # CSRF cookie only over HTTPS
SECURE_HSTS_SECONDS = 31536000    # tell browsers to always use HTTPS for 1 year
```

> **Note:** Do not set `SECURE_SSL_REDIRECT = True` before nginx and SSL are configured — you will get redirect loops.

### Generate a Strong SECRET_KEY

```bash
python -c "import secrets; print(secrets.token_hex(50))"
```

Paste the output into `.env` as `SECRET_KEY`. Never use the development placeholder in production.

---

## 7. Switch to MySQL

### Create the Database and User

```bash
sudo mysql
```

```sql
CREATE DATABASE docchat CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'docchat_user'@'localhost' IDENTIFIED BY 'your-strong-db-password';
GRANT ALL PRIVILEGES ON docchat.* TO 'docchat_user'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

### Test the Connection

```bash
mysql -u docchat_user -p docchat
# Enter the password when prompted
# You should see: MySQL [(docchat)]>
EXIT;
```

### Run Migrations

```bash
cd /home/docchat/app
source venv/bin/activate
python manage.py migrate
python manage.py createsuperuser
```

---

## 8. Collect Static Files

Django's development server serves static files automatically, but nginx serves them in production (faster). First, collect all static files into one directory:

```bash
python manage.py collectstatic --noinput
```

This copies everything from `static/` and all installed apps into `staticfiles/`. Expected output:

```
X static files copied to '/home/docchat/app/staticfiles'.
```

---

## 9. Gunicorn — The Python Application Server

Gunicorn runs multiple Python processes, each capable of handling one request at a time.

**Test that gunicorn works:**

```bash
cd /home/docchat/app
source venv/bin/activate
gunicorn dochat.wsgi:application --workers 2 --bind 127.0.0.1:8000
```

Visit `http://your-server-ip:8000/` — you should see the DocChat UI (without CSS, because nginx is not serving static files yet).

Press `Ctrl+C` to stop.

### Why 2 Workers?

SSE streaming responses hold a connection open for the entire streaming duration. With 1 worker, one long streaming response would block everyone else. 2 workers allows 2 simultaneous streaming conversations. For higher load, use `2 × CPU cores` workers.

### Create a Systemd Service (Auto-Start)

Create `/etc/systemd/system/docchat.service`:

```ini
[Unit]
Description=DocChat Gunicorn Application Server
After=network.target mysql.service

[Service]
User=docchat
Group=www-data
WorkingDirectory=/home/docchat/app
EnvironmentFile=/home/docchat/app/.env
ExecStart=/home/docchat/app/venv/bin/gunicorn \
    dochat.wsgi:application \
    --workers 2 \
    --bind 127.0.0.1:8000 \
    --timeout 300 \
    --keep-alive 5 \
    --log-file /home/docchat/app/gunicorn.log \
    --log-level info
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`--timeout 300` — 5-minute timeout per request. Streaming responses for large documents can take a while; without this, gunicorn kills slow responses.

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable docchat
sudo systemctl start docchat
sudo systemctl status docchat
```

Expected status: `Active: active (running)`.

---

## 10. Nginx — Reverse Proxy and Static File Server

Nginx sits in front of gunicorn — it handles HTTPS, serves static files directly (fast), and forwards application requests to gunicorn.

Create `/etc/nginx/sites-available/docchat`:

```nginx
server {
    listen 80;
    server_name your-domain.com www.your-domain.com;

    # Static files — served directly by nginx (fast, no Python involved)
    location /static/ {
        alias /home/docchat/app/staticfiles/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # Application requests — forwarded to gunicorn
    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;

        # CRITICAL for SSE streaming — disable nginx buffering
        # Without this, nginx holds the entire response before sending it to the browser
        # This breaks streaming — the user would see nothing until the answer is complete
        proxy_buffering    off;
        proxy_cache        off;

        # Increase timeouts for streaming responses
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
```

**Enable the site:**

```bash
sudo ln -s /etc/nginx/sites-available/docchat /etc/nginx/sites-enabled/
sudo nginx -t    # test configuration for syntax errors
sudo systemctl reload nginx
```

Visit `http://your-domain.com/` — you should see DocChat with full CSS styling.

> **`proxy_buffering off` is critical.** Without it, nginx buffers the SSE stream and releases it all at once — the user sees a loading spinner, then the full answer appears instantly, with no streaming effect. Always set this for SSE endpoints.

---

## 11. SSL with Let's Encrypt (Certbot)

Certbot automatically gets a free SSL certificate and configures nginx to use it.

```bash
sudo apt install certbot python3-certbot-nginx -y

sudo certbot --nginx -d your-domain.com -d www.your-domain.com
```

Follow the prompts (enter email, agree to terms). Certbot will:
1. Get a certificate from Let's Encrypt
2. Modify your nginx config to enable HTTPS
3. Set up automatic renewal

**Verify auto-renewal:**

```bash
sudo certbot renew --dry-run
```

Certificates are valid for 90 days and renew automatically via a cron job installed by Certbot.

After SSL is set up, go back and enable `SECURE_SSL_REDIRECT = True` in `settings.py`, then restart gunicorn:

```bash
sudo systemctl restart docchat
```

---

## 12. Email in Production

Confirm that the SMTP email backend is active. In `dochat/settings.py`, the default is already `smtp.EmailBackend` — just make sure the console backend override from development is removed.

**Test email from the server:**

```bash
cd /home/docchat/app && source venv/bin/activate
python manage.py shell
```

```python
from django.core.mail import send_mail
send_mail(
    "DocChat Test",
    "This is a test email from DocChat.",
    "your-gmail@gmail.com",
    ["your-test-email@example.com"],
)
```

If you get a connection error, double-check `EMAIL_HOST_USER` and `EMAIL_APP_PASSWORD` in `.env`.

---

## 13. Ollama in Production (Optional)

If you want to use Ollama (local LLM) in production, run it as a systemd service:

Create `/etc/systemd/system/ollama.service`:

```ini
[Unit]
Description=Ollama Local LLM Server
After=network.target

[Service]
User=docchat
ExecStart=/usr/local/bin/ollama serve
Restart=always
RestartSec=5
Environment=OLLAMA_HOST=127.0.0.1:11434

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable ollama
sudo systemctl start ollama
```

> **Resource warning:** Ollama can consume all available RAM when loading a large model. On a 4 GB server, use small models (7B parameters or less). Consider Gemini or Sarvam AI for cloud servers — they require no local GPU/RAM and produce better results.

---

## 14. Cost Management

### Set Up Model Pricing in Admin

After deployment, log into the admin panel and add `ModelPricing` rows for your active provider and model. Without these, all costs show as ₹0.

### Monitor Monthly Spend

Check the Chat Sessions admin view weekly:
- Sort by `total_cost` descending to see the most expensive sessions
- Look at `avg_cost_per_message` to understand typical per-question cost
- Project monthly cost: `avg_cost_per_message × expected_messages_per_day × 30`

### Gemini Quotas

Set a spending limit in Google Cloud Console:
1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Navigate to Billing → Budgets & alerts
3. Create a budget for the Generative AI API service

---

## 15. Health Check and Monitoring

**Health endpoint:** `GET /status/` returns `200 OK` when a document is loaded. Use this for uptime monitoring (Uptime Robot, Better Uptime, etc.):

```
Monitor URL: https://your-domain.com/status/
Expected content: "document_loaded"
```

**Log rotation:** Already configured — `app.log` rotates at 10 MB, keeping 5 old files. No action needed.

**Log location:** `/home/docchat/app/app.log`

**Gunicorn log:** `/home/docchat/app/gunicorn.log`

**Check service status:**

```bash
sudo systemctl status docchat
sudo journalctl -u docchat -n 50   # last 50 log lines
```

---

## 16. Common Production Issues

| Problem | Symptom | Fix |
|---------|---------|-----|
| Static files not loading (404) | CSS/JS missing | Run `collectstatic`; verify nginx `alias` path |
| SSE not streaming | Answer appears all at once | Add `proxy_buffering off` to nginx config |
| Qdrant permission error | Error on document upload | `chown -R docchat:docchat /home/docchat/app/qdrant_storage/` |
| Sentence-transformers slow first response | 30-60 second wait on first chat | Model downloads on first use (~90 MB); subsequent calls are fast |
| Tesseract language data not found | OCR errors for Hindi/Gujarati | Install `tesseract-ocr-hin tesseract-ocr-guj`; verify `tesseract --list-langs` |
| Gunicorn timeout on large PDFs | 502 Bad Gateway during upload | Increase `--timeout` in gunicorn service (currently 300s) |
| "ALLOWED_HOSTS" error | 400 Bad Request on all pages | Add your domain to `ALLOWED_HOSTS` in `settings.py` |
| Gemini cache errors after redeploy | LLM errors on first chat | Cache was created with old model name; delete document and re-upload |

---

## Final Checklist

Before going live:

- [ ] `DEBUG = False` in `settings.py`
- [ ] `ALLOWED_HOSTS` set to your domain(s)
- [ ] Strong `SECRET_KEY` (64+ random chars)
- [ ] MySQL running and migrations applied
- [ ] Static files collected (`collectstatic`)
- [ ] Gunicorn running as a systemd service
- [ ] Nginx configured with `proxy_buffering off`
- [ ] SSL certificate installed (Certbot)
- [ ] `SECURE_SSL_REDIRECT = True` after SSL is confirmed working
- [ ] At least one document uploaded and set as active
- [ ] Model pricing rows added for your provider/model
- [ ] Email OTP tested end-to-end
- [ ] Health check URL monitored
- [ ] Spending limit set in Google Cloud (if using Gemini)

---

Congratulations — your DocChat deployment is complete. You now have a fully functional multilingual document Q&A system running in production, accessible to users anywhere in the world.
