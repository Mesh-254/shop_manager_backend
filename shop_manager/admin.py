from django.contrib import admin
from .models import *



admin.site.register(SubscriptionPlan)
admin.site.register(Shop)
admin.site.register(Category)
admin.site.register(Supplier)
admin.site.register(Product)
admin.site.register(Stock)
admin.site.register(Purchase)
admin.site.register(PurchaseItem)
admin.site.register(Sale)
admin.site.register(SaleItem)
admin.site.register(Expense)
admin.site.register(OfflineSyncLog)
