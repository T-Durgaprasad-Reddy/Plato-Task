from django.db import transaction, IntegrityError
from django.core.exceptions import ValidationError
from .models import Merchant, Payout, LedgerEntry, BankAccount


class PayoutService:
    """
    Service layer for payout operations. All money-touching operations
    go through here to ensure atomicity and correctness.
    """

    @staticmethod
    @transaction.atomic
    def create_payout(merchant_id, amount_paise, bank_account_id, idempotency_key):
        """
        Create a payout request with idempotency and concurrency safety.
        
        Flow:
        1. Check if idempotency_key already used → return existing payout (replay)
        2. Lock the merchant row with SELECT FOR UPDATE
        3. Check available_balance >= amount_paise
        4. Create Payout (status=pending) + LedgerEntry (type=HOLD) atomically
        5. Return (payout, is_replay)
        
        Race condition handling:
        - Two requests for the same merchant serialize on select_for_update()
        - Two requests with the same idempotency_key: first wins via unique_together
          constraint; second catches IntegrityError and returns the winner
        """
        # 1. Idempotency check (fast path — no lock needed)
        existing = Payout.objects.filter(
            merchant_id=merchant_id, idempotency_key=idempotency_key
        ).first()
        if existing:
            return existing, True  # Replay — return cached response

        # 2. Lock the merchant row to serialize balance checks
        # This is the critical concurrency primitive: SELECT ... FOR UPDATE
        # Any other transaction trying to lock the same merchant will BLOCK here
        # until this transaction commits or rolls back.
        merchant = Merchant.objects.select_for_update().get(id=merchant_id)

        # 3. Check available balance (while we hold the lock)
        available_balance = merchant.get_available_balance()
        if available_balance < amount_paise:
            raise ValidationError(
                f"Insufficient balance. Available: {available_balance}, "
                f"Requested: {amount_paise}"
            )

        # Validate bank account belongs to this merchant
        bank_account = BankAccount.objects.get(
            id=bank_account_id, merchant=merchant
        )

        # 4. Create Payout + HOLD entry in one atomic transaction
        try:
            payout = Payout.objects.create(
                merchant=merchant,
                bank_account=bank_account,
                amount_paise=amount_paise,
                status=Payout.PENDING,
                idempotency_key=idempotency_key,
            )
        except IntegrityError:
            # Race condition: another request with the same idempotency_key
            # committed between our check and our insert. Fetch the winner.
            payout = Payout.objects.get(
                merchant_id=merchant_id, idempotency_key=idempotency_key
            )
            return payout, True

        # Create HOLD ledger entry — locks the funds immediately
        LedgerEntry.objects.create(
            merchant=merchant,
            entry_type=LedgerEntry.HOLD,
            amount_paise=amount_paise,  # Unsigned positive value
            ref_type='payout',
            ref_id=str(payout.id),
        )

        return payout, False

    @staticmethod
    @transaction.atomic
    def transition_payout(payout_id, new_status):
        """
        Transition a payout to a new status with proper ledger adjustments.
        
        Uses select_for_update to prevent concurrent status changes.
        The Payout.save() override enforces the state machine — illegal
        transitions raise ValidationError.
        
        Ledger adjustments:
        - COMPLETED: RELEASE hold + DEBIT the funds (money leaves the system)
        - FAILED: RELEASE hold only (money returns to available balance)
        """
        # Lock the payout row to prevent concurrent transitions
        payout = Payout.objects.select_for_update().get(id=payout_id)

        old_status = payout.status
        payout.status = new_status
        payout.save()  # save() enforces state machine via can_transition_to()

        # Ledger adjustments based on new status
        if new_status == Payout.COMPLETED:
            # Release the hold
            LedgerEntry.objects.create(
                merchant=payout.merchant,
                entry_type=LedgerEntry.RELEASE,
                amount_paise=payout.amount_paise,
                ref_type='payout',
                ref_id=str(payout.id),
            )
            # Debit the funds (money actually leaves)
            LedgerEntry.objects.create(
                merchant=payout.merchant,
                entry_type=LedgerEntry.DEBIT,
                amount_paise=payout.amount_paise,
                ref_type='payout',
                ref_id=str(payout.id),
            )

        elif new_status == Payout.FAILED:
            # Release the hold (money returns to available balance)
            LedgerEntry.objects.create(
                merchant=payout.merchant,
                entry_type=LedgerEntry.RELEASE,
                amount_paise=payout.amount_paise,
                ref_type='payout',
                ref_id=str(payout.id),
            )

        return payout
