from decimal import Decimal

from django.db import migrations, models


def backfill_avg_cost(apps, schema_editor):
    """Set avg_cost_per_message for all existing ChatSession rows."""
    ChatSession = apps.get_model("chat", "ChatSession")
    for session in ChatSession.objects.all():
        if session.message_count > 0:
            session.avg_cost_per_message = session.total_cost / session.message_count
        else:
            session.avg_cost_per_message = Decimal("0")
        session.save(update_fields=["avg_cost_per_message"])


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0009_chatsession_avg_tokens_per_message"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatsession",
            name="avg_cost_per_message",
            field=models.DecimalField(max_digits=14, decimal_places=6, default=0),
        ),
        migrations.RunPython(backfill_avg_cost, migrations.RunPython.noop),
    ]
