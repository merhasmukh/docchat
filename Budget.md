# Gujarat Vidyapith Admission AI Chatbot — Budget Proposal

**Project:** AI-powered admission information chatbot for Gujarat Vidyapith
**Prepared:** March 2026
**Purpose:** Automate answers to student admission queries (courses, eligibility, fees, dates) in Gujarati, Hindi, and English

---

## Assumptions

| Parameter | Value |
|---|---|
| Message volume | ~1,000 messages/day (30,000/month) |
| LLM model | Gemini 2.5 Flash Lite |
| Avg. tokens per message | ~4,000 input + ~300 output |
| Currency | Indian Rupees (INR) |
| USD/INR rate | ₹85 |
| Email service | Gmail SMTP (free tier) |

---

## One-Time Setup Costs

| Item | Details | Estimated Cost |
|---|---|---|
| Domain registration | e.g. `admissions.gujaratvidyapith.org` or subdomain (free if using existing domain) | ₹0 – ₹1,000 |
| SSL certificate | Free via Let's Encrypt (auto-renew) | ₹0 |
| Server initial setup | OS, Docker, Nginx, Django, Qdrant, Gunicorn config | ₹0 (in-house) |
| Document ingestion | Upload and index admission brochure/PDFs | ₹0 (in-house) |
| **One-Time Total** | | **₹0 – ₹1,000** |

> If external development/deployment is contracted, add ₹10,000–₹25,000 for one-time setup.

---

## Recurring Costs — Monthly

### Option A: Budget VPS (Recommended)

| Item | Provider / Details | Monthly | Yearly |
|---|---|---|---|
| VPS Server | Hostinger KVM VPS (2 vCPU, 8 GB RAM, 100 GB NVMe) | ₹1,400 | ₹16,800 |
| LLM API — Gemini 2.5 Flash Lite | ~30,000 messages/month at ~₹0.056/message | ₹1,675 | ₹20,100 |
| Email (Gmail SMTP) | Free tier (500 emails/day) | ₹0 | ₹0 |
| Domain renewal | Amortized monthly | ₹0 – ₹83 | ₹0 – ₹1,000 |
| **Option A Total** | | **~₹3,075/mo** | **~₹36,900/yr** |

### Option B: Managed Hosting

| Item | Provider / Details | Monthly | Yearly |
|---|---|---|---|
| Managed server | Railway / Render / AWS Lightsail with managed DB | ₹3,500 | ₹42,000 |
| LLM API — Gemini 2.5 Flash Lite | Same as above | ₹1,675 | ₹20,100 |
| Email (Gmail SMTP) | Free | ₹0 | ₹0 |
| **Option B Total** | | **~₹5,175/mo** | **~₹62,100/yr** |

---

## LLM Cost Breakdown

**Model:** Gemini 2.5 Flash Lite
**Pricing:** Input $0.075/MTok · Output $0.30/MTok · Cached input $0.019/MTok

| Scenario | Messages/day | Cost/message | Monthly cost |
|---|---|---|---|
| Low usage | 300 | ₹0.056 | ~₹500 |
| Medium usage | 1,000 | ₹0.056 | ~₹1,675 |
| High usage | 3,000 | ₹0.056 | ~₹5,025 |

> **Cost-saving features already implemented:**
> - Gemini context caching (repeated document context costs ~75% less)
> - Qdrant RAG — only 3 relevant chunks sent per query instead of full document
> - Automatic implicit caching for repeated conversation history

---

## Summary

| | Option A (VPS) | Option B (Managed) |
|---|---|---|
| Monthly | ₹3,075 | ₹5,175 |
| Yearly | ₹36,900 | ₹62,100 |
| One-time setup | ₹0 – ₹1,000 | ₹0 – ₹1,000 |
| **Year 1 Total** | **~₹37,900** | **~₹63,100** |

---

## Recommendation

**Option A (Budget VPS)** is recommended for Phase 1:

- Full control over server, no vendor lock-in
- Sufficient for expected load (1,000 msg/day)
- Can scale vertically (upgrade VPS) or horizontally if needed
- Saves ~₹36,350/year compared to managed hosting

**Upgrade triggers:** Move to Option B (or add load balancer) if:
- Sustained load exceeds 5,000 messages/day
- Uptime SLA requirement > 99.5%
- In-house server maintenance is not feasible

---

## Cost Per Student Interaction

At 1,000 messages/day and ₹3,075/month:

> **₹0.103 per message** (~10 paise per student query)

This compares favourably to:
- Printed admission brochures: ₹20–₹50 per student
- Phone helpline staff: ₹15,000–₹25,000/month salary per operator
- WhatsApp Business API: ₹0.58–₹0.79 per conversation (business-initiated)

---

*Budget figures are estimates based on March 2026 pricing. Actual LLM costs depend on message volume and average context length.*
