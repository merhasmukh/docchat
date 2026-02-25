from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0004_llmconfig_rag_embedding'),
    ]

    operations = [
        migrations.AddField(
            model_name='llmconfig',
            name='sarvam_model',
            field=models.CharField(
                default='sarvam-m',
                help_text='Sarvam AI model ID, e.g. sarvam-m',
                max_length=100,
            ),
        ),
    ]
