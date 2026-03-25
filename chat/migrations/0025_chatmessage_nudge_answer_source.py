from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0024_llmconfig_rag_top_k'),
    ]

    operations = [
        migrations.AlterField(
            model_name='chatmessage',
            name='answer_source',
            field=models.CharField(
                max_length=20,
                choices=[
                    ('llm',           'LLM (normal call)'),
                    ('session_cache', 'Session cache'),
                    ('liked_qa',      'Liked Q&A cache'),
                    ('nudge',         'Proactive nudge message'),
                ],
                default='llm',
                help_text='Where this answer was sourced from.',
            ),
        ),
    ]
