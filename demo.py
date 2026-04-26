"""
Demo script — runs the full payout lifecycle in the terminal.
Usage: python demo.py
"""
import os
import uuid

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

from payouts.models import Merchant, BankAccount, LedgerEntry, Payout
from payouts.services import PayoutService


def demo():
    # Ensure seed data exists
    merchant = Merchant.objects.first()
    if not merchant:
        print("No merchants found. Run: python manage.py seed_data")
        return

    bank_account = BankAccount.objects.filter(merchant=merchant).first()
    if not bank_account:
        print("No bank accounts found. Run: python manage.py seed_data")
        return

    print("=" * 60)
    print("  PLAYTO PAYOUT ENGINE — DEMO")
    print("=" * 60)

    # 1. Show current state
    print(f"\n--- 1. Merchant State ---")
    available = merchant.get_available_balance()
    held = merchant.get_held_balance()
    print(f"  Merchant:  {merchant.name}")
    print(f"  Available: {available} paise (INR {available/100:.2f})")
    print(f"  Held:      {held} paise (INR {held/100:.2f})")
    print(f"  Bank:      {bank_account}")

    # 2. Create a payout
    print(f"\n--- 2. Requesting Payout (5,000 paise = INR 50) ---")
    idem_key = str(uuid.uuid4())
    payout, is_replay = PayoutService.create_payout(
        merchant_id=merchant.id,
        amount_paise=5000,
        bank_account_id=bank_account.id,
        idempotency_key=idem_key,
    )
    print(f"  Payout #{payout.id} | Status: {payout.status} | Replay: {is_replay}")

    available = merchant.get_available_balance()
    held = merchant.get_held_balance()
    print(f"  Available: {available} paise | Held: {held} paise")

    # 3. Test idempotency
    print(f"\n--- 3. Idempotency Test (same key) ---")
    payout2, is_replay2 = PayoutService.create_payout(
        merchant_id=merchant.id,
        amount_paise=5000,
        bank_account_id=bank_account.id,
        idempotency_key=idem_key,
    )
    print(f"  Same ID: {payout.id == payout2.id} | Replay: {is_replay2}")

    # 4. Process the payout (simulate success)
    print(f"\n--- 4. Processing Payout (pending -> processing -> completed) ---")
    PayoutService.transition_payout(payout.id, Payout.PROCESSING)
    print(f"  Status after processing: {Payout.objects.get(id=payout.id).status}")
    PayoutService.transition_payout(payout.id, Payout.COMPLETED)
    print(f"  Status after completed: {Payout.objects.get(id=payout.id).status}")

    available = merchant.get_available_balance()
    held = merchant.get_held_balance()
    print(f"  Final Available: {available} paise (INR {available/100:.2f})")
    print(f"  Final Held:      {held} paise")

    # 5. Show ledger entries for this payout
    print(f"\n--- 5. Ledger Entries for Payout #{payout.id} ---")
    entries = LedgerEntry.objects.filter(ref_id=str(payout.id)).order_by('created_at')
    for e in entries:
        print(f"  {e.entry_type:8s} | {e.amount_paise:>8d} paise | {e.ref_type}:{e.ref_id}")

    # 6. Test state machine guard
    print(f"\n--- 6. State Machine Guard (completed -> failed) ---")
    try:
        PayoutService.transition_payout(payout.id, Payout.FAILED)
        print("  ERROR: Should have raised ValidationError!")
    except Exception as e:
        print(f"  BLOCKED (correct): {str(e).encode('ascii', 'replace').decode('ascii')}")

    print("\n" + "=" * 60)
    print("  DEMO COMPLETE")
    print("=" * 60)


if __name__ == '__main__':
    demo()
