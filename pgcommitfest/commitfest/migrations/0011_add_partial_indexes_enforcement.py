# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("commitfest", "0010_add_failing_since_column"),
    ]
    operations = [
        migrations.RunSQL("""
CREATE UNIQUE INDEX cf_enforce_maxoneopen_idx
ON commitfest_commitfest (status)
WHERE status not in (4);
"""),

        migrations.RunSQL("""
CREATE UNIQUE INDEX cf_enforce_maxoneoutcome_idx
ON commitfest_patchoncommitfest (patch_id)
WHERE status not in (5);
"""),
    ]
