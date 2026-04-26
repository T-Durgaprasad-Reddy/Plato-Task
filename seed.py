import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from payouts.models import Merchant, LedgerTransaction

def seed():
    merchant, created = Merchant.objects.get_or_create(name="Test Merchant")
    if created or merchant.transactions.count() == 0:
        # Give initial credit
        LedgerTransaction.objects.create(
            merchant=merchant,
            amount_paise=1000000, # 1,000,000 paise = 10,000 INR/USD (depending on currency unit)
            type=LedgerTransaction.Type.CREDIT,
            reference_id="INITIAL_TOPUP"
        )
        print("Seeded merchant with 1,000,000 paise")
    else:
        print("Merchant already exists")

if __name__ == "__main__":
    seed()
