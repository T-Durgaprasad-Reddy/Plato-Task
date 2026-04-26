import random
import datetime
from celery import shared_task
from django.db import transaction
from django.utils import timezone
from .models import Payout
from .services import PayoutService


@shared_task(bind=True, max_retries=3)
def process_payout_task(self, payout_id):
    """
    Background task to process a payout. Simulates a bank API call.
    
    Flow:
    1. Lock the payout row with SELECT FOR UPDATE, verify status=pending
    2. Transition to processing
    3. Simulate bank outcome: 70% success, 20% fail, 10% hang
    4. On success: completed + RELEASE + DEBIT
    5. On fail: failed + RELEASE
    6. On hang: do nothing — retry_stuck_payouts_task will pick it up
    """
    try:
        with transaction.atomic():
            # Lock the payout row — prevents duplicate processing
            payout = Payout.objects.select_for_update().get(
                id=payout_id, status=Payout.PENDING
            )
            # Transition to PROCESSING
            PayoutService.transition_payout(payout_id, Payout.PROCESSING)

        # Simulate bank API call
        # 70% success, 20% fail, 10% hang (do nothing)
        roll = random.random()

        if roll < 0.70:
            # Bank says: success
            PayoutService.transition_payout(payout_id, Payout.COMPLETED)
        elif roll < 0.90:
            # Bank says: failure
            PayoutService.transition_payout(payout_id, Payout.FAILED)
        else:
            # Bank hangs — do nothing
            # retry_stuck_payouts_task will catch this after 30s
            pass

    except Payout.DoesNotExist:
        # Payout already processed or doesn't exist — skip silently
        return
    except Exception as exc:
        # Retry with exponential backoff: 2^0=1s, 2^1=2s, 2^2=4s
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)


@shared_task
def retry_stuck_payouts_task():
    """
    Periodic task (runs every 15s via Celery Beat) to handle payouts
    stuck in 'processing' state for > 30 seconds.
    
    - If retry_count < 3: increment retry_count, re-queue for processing
    - If retry_count >= 3: mark as failed and RELEASE held funds
    
    Uses exponential backoff indirectly: each retry resets updated_at,
    so the next retry won't trigger until another 30s passes.
    """
    cutoff = timezone.now() - datetime.timedelta(seconds=30)

    # Find payouts stuck in processing with retries remaining
    stuck_payouts = Payout.objects.filter(
        status=Payout.PROCESSING,
        updated_at__lt=cutoff,
        retry_count__lt=3,
    )

    for payout in stuck_payouts:
        payout.retry_count += 1
        payout.updated_at = timezone.now()  # Reset the clock
        # Use update() to avoid triggering state machine validation
        Payout.objects.filter(id=payout.id).update(
            retry_count=payout.retry_count,
            updated_at=timezone.now(),
        )
        # Re-queue for processing — the task will re-roll the dice
        # We reset status to pending so the task can pick it up
        with transaction.atomic():
            p = Payout.objects.select_for_update().get(id=payout.id)
            # Directly update status without save() validation
            # since processing → pending is a retry, not a normal transition
            Payout.objects.filter(id=payout.id).update(status=Payout.PENDING)
        process_payout_task.delay(payout.id)

    # Mark as failed if exceeded max retries
    exhausted_payouts = Payout.objects.filter(
        status=Payout.PROCESSING,
        updated_at__lt=cutoff,
        retry_count__gte=3,
    )
    for payout in exhausted_payouts:
        PayoutService.transition_payout(payout.id, Payout.FAILED)
