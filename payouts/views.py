import uuid
from rest_framework import status, views, generics
from rest_framework.response import Response
from django.core.exceptions import ValidationError as DjangoValidationError

from .models import Merchant, Payout, LedgerEntry, BankAccount
from .serializers import (
    PayoutSerializer, PayoutCreateSerializer,
    MerchantSerializer, LedgerEntrySerializer,
    BankAccountSerializer,
)
from .services import PayoutService
from .tasks import process_payout_task


class MerchantListView(generics.ListAPIView):
    """GET /api/v1/merchants/ — List all merchants with balances."""
    queryset = Merchant.objects.all()
    serializer_class = MerchantSerializer


class MerchantBalanceView(views.APIView):
    """GET /api/v1/merchants/<id>/balance/ — Get balance for a merchant."""
    def get(self, request, merchant_id):
        try:
            merchant = Merchant.objects.get(id=merchant_id)
        except Merchant.DoesNotExist:
            return Response(
                {"error": "Merchant not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response({
            "merchant_id": merchant.id,
            "merchant_name": merchant.name,
            "available_balance": merchant.get_available_balance(),
            "held_balance": merchant.get_held_balance(),
        })


class BankAccountListView(generics.ListAPIView):
    """GET /api/v1/merchants/<id>/bank-accounts/ — Bank accounts for merchant."""
    serializer_class = BankAccountSerializer

    def get_queryset(self):
        return BankAccount.objects.filter(
            merchant_id=self.kwargs['merchant_id']
        )


class LedgerEntryListView(generics.ListAPIView):
    """
    GET /api/v1/merchants/<id>/ledger/ — Last 20 ledger entries for a merchant.
    """
    serializer_class = LedgerEntrySerializer

    def get_queryset(self):
        return LedgerEntry.objects.filter(
            merchant_id=self.kwargs['merchant_id']
        ).order_by('-created_at')[:20]


class PayoutListView(views.APIView):
    """
    GET  /api/v1/payouts/?merchant_id=<id> — List payouts for a merchant
    POST /api/v1/payouts/ — Create a new payout
    
    POST requires:
      Header: Idempotency-Key (UUID)
      Body: { "amount_paise": int, "bank_account_id": int, "merchant_id": int }
    """
    def get(self, request):
        merchant_id = request.query_params.get('merchant_id')
        qs = Payout.objects.all().order_by('-created_at')[:50]
        if merchant_id:
            qs = Payout.objects.filter(
                merchant_id=merchant_id
            ).order_by('-created_at')[:50]
        serializer = PayoutSerializer(qs, many=True)
        return Response(serializer.data)

    def post(self, request):
        # 1. Validate Idempotency-Key header
        idempotency_key = request.headers.get('Idempotency-Key')
        if not idempotency_key:
            return Response(
                {"error": "Idempotency-Key header is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            uuid.UUID(idempotency_key)  # Validate it's a UUID
        except ValueError:
            return Response(
                {"error": "Idempotency-Key must be a valid UUID"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 2. Validate request body
        serializer = PayoutCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        merchant_id = request.data.get('merchant_id')
        if not merchant_id:
            return Response(
                {"error": "merchant_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 3. Create payout via service layer
        try:
            payout, is_replay = PayoutService.create_payout(
                merchant_id=merchant_id,
                amount_paise=serializer.validated_data['amount_paise'],
                bank_account_id=serializer.validated_data['bank_account_id'],
                idempotency_key=idempotency_key,
            )
        except DjangoValidationError as e:
            return Response(
                {"error": str(e.message if hasattr(e, 'message') else e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except BankAccount.DoesNotExist:
            return Response(
                {"error": "Bank account not found or doesn't belong to merchant"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Merchant.DoesNotExist:
            return Response(
                {"error": "Merchant not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 4. Queue Celery task for new payouts (not replays)
        if not is_replay:
            process_payout_task.delay(payout.id)

        # 5. Return response
        response_serializer = PayoutSerializer(payout)
        http_status = status.HTTP_200_OK if is_replay else status.HTTP_201_CREATED
        return Response(response_serializer.data, status=http_status)
