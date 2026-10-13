import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):
    """Employees (with an automatic monthly salary entry), fixed monthly expenses (rent ...), and the extra Expense
    columns that link an expense to an employee / the fixed expense that created it."""

    dependencies = [
        ('backend', '0006_selling_price_mrp'),
    ]

    operations = [
        migrations.CreateModel(
            name='Employee',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(blank=True, editable=False, max_length=20, unique=True, verbose_name='Employee ID')),
                ('name', models.CharField(max_length=255)),
                ('phone', models.CharField(blank=True, max_length=15, null=True)),
                ('designation', models.CharField(blank=True, default='', help_text='e.g. Pharmacist, Helper', max_length=100)),
                ('joining_date', models.DateField(default=django.utils.timezone.localdate)),
                ('leaving_date', models.DateField(blank=True, help_text='Leave empty while the employee is working', null=True)),
                ('monthly_salary', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
                ('pay_day', models.PositiveSmallIntegerField(default=1, help_text='Day of the month the salary is paid (1-28)', validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(28)])),
                ('auto_salary', models.BooleanField(default=True, verbose_name='Add salary automatically every month')),
                ('is_active', models.BooleanField(default=True)),
                ('last_salary_period', models.DateField(blank=True, editable=False, null=True)),
            ],
            options={
                'verbose_name': 'Employee',
                'verbose_name_plural': 'Employees',
            },
        ),
        migrations.CreateModel(
            name='RecurringExpense',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(help_text='e.g. Shop rent', max_length=255)),
                ('name', models.CharField(choices=[('RENT', 'Rent'), ('SALARY', 'Salary'), ('ELECTRICITY', 'Electricity'), ('WATER', 'Water'), ('MAINTENANCE', 'Maintenance'), ('OTHER', 'Other')], max_length=255, verbose_name='Type')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=12)),
                ('day_of_month', models.PositiveSmallIntegerField(default=1, help_text='The entry is added on this day of every month (1-28)', validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(28)])),
                ('start_date', models.DateField(default=django.utils.timezone.localdate, help_text='The first entry is added on or after this date')),
                ('end_date', models.DateField(blank=True, help_text='Optional: stop adding entries after this date', null=True)),
                ('is_active', models.BooleanField(default=True)),
                ('last_period', models.DateField(blank=True, editable=False, null=True)),
            ],
            options={
                'verbose_name': 'Fixed Expense',
                'verbose_name_plural': 'Fixed Expenses',
            },
        ),
        migrations.AddField(
            model_name='expense',
            name='employee',
            field=models.ForeignKey(blank=True, help_text='For a salary: whose salary it is', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='expenses', to='backend.employee'),
        ),
        migrations.AddField(
            model_name='expense',
            name='note',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='expense',
            name='recurring',
            field=models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='entries', to='backend.recurringexpense'),
        ),
        migrations.AddField(
            model_name='expense',
            name='period',
            field=models.DateField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name='expense',
            name='auto',
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.AddConstraint(
            model_name='expense',
            constraint=models.UniqueConstraint(condition=models.Q(recurring__isnull=False), fields=('recurring', 'period'), name='unique_recurring_period'),
        ),
        migrations.AddConstraint(
            model_name='expense',
            constraint=models.UniqueConstraint(condition=models.Q(auto=True, employee__isnull=False), fields=('employee', 'period'), name='unique_salary_period'),
        ),
    ]
