# EXPLAINER.md — Playto Payout Engine

## 1. The Ledger

**Why no balance column?**

Storing a `balance` field creates a single mutable value that can drift out of sync due to bugs, race conditions, or botched rollbacks. An append-only ledger is the source of truth — balance is always *derived*, never *stored*.

**The balance query — `Sum + Case/When`, database aggregation only, no Python loops:**

```python
# payouts/models.py — Merchant.get_available_balance()
from django.db.models import Sum, Case, When, Value, BigIntegerField

def get_available_balance(self):
    result = self.ledger_entries.aggregate(
        balance=(
            Sum(Case(When(entry_type='CREDIT',  then='amount_paise'), default=Value(0), output_field=BigIntegerField()))
          - Sum(Case(When(entry_type='DEBIT',   then='amount_paise'), default=Value(0), output_field=BigIntegerField()))
          - Sum(Case(When(entry_type='HOLD',    then='amount_paise'), default=Value(0), output_field=BigIntegerField()))
          + Sum(Case(When(entry_type='RELEASE', then='amount_paise'), default=Value(0), output_field=BigIntegerField()))
        )
    )
    return result['balance'] or 0
```

**Generated SQL:**
```sql
SELECT
    COALESCE(SUM(CASE WHEN entry_type='CREDIT'  THEN amount_paise ELSE 0 END), 0)
  - COALESCE(SUM(CASE WHEN entry_type='DEBIT'   THEN amount_paise ELSE 0 END), 0)
  - COALESCE(SUM(CASE WHEN entry_type='HOLD'    THEN amount_paise ELSE 0 END), 0)
  + COALESCE(SUM(CASE WHEN entry_type='RELEASE' THEN amount_paise ELSE 0 END), 0)
FROM payouts_ledgerentry
WHERE merchant_id = %s;
```

All `amount_paise` values are **unsigned `BigIntegerField`s** (always positive). The sign is determined solely by `entry_type`. No floats, no Decimal. Integer arithmetic on paise is exact.

---

## 2. The Lock

**The race condition without a lock:**

```python
# WRONG — classic Check-Then-Act race
balance = merchant.get_available_balance()   # Thread A reads 10,000
if balance >= amount_paise:                  # Thread B also reads 10,000
    LedgerEntry.objects.create(type='HOLD')  # Both pass and both HOLD → balance = -2,000
```

Between the `if` check and the `create`, another thread can read the same old balance and also pass the check.

**The fix — `SELECT FOR UPDATE` inside `transaction.atomic()`:**

```python
# payouts/services.py — PayoutService.create_payout()
from django.db import transaction

with transaction.atomic():
    # This is the critical primitive: SELECT ... FOR UPDATE
    # Postgres places an exclusive row-level lock on this merchant row.
    # Any other transaction trying to lock the same row BLOCKS here
    # until this transaction commits or rolls back.
    merchant = Merchant.objects.select_for_update().get(id=merchant_id)

    available_balance = merchant.get_available_balance()  # Read while holding lock
    if available_balance < amount_paise:
        raise ValidationError("Insufficient balance")

    # HOLD creation happens inside the same transaction, under the same lock
    Payout.objects.create(...)
    LedgerEntry.objects.create(entry_type='HOLD', ...)
```

The DB primitive is `SELECT ... FOR UPDATE` — a row-level exclusive lock in PostgreSQL. The balance check and the HOLD write are a single atomic, serialized unit. Concurrent transactions queue up and re-read the *post-commit* balance, ensuring the second request correctly sees the already-reduced balance.

---

## 3. The Idempotency

**How it works:**

The `Payout` model has a `unique_together = ('merchant', 'idempotency_key')` constraint directly in the database:

```python
# payouts/models.py
class Payout(models.Model):
    idempotency_key = models.CharField(max_length=255)
    ...
    class Meta:
        unique_together = ('merchant', 'idempotency_key')
```

On every POST, the service first does a fast lookup:

```python
# payouts/services.py — PayoutService.create_payout()
existing = Payout.objects.filter(
    merchant_id=merchant_id, idempotency_key=idempotency_key
).first()
if existing:
    return existing, True  # Replay — return exact same response, 200 OK
```

**Handling the in-flight race window:**

If two requests with the *same* key arrive simultaneously, the first one passes the lookup (no row yet) and proceeds. The second one also passes the lookup (still no row). When both try to INSERT, PostgreSQL enforces the `unique_together` constraint — one INSERT wins, the other gets an `IntegrityError`:

```python
# payouts/services.py
try:
    payout = Payout.objects.create(..., idempotency_key=idempotency_key)
except IntegrityError:
    # Race condition: another thread inserted first.
    # Fetch the winner and return it as a replay.
    payout = Payout.objects.get(merchant_id=merchant_id, idempotency_key=idempotency_key)
    return payout, True
```

**Why `get_or_create` isn't enough:** `get_or_create` has its own race window between the `get` and the `create`. We explicitly catch `IntegrityError` to handle it correctly.

The idempotency key is stored in PostgreSQL on the `Payout` row itself — not in Redis. A Redis key can expire silently, creating a window where duplicate payouts could be triggered after TTL. DB rows never expire unintentionally.

**24-hour expiry window:**

Keys are valid for exactly 24 hours from the payout's `created_at`. After that, the client must generate a fresh UUID:

```python
# payouts/services.py
IDEMPOTENCY_EXPIRY_HOURS = 24

existing = Payout.objects.filter(
    merchant_id=merchant_id, idempotency_key=idempotency_key
).first()

if existing:
    expiry_cutoff = timezone.now() - timedelta(hours=IDEMPOTENCY_EXPIRY_HOURS)
    if existing.created_at < expiry_cutoff:
        raise ValidationError(
            "Idempotency key has expired (older than 24h). "
            "Please generate a new idempotency key."
        )
    return existing, True  # Within 24h — safe replay
```

| Scenario | Response |
|----------|----------|
| First request | `201 Created` — new payout |
| Same key within 24h | `200 OK` — exact same payout replayed |
| Same key after 24h | `400 Bad Request` — "key expired, generate a new one" |

The `unique_together` DB constraint still holds, so even if a client retries the expired key, they will never accidentally create a duplicate payout — they'll get the 400 error every time.


---

## 4. The State Machine

**Allowed transitions:**
```
pending → processing → completed  (terminal)
pending → processing → failed     (terminal)
```

**Enforced at the model level via `save()` override:**

```python
# payouts/models.py
class Payout(models.Model):
    VALID_TRANSITIONS = {
        'pending':    ['processing'],
        'processing': ['completed', 'failed'],
        'completed':  [],   # Terminal
        'failed':     [],   # Terminal
    }

    def save(self, *args, **kwargs):
        if self.pk:
            old = Payout.objects.get(pk=self.pk)
            if old.status != self.status:
                if not old.can_transition_to(self.status):
                    raise ValidationError(
                        f"Illegal state transition: {old.status} -> {self.status}"
                    )
        super().save(*args, **kwargs)
```

Any code path that calls `payout.save()` — whether the Celery task, the retry worker, or a rogue API call — will hit this guard. There is no way to move a `failed` payout to `completed`. Tested explicitly:

```python
# payouts/tests.py — StateMachineTest
def test_failed_to_completed_blocked(self):
    PayoutService.transition_payout(self.payout.id, Payout.PROCESSING)
    PayoutService.transition_payout(self.payout.id, Payout.FAILED)

    with self.assertRaises(ValidationError):
        PayoutService.transition_payout(self.payout.id, Payout.COMPLETED)
```

---

## 5. The AI Audit

**Error 1 — Signed amounts instead of unsigned + Case/When**

The first AI-generated balance query stored signed values (`-5000` for a hold) and used a simple `Sum`:

```python
# AI generated — WRONG
LedgerTransaction.objects.create(amount_paise=-amount_paise, type='HOLD')  # stored as -5000
balance = self.transactions.aggregate(total=Sum('amount_paise'))['total']   # just sums everything
```

**Why it was wrong:** The spec explicitly requires `BigIntegerField` (always positive) with sign determined by `entry_type`. Signed storage makes the formula opaque and breaks the ability to use clean `CASE WHEN` aggregation per entry type. It also makes auditing harder (a `-5000` in the ledger — is that a HOLD or a DEBIT?).

**The fix:** Unsigned `amount_paise`, type-based `Sum(Case(When(...)))`:
```python
# Correct
LedgerEntry.objects.create(amount_paise=5000, entry_type='HOLD')  # unsigned
balance = Sum(Case(When(entry_type='CREDIT', then='amount_paise'), ...))
        - Sum(Case(When(entry_type='HOLD',   then='amount_paise'), ...))
        ...
```

**Error 2 — No lock on Payout row in Celery task**

The AI generated:
```python
# AI generated — WRONG
payout = Payout.objects.get(id=payout_id, status='pending')  # plain read, no lock
payout.status = 'processing'
payout.save()  # race: two workers can both read 'pending' and both proceed
```

**Why it was wrong:** Two Celery workers could pick up the same payout simultaneously. Both read `status=pending`, both transition to `processing`, both call the bank, both post DEBIT entries — double charge.

**The fix:** `select_for_update()` on the payout row:
```python
# Correct — payouts/tasks.py
with transaction.atomic():
    payout = Payout.objects.select_for_update().get(
        id=payout_id, status=Payout.PENDING
    )
    PayoutService.transition_payout(payout_id, Payout.PROCESSING)
```

The `select_for_update()` ensures only one worker processes a given payout, even if two workers grab the same task from the queue.

**Error 3 — Idempotency via a separate `IdempotencyKey` table**

The initial implementation used a separate `IdempotencyKey` model with `response_json = JSONField()`. This stores a serialized copy of the response that can drift out of sync if the Payout is later updated (e.g., status changes).

**The fix:** `idempotency_key` lives directly on `Payout` with `unique_together`, and the replay response is always freshly serialized from the live Payout row — never from stale cached JSON.
