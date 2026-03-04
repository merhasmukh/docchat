from django.db import migrations, models


def backfill_avg_tokens(apps, schema_editor):
    """Set avg_tokens_per_message for all existing ChatSession rows."""
    ChatSession = apps.get_model("chat", "ChatSession")
    for session in ChatSession.objects.all():
        if session.message_count > 0:
            session.avg_tokens_per_message = session.total_tokens / session.message_count
        else:
            session.avg_tokens_per_message = 0.0
        session.save(update_fields=["avg_tokens_per_message"])


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0008_add_emailverification"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatsession",
            name="avg_tokens_per_message",
            field=models.FloatField(default=0),
        ),
        migrations.RunPython(backfill_avg_tokens, migrations.RunPython.noop),
    ]
