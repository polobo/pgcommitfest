from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ("commitfest", "0012_add_leave_date_check_constraint"),
    ]
    operations = [
        migrations.AlterField(
            model_name="commitfest",
            name="status",
            field=models.IntegerField(
                choices=[
                    (1, "Future"),
                    (2, "Open"),
                    (3, "In Progress"),
                    (4, "Closed"),
                    (5, "Parked"),
                ],
                default=1,
            ),
        )
    ]
