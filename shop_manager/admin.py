from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.inlines.admin import StackedInline
from .models import (
    SubscriptionPlan, Shop, Category, Supplier, Product,
    Stock, Purchase, PurchaseItem, Sale, SaleItem,
    Expense, OfflineSyncLog
)
from accounts.models import UserRole


class ShopScopedAdmin(ModelAdmin):
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.role == UserRole.SHOP_ADMIN:
            return qs.filter(shop=request.user.shop)
        return qs

    def has_view_permission(self, request, obj=None):
        if not request.user.is_staff:
            return False
        if request.user.role == UserRole.SUPER_ADMIN:
            return True
        if request.user.role == UserRole.SHOP_ADMIN:
            return True  # Can view changelist (queryset will scope to their shop)
        return False

    def has_change_permission(self, request, obj=None):
        if request.user.role == UserRole.SUPER_ADMIN:
            return True
        if request.user.role == UserRole.SHOP_ADMIN:
            # Can change objects in their shop (obj check for detail view)
            return obj is None or (hasattr(obj, 'shop') and obj.shop == request.user.shop)
        return False

    def has_add_permission(self, request):
        if request.user.role == UserRole.SUPER_ADMIN:
            return True
        if request.user.role == UserRole.SHOP_ADMIN:
            return True  # Allow adding new items (will be scoped on save)
        return False

    def has_delete_permission(self, request, obj=None):
        if request.user.role == UserRole.SUPER_ADMIN:
            return True
        if request.user.role == UserRole.SHOP_ADMIN:
            return obj is None or (hasattr(obj, 'shop') and obj.shop == request.user.shop)
        return False

    # Optional: Make certain fields read-only for SHOP_ADMIN
    def get_readonly_fields(self, request, obj=None):
        readonly = super().get_readonly_fields(request, obj)
        if request.user.role == UserRole.SHOP_ADMIN:
            # Example: Prevent changing shop on existing objects
            if obj and hasattr(obj, 'shop'):
                readonly += ('shop',)
        return readonly


# Inlines (unchanged)
class PurchaseItemInline(StackedInline):
    model = PurchaseItem
    extra = 1
    fields = ('product', 'quantity', 'unit_cost_price', 'total_cost')
    readonly_fields = ('total_cost',)

class SaleItemInline(StackedInline):
    model = SaleItem
    extra = 1
    fields = ('product', 'quantity', 'unit_selling_price', 'profit')
    readonly_fields = ('profit',)


# ModelAdmins (fixed)
@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(ModelAdmin):
    list_display = ('name', 'price_per_month', 'user_limit', 'product_limit', 'shop_limit')
    search_fields = ('name',)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.role != 'SuperAdmin':
            return qs.none()
        return qs


@admin.register(Shop)
class ShopAdmin(ShopScopedAdmin):
    list_display = ('name', 'owner', 'currency_code', 'is_active', 'created_at')
    search_fields = ('name', 'owner__email')
    list_filter = ('is_active', 'currency_code', 'country')
    fieldsets = (
        (None, {"fields": ("owner", "name", "description", "country", "currency_code")}),
        ("Advanced", {"fields": ("logo", "subscription_plan", "is_active")}),
    )


@admin.register(Category)
class CategoryAdmin(ShopScopedAdmin):
    list_display = ('name', 'shop', 'description')
    search_fields = ('name', 'shop__name')
    list_filter = ('shop',)


@admin.register(Supplier)
class SupplierAdmin(ShopScopedAdmin):
    # Removed 'contact_email' – it doesn't exist on Supplier model
    list_display = ('name', 'phone', 'shop')  # Keep only existing fields
    search_fields = ('name',)


@admin.register(Product)
class ProductAdmin(ShopScopedAdmin):
    # Removed 'sku' (doesn't exist) and 'brand' (doesn't exist in list_filter)
    list_display = ('name', 'category', 'cost_price', 'selling_price', 'shop')
    search_fields = ('name',)  # Removed 'sku', 'brand'
    list_filter = ('category', 'shop')  # Removed 'brand'


@admin.register(Stock)
class StockAdmin(ShopScopedAdmin):
    list_display = ('product', 'quantity', 'get_shop')  # Replaced direct 'shop' with method
    search_fields = ('product__name', 'product__sku')
    list_filter = ()  # Removed invalid 'shop' filter – add related if needed

    readonly_fields = ('quantity',)

    def get_shop(self, obj):
        # Assuming Stock → Product → Shop relationship
        return obj.product.shop if obj.product and obj.product.shop else '-'
    get_shop.short_description = 'Shop'
    get_shop.admin_order_field = 'product__shop'  # Enables sorting


@admin.register(Purchase)
class PurchaseAdmin(ShopScopedAdmin):
    list_display = ('id', 'shop', 'supplier', 'total_amount', 'payment_status', 'purchase_date')
    search_fields = ('id', 'supplier__name')
    list_filter = ('payment_status', 'shop', 'purchase_date')
    inlines = [PurchaseItemInline]
    readonly_fields = ('total_amount', 'created_by')


@admin.register(Sale)
class SaleAdmin(ShopScopedAdmin):
    list_display = ('id', 'shop', 'total_amount', 'payment_status', 'sale_date', 'sold_by')
    search_fields = ('id',)
    list_filter = ('payment_status', 'shop', 'sale_date')
    inlines = [SaleItemInline]


@admin.register(Expense)
class ExpenseAdmin(ShopScopedAdmin):
    list_display = ('title', 'amount', 'expense_type', 'date', 'shop')
    list_filter = ('expense_type', 'shop', 'date')


@admin.register(OfflineSyncLog)
class OfflineSyncLogAdmin(ModelAdmin):
    list_display = ('shop', 'sync_status', 'sync_date', 'error_message')
    list_filter = ('sync_status', 'shop')
    readonly_fields = ('sync_date', 'error_message')