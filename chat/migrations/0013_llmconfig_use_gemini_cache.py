from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0012_cache_pricing_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="llmconfig",
            name="use_gemini_cache",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Enable Gemini context caching in full-context mode. "
                    "Disable to send the full document with every request (no cache storage cost)."
                ),
            ),
        ),
    ]
