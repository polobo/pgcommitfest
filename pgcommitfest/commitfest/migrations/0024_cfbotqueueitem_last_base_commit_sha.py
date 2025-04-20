from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('commitfest', '0023_cfbotbranchhistory_task_count'),
    ]

    operations = [
        migrations.AddField(
            model_name='cfbotqueueitem',
            name='last_base_commit_sha',
            field=models.TextField(null=True, blank=False),
        ),
    ]
