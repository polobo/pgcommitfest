# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ("commitfest", "0011_add_partial_indexes_enforcement"),
    ]
    operations = [
        migrations.RunSQL("""
ALTER TABLE commitfest_patchoncommitfest
ADD CONSTRAINT status_and_leavedate_correlation
CHECK ((status IN (4,5,6,7,8)) = (leavedate IS NOT NULL));
"""),
        migrations.RunSQL("""
COMMENT ON COLUMN commitfest_patchoncommitfest.leavedate IS
$$A leave date is recorded in two situations, both of which
means this particular patch-cf combination became inactive
on the corresponding date.  For status 5 the patch was moved
to some other cf.  For 4,6,7, and 8, this was the final cf.
$$
"""),
        migrations.RunSQL("""
COMMENT ON TABLE commitfest_patchoncommitfest IS
$$PoCF (poc): This is a re-entrant table: patches may become associated
with a given cf multiple times, resetting the entrydate and clearing
the leavedate each time.  Non-final statuses never have a leavedate
while final statuses always do.  The final status of 5 (moved) is
special in that all but one of the rows a patch has in this table
must have it as the status.
$$
"""),
    ]
