import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='Merchant',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name='BankAccount',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('account_number', models.CharField(max_length=20)),
                ('ifsc', models.CharField(max_length=11)),
                ('merchant', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='bank_accounts',
                    to='payouts.merchant',
                )),
            ],
            options={
                'unique_together': {('merchant', 'account_number', 'ifsc')},
            },
        ),
        migrations.CreateModel(
            name='LedgerEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('entry_type', models.CharField(
                    choices=[
                        ('CREDIT', 'Credit'),
                        ('DEBIT', 'Debit'),
                        ('HOLD', 'Hold'),
                        ('RELEASE', 'Release'),
                    ],
                    max_length=10,
                )),
                ('amount_paise', models.BigIntegerField()),
                ('ref_type', models.CharField(max_length=50)),
                ('ref_id', models.CharField(max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('merchant', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='ledger_entries',
                    to='payouts.merchant',
                )),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='ledgerentry',
            index=models.Index(fields=['merchant', 'entry_type'], name='payouts_led_merchan_entry_idx'),
        ),
        migrations.AddIndex(
            model_name='ledgerentry',
            index=models.Index(fields=['ref_type', 'ref_id'], name='payouts_led_ref_type_idx'),
        ),
        migrations.CreateModel(
            name='Payout',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount_paise', models.BigIntegerField()),
                ('status', models.CharField(
                    choices=[
                        ('pending', 'Pending'),
                        ('processing', 'Processing'),
                        ('completed', 'Completed'),
                        ('failed', 'Failed'),
                    ],
                    default='pending',
                    max_length=20,
                )),
                ('idempotency_key', models.CharField(max_length=255)),
                ('retry_count', models.IntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('merchant', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='payouts',
                    to='payouts.merchant',
                )),
                ('bank_account', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='payouts',
                    to='payouts.bankaccount',
                )),
            ],
            options={
                'ordering': ['-created_at'],
                'unique_together': {('merchant', 'idempotency_key')},
            },
        ),
    ]
