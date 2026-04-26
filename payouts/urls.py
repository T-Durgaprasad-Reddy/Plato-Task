from django.urls import path
from .views import (
    MerchantListView,
    MerchantBalanceView,
    BankAccountListView,
    LedgerEntryListView,
    PayoutListView,
)

urlpatterns = [
    # Merchants
    path('merchants/', MerchantListView.as_view(), name='merchant-list'),
    path('merchants/<int:merchant_id>/balance/', MerchantBalanceView.as_view(), name='merchant-balance'),
    path('merchants/<int:merchant_id>/bank-accounts/', BankAccountListView.as_view(), name='bank-account-list'),
    path('merchants/<int:merchant_id>/ledger/', LedgerEntryListView.as_view(), name='ledger-list'),
    # Payouts
    path('payouts/', PayoutListView.as_view(), name='payout-list'),
]
