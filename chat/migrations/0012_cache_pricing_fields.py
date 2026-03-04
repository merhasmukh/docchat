from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0011_llmconfig_context_mode"),
    ]

    operations = [
        # ── ModelPricing: cache price fields ─────────────────────────────────
        migrations.AddField(
            model_name="modelpricing",
            name="cache_read_price_per_million",
            field=models.DecimalField(
                blank=True, null=True, max_digits=12, decimal_places=4,
                help_text="INR per 1M tokens read from cache (cheaper than standard input). Gemini only.",
            ),
        ),
        migrations.AddField(
            model_name="modelpricing",
            name="cache_storage_price_per_million_per_hour",
            field=models.DecimalField(
                blank=True, null=True, max_digits=12, decimal_places=4,
                help_text="INR per 1M cached tokens per hour of storage. Gemini only.",
            ),
        ),
        # ── ChatMessage: per-message cache cost breakdown ─────────────────────
        migrations.AddField(
            model_name="chatmessage",
            name="cached_input_tokens",
            field=models.IntegerField(
                default=0,
                help_text="Input tokens served from Gemini context cache.",
            ),
        ),
        migrations.AddField(
            model_name="chatmessage",
            name="cache_read_cost",
            field=models.DecimalField(
                max_digits=14, decimal_places=6, default=0,
                help_text="Cost for cached input tokens at cache-read rate.",
            ),
        ),
        migrations.AddField(
            model_name="chatmessage",
            name="cache_storage_cost",
            field=models.DecimalField(
                max_digits=14, decimal_places=6, default=0,
                help_text="Pro-rated cache storage cost for this message (1 hour approximation).",
            ),
        ),
        # ── ChatSession: running totals ───────────────────────────────────────
        migrations.AddField(
            model_name="chatsession",
            name="total_cached_input_tokens",
            field=models.BigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="chatsession",
            name="total_cache_read_cost",
            field=models.DecimalField(max_digits=14, decimal_places=6, default=0),
        ),
        migrations.AddField(
            model_name="chatsession",
            name="total_cache_storage_cost",
            field=models.DecimalField(max_digits=14, decimal_places=6, default=0),
        ),
    ]
