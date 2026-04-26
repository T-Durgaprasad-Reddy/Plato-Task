from rest_framework import serializers
from .models import Merchant, BankAccount, LedgerEntry, Payout


class BankAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = BankAccount
        fields = ['id', 'merchant', 'account_number', 'ifsc']
        read_only_fields = ['merchant']


class LedgerEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = LedgerEntry
        fields = [
            'id', 'merchant', 'entry_type', 'amount_paise',
            'ref_type', 'ref_id', 'created_at',
        ]


class MerchantSerializer(serializers.ModelSerializer):
    available_balance = serializers.SerializerMethodField()
    held_balance = serializers.SerializerMethodField()

    class Meta:
        model = Merchant
        fields = ['id', 'name', 'available_balance', 'held_balance', 'created_at']

    def get_available_balance(self, obj):
        return obj.get_available_balance()

    def get_held_balance(self, obj):
        return obj.get_held_balance()


class PayoutSerializer(serializers.ModelSerializer):
    bank_account_display = serializers.SerializerMethodField()

    class Meta:
        model = Payout
        fields = [
            'id', 'merchant', 'bank_account', 'bank_account_display',
            'amount_paise', 'status', 'idempotency_key',
            'retry_count', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'status', 'idempotency_key', 'retry_count',
            'created_at', 'updated_at',
        ]

    def get_bank_account_display(self, obj):
        return f"{obj.bank_account.account_number} ({obj.bank_account.ifsc})"


class PayoutCreateSerializer(serializers.Serializer):
    """Validates the POST body for creating a payout."""
    amount_paise = serializers.IntegerField(min_value=1)
    bank_account_id = serializers.IntegerField()


class BalanceSerializer(serializers.Serializer):
    available_balance = serializers.IntegerField()
    held_balance = serializers.IntegerField()
