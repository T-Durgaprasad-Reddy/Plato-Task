import random
from django.core.management.base import BaseCommand
from payouts.models import Merchant, BankAccount, LedgerEntry


class Command(BaseCommand):
    help = (
        'Seeds the database with 3 merchants, each with 5 CREDIT ledger '
        'entries (totaling 50,000-200,000 paise) and 2 bank accounts.'
    )

    def handle(self, *args, **options):
        merchants_data = [
            {'name': 'Acme Corp', 'total_paise': 200000},     # ₹2,000
            {'name': 'Widget Labs', 'total_paise': 100000},    # ₹1,000
            {'name': 'FreshBites', 'total_paise': 50000},      # ₹500
        ]

        for data in merchants_data:
            merchant, created = Merchant.objects.get_or_create(name=data['name'])
            verb = 'Created' if created else 'Found existing'
            self.stdout.write(f"{verb} merchant: {merchant.name}")

            # Create 2 bank accounts per merchant
            bank_accounts_data = [
                {
                    'account_number': f'10{random.randint(10000000, 99999999)}',
                    'ifsc': f'HDFC000{random.randint(1000, 9999)}',
                },
                {
                    'account_number': f'20{random.randint(10000000, 99999999)}',
                    'ifsc': f'ICIC000{random.randint(1000, 9999)}',
                },
            ]

            for ba_data in bank_accounts_data:
                ba, ba_created = BankAccount.objects.get_or_create(
                    merchant=merchant,
                    account_number=ba_data['account_number'],
                    ifsc=ba_data['ifsc'],
                )
                if ba_created:
                    self.stdout.write(f"  Bank account created: {ba}")

            # Create 5 CREDIT entries totaling data['total_paise']
            if merchant.ledger_entries.count() == 0:
                total = data['total_paise']
                amounts = self._split_amount(total, 5)
                for i, amt in enumerate(amounts, 1):
                    LedgerEntry.objects.create(
                        merchant=merchant,
                        entry_type=LedgerEntry.CREDIT,
                        amount_paise=amt,
                        ref_type='topup',
                        ref_id=f'SEED-{merchant.id}-{i}',
                    )
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  5 CREDIT entries totaling {total} paise "
                        f"(INR {total/100:.2f})"
                    )
                )
            else:
                self.stdout.write(
                    f"  Ledger entries already exist, skipping credits"
                )

        self.stdout.write(self.style.SUCCESS('\nSeed complete!'))

    @staticmethod
    def _split_amount(total, n):
        """Split total into n random positive integers that sum to total."""
        cuts = sorted(random.sample(range(1, total), n - 1))
        parts = []
        prev = 0
        for cut in cuts:
            parts.append(cut - prev)
            prev = cut
        parts.append(total - prev)
        return parts
