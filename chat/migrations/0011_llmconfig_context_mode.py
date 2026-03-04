from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0010_chatsession_avg_cost_per_message"),
    ]

    operations = [
        migrations.AddField(
            model_name="llmconfig",
            name="context_mode",
            field=models.CharField(
                choices=[
                    ("auto", "Auto (use document's computed mode)"),
                    ("full", "Full context (send entire document)"),
                    ("rag",  "RAG (retrieve relevant pages only)"),
                ],
                default="auto",
                help_text=(
                    "Override the context strategy for all providers. "
                    "'Auto' uses the mode computed at document upload time. "
                    "'Full' sends the entire document (Gemini will use context caching). "
                    "'RAG' retrieves the most relevant pages only."
                ),
                max_length=10,
            ),
        ),
    ]
