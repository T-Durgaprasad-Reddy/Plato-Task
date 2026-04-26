from django.urls import path
from django.http import JsonResponse
from .views import (
    MerchantListView,
    MerchantBalanceView,
    BankAccountListView,
    LedgerEntryListView,
    PayoutListView,
)


def api_v1_root(request):
    return JsonResponse({
        "service": "Playto Payout Engine API",
        "version": "v1",
        "endpoints": {
            "merchants":     "/api/v1/merchants/",
            "balance":       "/api/v1/merchants/<id>/balance/",
            "bank_accounts": "/api/v1/merchants/<id>/bank-accounts/",
            "ledger":        "/api/v1/merchants/<id>/ledger/",
            "payouts":       "/api/v1/payouts/",
        }
    })


urlpatterns = [
    path('', api_v1_root, name='api-root'),
    # Merchants
    path('merchants/', MerchantListView.as_view(), name='merchant-list'),
    path('merchants/<int:merchant_id>/balance/', MerchantBalanceView.as_view(), name='merchant-balance'),
    path('merchants/<int:merchant_id>/bank-accounts/', BankAccountListView.as_view(), name='bank-account-list'),
    path('merchants/<int:merchant_id>/ledger/', LedgerEntryListView.as_view(), name='ledger-list'),
    # Payouts
    path('payouts/', PayoutListView.as_view(), name='payout-list'),
]
