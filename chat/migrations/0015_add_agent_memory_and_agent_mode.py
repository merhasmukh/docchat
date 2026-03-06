from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0014_add_source_type_to_document"),
    ]

    operations = [
        migrations.AddField(
            model_name="llmconfig",
            name="agent_mode",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Enable agentic loop with tool-use and cross-session memory. "
                    "Agent can search the document, fetch specific pages, and remember users across sessions."
                ),
            ),
        ),
        migrations.CreateModel(
            name="AgentMemory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("user_email", models.EmailField(db_index=True, unique=True)),
                ("memory_text", models.TextField(blank=True)),
                ("total_sessions", models.IntegerField(default=0)),
                ("last_updated", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Agent Memory",
                "verbose_name_plural": "Agent Memories",
                "ordering": ["-last_updated"],
            },
        ),
    ]
