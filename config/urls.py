from django.contrib import admin
from django.urls import path, include
from django.http import JsonResponse


def api_root(request):
    return JsonResponse({
        "service": "Playto Payout Engine",
        "version": "v1",
        "status": "ok",
        "endpoints": {
            "merchants":     "/api/v1/merchants/",
            "balance":       "/api/v1/merchants/<id>/balance/",
            "bank_accounts": "/api/v1/merchants/<id>/bank-accounts/",
            "ledger":        "/api/v1/merchants/<id>/ledger/",
            "payouts":       "/api/v1/payouts/",
        }
    })


urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/', include('payouts.urls')),
    path('', api_root),
]
