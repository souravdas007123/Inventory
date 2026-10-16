from django.db import migrations


class Migration(migrations.Migration):
    """Serial numbers 17-26 in front of the remaining names of the admin panel (names only, no data changes)."""

    dependencies = [
        ('backend', '0011_speed_indexes'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='shopprofile',
            options={'verbose_name': '25. Shop Profile', 'verbose_name_plural': '25. Shop Profile'},
        ),
        migrations.AlterModelOptions(
            name='customer',
            options={'verbose_name': '17. Customer', 'verbose_name_plural': '17. Customers'},
        ),
        migrations.AlterModelOptions(
            name='customerpayment',
            options={'verbose_name': '18. Customer Payment', 'verbose_name_plural': '18. Customer Payments'},
        ),
        migrations.AlterModelOptions(
            name='salesreturn',
            options={'verbose_name': '19. Sales Return (Credit Note)', 'verbose_name_plural': '19. Sales Returns (Credit Notes)'},
        ),
        migrations.AlterModelOptions(
            name='purchasereturn',
            options={'verbose_name': '20. Purchase Return (Debit Note)', 'verbose_name_plural': '20. Purchase Returns (Debit Notes)'},
        ),
        migrations.AlterModelOptions(
            name='stockwriteoff',
            options={'verbose_name': '21. Stock Write-off', 'verbose_name_plural': '21. Stock Write-offs'},
        ),
        migrations.AlterModelOptions(
            name='stockaudit',
            options={'verbose_name': '22. Stock Audit', 'verbose_name_plural': '22. Stock Audits'},
        ),
        migrations.AlterModelOptions(
            name='employee',
            options={'verbose_name': '23. Employee', 'verbose_name_plural': '23. Employees'},
        ),
        migrations.AlterModelOptions(
            name='recurringexpense',
            options={'verbose_name': '24. Fixed Expense', 'verbose_name_plural': '24. Fixed Expenses'},
        ),
        migrations.AlterModelOptions(
            name='importlayout',
            options={'verbose_name': '26. Import file layout', 'verbose_name_plural': '26. Import file layouts'},
        ),
    ]
