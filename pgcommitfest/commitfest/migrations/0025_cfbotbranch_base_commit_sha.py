from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('commitfest', '0024_cfbotqueueitem_last_base_commit_sha'),
    ]

    operations = [
        migrations.AddField(
            model_name='cfbotbranch',
            name='base_commit_sha',
            field=models.TextField(null=True, blank=False),
        ),
    ]
