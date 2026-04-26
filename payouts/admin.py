from django.contrib import admin
from .models import Merchant, BankAccount, LedgerEntry, Payout


@admin.register(Merchant)
class MerchantAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'created_at')


@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = ('id', 'merchant', 'account_number', 'ifsc')
    list_filter = ('merchant',)


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ('id', 'merchant', 'entry_type', 'amount_paise', 'ref_type', 'ref_id', 'created_at')
    list_filter = ('entry_type', 'merchant')
    ordering = ('-created_at',)


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    list_display = ('id', 'merchant', 'amount_paise', 'status', 'idempotency_key', 'created_at')
    list_filter = ('status', 'merchant')
    ordering = ('-created_at',)
