import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from django.test import TransactionTestCase
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient
from rest_framework import status
from .models import Merchant, BankAccount, LedgerEntry, Payout
from .services import PayoutService


class ConcurrencyTest(TransactionTestCase):
    """
    Test 1 (spec): Fire 2 payout requests for the same merchant simultaneously.
    Assert only 1 HOLD LedgerEntry is created.
    
    Uses ThreadPoolExecutor as required by the spec.
    """

    def setUp(self):
        self.merchant = Merchant.objects.create(name='Concurrency Test Merchant')
        self.bank_account = BankAccount.objects.create(
            merchant=self.merchant,
            account_number='1234567890',
            ifsc='HDFC0001234',
        )
        # Credit 10,000 paise (₹100)
        LedgerEntry.objects.create(
            merchant=self.merchant,
            entry_type=LedgerEntry.CREDIT,
            amount_paise=10000,
            ref_type='topup',
            ref_id='INIT',
        )

    def test_concurrent_payouts_only_one_hold(self):
        """
        Balance = 10,000 paise. Two parallel requests for 6,000 paise each.
        Only one should succeed (get a HOLD). The other must fail with
        'Insufficient balance'.
        """
        results = []

        def make_payout_request(idempotency_key):
            client = APIClient()
            response = client.post(
                '/api/v1/payouts/',
                data={
                    'amount_paise': 6000,
                    'bank_account_id': self.bank_account.id,
                    'merchant_id': self.merchant.id,
                },
                format='json',
                HTTP_IDEMPOTENCY_KEY=str(idempotency_key),
            )
            return response

        # Fire 2 requests simultaneously using ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(make_payout_request, uuid.uuid4()),
                executor.submit(make_payout_request, uuid.uuid4()),
            ]
            for future in as_completed(futures):
                results.append(future.result())

        # Exactly one should succeed (201), one should fail (400)
        successes = [r for r in results if r.status_code == status.HTTP_201_CREATED]
        failures = [r for r in results if r.status_code == status.HTTP_400_BAD_REQUEST]

        self.assertEqual(len(successes), 1, f"Expected 1 success, got {len(successes)}")
        self.assertEqual(len(failures), 1, f"Expected 1 failure, got {len(failures)}")

        # Only 1 HOLD entry should exist — the core assertion
        hold_count = LedgerEntry.objects.filter(
            merchant=self.merchant, entry_type=LedgerEntry.HOLD
        ).count()
        self.assertEqual(hold_count, 1, f"Expected 1 HOLD entry, got {hold_count}")

        # On PostgreSQL: select_for_update() serializes requests -> "Insufficient balance"
        # On SQLite (dev): no FOR UPDATE -> "database table is locked" (still mutex)
        # Both prove mutual exclusion: only 1 payout was created.
        failure_text = str(failures[0].data)
        self.assertTrue(
            'Insufficient balance' in failure_text or 'locked' in failure_text,
            f"Unexpected failure message: {failure_text}"
        )


class IdempotencyTest(TransactionTestCase):
    """
    Test 2 (spec): POST the same Idempotency-Key twice.
    Assert same payout_id returned, only 1 row in DB.
    """

    def setUp(self):
        self.merchant = Merchant.objects.create(name='Idempotency Test Merchant')
        self.bank_account = BankAccount.objects.create(
            merchant=self.merchant,
            account_number='9876543210',
            ifsc='ICIC0005678',
        )
        LedgerEntry.objects.create(
            merchant=self.merchant,
            entry_type=LedgerEntry.CREDIT,
            amount_paise=100000,
            ref_type='topup',
            ref_id='INIT',
        )
        self.client = APIClient()

    def test_same_key_returns_same_payout(self):
        """Same key, same payload → 201 first time, 200 second time, same ID."""
        idem_key = str(uuid.uuid4())
        payload = {
            'amount_paise': 1000,
            'bank_account_id': self.bank_account.id,
            'merchant_id': self.merchant.id,
        }

        # First request → 201 Created
        resp1 = self.client.post(
            '/api/v1/payouts/',
            data=payload,
            format='json',
            HTTP_IDEMPOTENCY_KEY=idem_key,
        )
        self.assertEqual(resp1.status_code, status.HTTP_201_CREATED)

        # Second request (same key) → 200 OK (replay)
        resp2 = self.client.post(
            '/api/v1/payouts/',
            data=payload,
            format='json',
            HTTP_IDEMPOTENCY_KEY=idem_key,
        )
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)

        # Same payout ID returned
        self.assertEqual(resp1.data['id'], resp2.data['id'])

        # Only 1 payout row in DB
        payout_count = Payout.objects.filter(merchant=self.merchant).count()
        self.assertEqual(payout_count, 1)

        # Only 1 HOLD ledger entry
        hold_count = LedgerEntry.objects.filter(
            merchant=self.merchant, entry_type=LedgerEntry.HOLD
        ).count()
        self.assertEqual(hold_count, 1)

    def test_expired_key_returns_400(self):
        """
        Idempotency key older than 24h must be rejected with 400.
        We time-travel the payout's created_at to 25h ago using .update()
        (bypasses auto_now_add so we can set an arbitrary past time).
        """
        from django.utils import timezone
        import datetime

        idem_key = str(uuid.uuid4())
        payload = {
            'amount_paise': 1000,
            'bank_account_id': self.bank_account.id,
            'merchant_id': self.merchant.id,
        }

        # Create the payout normally
        resp1 = self.client.post(
            '/api/v1/payouts/',
            data=payload,
            format='json',
            HTTP_IDEMPOTENCY_KEY=idem_key,
        )
        self.assertEqual(resp1.status_code, status.HTTP_201_CREATED)

        # Time-travel: set created_at to 25 hours ago (expired)
        past = timezone.now() - datetime.timedelta(hours=25)
        Payout.objects.filter(id=resp1.data['id']).update(created_at=past)

        # Second request with same key — should now be rejected as expired
        resp2 = self.client.post(
            '/api/v1/payouts/',
            data=payload,
            format='json',
            HTTP_IDEMPOTENCY_KEY=idem_key,
        )
        self.assertEqual(resp2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('expired', str(resp2.data).lower())


class StateMachineTest(TransactionTestCase):
    """
    Verify that illegal state transitions are blocked at the model level.
    """

    def setUp(self):
        self.merchant = Merchant.objects.create(name='State Machine Merchant')
        self.bank_account = BankAccount.objects.create(
            merchant=self.merchant,
            account_number='5555555555',
            ifsc='SBIN0009999',
        )
        LedgerEntry.objects.create(
            merchant=self.merchant,
            entry_type=LedgerEntry.CREDIT,
            amount_paise=50000,
            ref_type='topup',
            ref_id='INIT',
        )
        # Create a payout
        self.payout, _ = PayoutService.create_payout(
            merchant_id=self.merchant.id,
            amount_paise=5000,
            bank_account_id=self.bank_account.id,
            idempotency_key=str(uuid.uuid4()),
        )

    def test_pending_to_completed_blocked(self):
        """pending → completed is illegal (must go through processing)."""
        with self.assertRaises(ValidationError):
            PayoutService.transition_payout(self.payout.id, Payout.COMPLETED)

    def test_failed_to_completed_blocked(self):
        """failed → completed is illegal (failed is terminal)."""
        # Move to processing then failed
        PayoutService.transition_payout(self.payout.id, Payout.PROCESSING)
        PayoutService.transition_payout(self.payout.id, Payout.FAILED)

        with self.assertRaises(ValidationError):
            PayoutService.transition_payout(self.payout.id, Payout.COMPLETED)

    def test_completed_to_anything_blocked(self):
        """completed is terminal — no transitions allowed."""
        PayoutService.transition_payout(self.payout.id, Payout.PROCESSING)
        PayoutService.transition_payout(self.payout.id, Payout.COMPLETED)

        with self.assertRaises(ValidationError):
            PayoutService.transition_payout(self.payout.id, Payout.FAILED)

    def test_valid_transition_pending_processing_completed(self):
        """Happy path: pending → processing → completed."""
        PayoutService.transition_payout(self.payout.id, Payout.PROCESSING)
        PayoutService.transition_payout(self.payout.id, Payout.COMPLETED)

        self.payout.refresh_from_db()
        self.assertEqual(self.payout.status, Payout.COMPLETED)

    def test_valid_transition_pending_processing_failed(self):
        """Failure path: pending → processing → failed releases funds."""
        PayoutService.transition_payout(self.payout.id, Payout.PROCESSING)
        PayoutService.transition_payout(self.payout.id, Payout.FAILED)

        self.payout.refresh_from_db()
        self.assertEqual(self.payout.status, Payout.FAILED)

        # After failure, the RELEASE should restore available balance
        available = self.merchant.get_available_balance()
        # Started with 50000, held 5000, released 5000 → back to 50000
        self.assertEqual(available, 50000)


class LedgerIntegrityTest(TransactionTestCase):
    """Verify the ledger balance formula correctness."""

    def setUp(self):
        self.merchant = Merchant.objects.create(name='Ledger Test Merchant')
        self.bank_account = BankAccount.objects.create(
            merchant=self.merchant,
            account_number='1111111111',
            ifsc='UTIB0001111',
        )

    def test_balance_after_credit(self):
        """CREDIT 10,000 → available = 10,000."""
        LedgerEntry.objects.create(
            merchant=self.merchant, entry_type=LedgerEntry.CREDIT,
            amount_paise=10000, ref_type='topup', ref_id='T1',
        )
        self.assertEqual(self.merchant.get_available_balance(), 10000)

    def test_balance_after_hold(self):
        """CREDIT 10,000 then HOLD 3,000 → available = 7,000."""
        LedgerEntry.objects.create(
            merchant=self.merchant, entry_type=LedgerEntry.CREDIT,
            amount_paise=10000, ref_type='topup', ref_id='T1',
        )
        LedgerEntry.objects.create(
            merchant=self.merchant, entry_type=LedgerEntry.HOLD,
            amount_paise=3000, ref_type='payout', ref_id='P1',
        )
        self.assertEqual(self.merchant.get_available_balance(), 7000)
        self.assertEqual(self.merchant.get_held_balance(), 3000)

    def test_balance_after_completed_payout(self):
        """CREDIT 10,000 → HOLD 3,000 → RELEASE 3,000 + DEBIT 3,000 = 7,000."""
        for entry_type, amount, ref in [
            (LedgerEntry.CREDIT, 10000, 'T1'),
            (LedgerEntry.HOLD, 3000, 'P1'),
            (LedgerEntry.RELEASE, 3000, 'P1'),
            (LedgerEntry.DEBIT, 3000, 'P1'),
        ]:
            LedgerEntry.objects.create(
                merchant=self.merchant, entry_type=entry_type,
                amount_paise=amount, ref_type='payout', ref_id=ref,
            )
        self.assertEqual(self.merchant.get_available_balance(), 7000)
        self.assertEqual(self.merchant.get_held_balance(), 0)

    def test_balance_after_failed_payout(self):
        """CREDIT 10,000 → HOLD 3,000 → RELEASE 3,000 = back to 10,000."""
        for entry_type, amount, ref in [
            (LedgerEntry.CREDIT, 10000, 'T1'),
            (LedgerEntry.HOLD, 3000, 'P1'),
            (LedgerEntry.RELEASE, 3000, 'P1'),
        ]:
            LedgerEntry.objects.create(
                merchant=self.merchant, entry_type=entry_type,
                amount_paise=amount, ref_type='payout', ref_id=ref,
            )
        self.assertEqual(self.merchant.get_available_balance(), 10000)
        self.assertEqual(self.merchant.get_held_balance(), 0)
