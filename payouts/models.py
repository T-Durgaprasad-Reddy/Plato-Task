import uuid
from django.db import models
from django.db.models import Sum, Case, When, Value, BigIntegerField
from django.core.exceptions import ValidationError


class Merchant(models.Model):
    """A merchant who holds a balance and can request payouts."""
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    def get_available_balance(self):
        """
        Calculates available balance using database aggregation only.
        Formula: SUM(CREDIT) - SUM(DEBIT) - SUM(HOLD) + SUM(RELEASE)
        
        No Python loops. All amounts are unsigned in the DB; the entry_type
        determines the sign. This is the append-only ledger pattern — balance
        is never stored, always derived.
        
        Generated SQL equivalent:
            SELECT
                COALESCE(SUM(CASE WHEN entry_type='CREDIT' THEN amount_paise ELSE 0 END), 0)
              - COALESCE(SUM(CASE WHEN entry_type='DEBIT'  THEN amount_paise ELSE 0 END), 0)
              - COALESCE(SUM(CASE WHEN entry_type='HOLD'   THEN amount_paise ELSE 0 END), 0)
              + COALESCE(SUM(CASE WHEN entry_type='RELEASE' THEN amount_paise ELSE 0 END), 0)
            FROM payouts_ledgerentry
            WHERE merchant_id = %s;
        """
        result = self.ledger_entries.aggregate(
            balance=(
                Sum(Case(
                    When(entry_type='CREDIT', then='amount_paise'),
                    default=Value(0),
                    output_field=BigIntegerField(),
                ))
                - Sum(Case(
                    When(entry_type='DEBIT', then='amount_paise'),
                    default=Value(0),
                    output_field=BigIntegerField(),
                ))
                - Sum(Case(
                    When(entry_type='HOLD', then='amount_paise'),
                    default=Value(0),
                    output_field=BigIntegerField(),
                ))
                + Sum(Case(
                    When(entry_type='RELEASE', then='amount_paise'),
                    default=Value(0),
                    output_field=BigIntegerField(),
                ))
            )
        )
        return result['balance'] or 0

    def get_held_balance(self):
        """
        Calculates currently held funds.
        Formula: SUM(HOLD) - SUM(RELEASE)
        Represents money reserved for in-flight payouts.
        """
        result = self.ledger_entries.aggregate(
            held=(
                Sum(Case(
                    When(entry_type='HOLD', then='amount_paise'),
                    default=Value(0),
                    output_field=BigIntegerField(),
                ))
                - Sum(Case(
                    When(entry_type='RELEASE', then='amount_paise'),
                    default=Value(0),
                    output_field=BigIntegerField(),
                ))
            )
        )
        return result['held'] or 0


class BankAccount(models.Model):
    """Indian bank account linked to a merchant for payouts."""
    merchant = models.ForeignKey(
        Merchant, on_delete=models.CASCADE, related_name='bank_accounts'
    )
    account_number = models.CharField(max_length=20)
    ifsc = models.CharField(max_length=11)

    class Meta:
        unique_together = ('merchant', 'account_number', 'ifsc')

    def __str__(self):
        return f"{self.account_number} ({self.ifsc})"


class LedgerEntry(models.Model):
    """
    Append-only ledger entry. Amount is always a positive BigIntegerField
    in paise. The entry_type determines whether it adds or subtracts from
    the balance. Never update or delete rows — only insert.
    """
    CREDIT = 'CREDIT'
    DEBIT = 'DEBIT'
    HOLD = 'HOLD'
    RELEASE = 'RELEASE'

    ENTRY_TYPE_CHOICES = [
        (CREDIT, 'Credit'),
        (DEBIT, 'Debit'),
        (HOLD, 'Hold'),
        (RELEASE, 'Release'),
    ]

    merchant = models.ForeignKey(
        Merchant, on_delete=models.CASCADE, related_name='ledger_entries'
    )
    entry_type = models.CharField(max_length=10, choices=ENTRY_TYPE_CHOICES)
    amount_paise = models.BigIntegerField()  # Always positive (unsigned)
    ref_type = models.CharField(max_length=50)  # e.g. "payout", "topup"
    ref_id = models.CharField(max_length=255)  # ID of the referenced object
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['merchant', 'entry_type']),
            models.Index(fields=['ref_type', 'ref_id']),
        ]

    def __str__(self):
        return f"{self.entry_type} {self.amount_paise}p [{self.ref_type}:{self.ref_id}]"


class Payout(models.Model):
    """
    Payout request. Has a strict state machine:
        pending → processing → completed
        pending → processing → failed
    
    Terminal states (completed, failed) cannot transition to anything.
    Idempotency is enforced via unique_together on (merchant, idempotency_key).
    """
    PENDING = 'pending'
    PROCESSING = 'processing'
    COMPLETED = 'completed'
    FAILED = 'failed'

    STATUS_CHOICES = [
        (PENDING, 'Pending'),
        (PROCESSING, 'Processing'),
        (COMPLETED, 'Completed'),
        (FAILED, 'Failed'),
    ]

    # Valid transitions: {current_status: [allowed_next_statuses]}
    VALID_TRANSITIONS = {
        PENDING: [PROCESSING],
        PROCESSING: [COMPLETED, FAILED],
        COMPLETED: [],  # Terminal
        FAILED: [],      # Terminal
    }

    merchant = models.ForeignKey(
        Merchant, on_delete=models.CASCADE, related_name='payouts'
    )
    bank_account = models.ForeignKey(
        BankAccount, on_delete=models.CASCADE, related_name='payouts'
    )
    amount_paise = models.BigIntegerField()
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=PENDING
    )
    idempotency_key = models.CharField(max_length=255)
    retry_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('merchant', 'idempotency_key')
        ordering = ['-created_at']

    def __str__(self):
        return f"Payout #{self.id} — {self.status} — {self.amount_paise}p"

    def can_transition_to(self, new_status):
        """Check if transitioning to new_status is legal."""
        allowed = self.VALID_TRANSITIONS.get(self.status, [])
        return new_status in allowed

    def save(self, *args, **kwargs):
        """
        Override save() to enforce state machine transitions.
        If this is an update (pk exists), we verify the transition is legal.
        Rejects illegal transitions like failed → completed.
        """
        if self.pk:
            try:
                old = Payout.objects.get(pk=self.pk)
                if old.status != self.status:
                    if not old.can_transition_to(self.status):
                        raise ValidationError(
                            f"Illegal state transition: {old.status} → {self.status}"
                        )
            except Payout.DoesNotExist:
                pass  # New object, no transition to validate
        super().save(*args, **kwargs)
